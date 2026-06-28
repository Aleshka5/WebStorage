import mimetypes
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.file_service import FileService
from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.entities.user import User
from app.domain.exceptions import QuotaExceededError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.dependencies.files import get_file_service
from app.presentation.dependencies.shared import get_shared_file_service
from app.presentation.routers.quota_router import _resolve_limit_bytes
from app.presentation.utils.content_disposition import build_attachment_content_disposition
from app.presentation.schemas.files import (
    FileNodeResponse,
    FileRecordResponse,
    MkdirRequest,
    RenameRequest,
)
from config import Settings, get_settings

router = APIRouter(prefix="/api/files", tags=["files"])

DEFAULT_MIME_TYPE = "application/octet-stream"
SEARCH_NOT_IMPLEMENTED_MESSAGE = "File search is not implemented yet"


def _normalize_api_path(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _build_api_path(parent_path: str, name: str) -> str:
    parent = parent_path.strip().replace("\\", "/").rstrip("/")
    if not parent or parent == "/":
        return f"/{name}"
    return f"{parent}/{name}"


def _file_record_response(record: FileRecord) -> FileRecordResponse:
    return FileRecordResponse(
        id=record.id,
        name=record.original_name,
        size=record.size_bytes,
        section=record.section,
        status=record.status,
        created_at=record.created_at,
    )


async def _ensure_upload_quota(
    user_id: UUID,
    role: Role,
    size: int,
    quota_repo: QuotaRepository,
    settings: Settings,
) -> None:
    usage = await quota_repo.get_by_user_id(user_id)
    limit_bytes = _resolve_limit_bytes(role, settings)
    if usage.total_bytes + size > limit_bytes:
        available = max(0, limit_bytes - usage.total_bytes)
        logger.warning(
            "Quota exceeded for user {}: used={}, limit={}, requested={}",
            user_id,
            usage.total_bytes,
            limit_bytes,
            size,
        )
        raise QuotaExceededError(
            f"Upload size {size} bytes exceeds available quota ({available} bytes remaining)",
            available_bytes=available,
        )


async def _iter_upload_chunks(upload_file: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload_file.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@router.get("", response_model=list[FileNodeResponse])
async def list_files(
    path: str = Query(default="/"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
) -> list[FileNodeResponse]:
    normalized = _normalize_api_path(path)
    logger.info("Listing files for user {} at path {}", current_user.id, path)

    try:
        nodes = await file_service.list_directory(current_user.id, normalized)
    except Exception:
        raise

    api_base = path.strip().replace("\\", "/").rstrip("/") or "/"
    return [
        FileNodeResponse.from_node(
            node,
            _build_api_path(api_base, node.name),
        )
        for node in nodes
    ]


@router.post("/upload", response_model=FileRecordResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    path: str = Query(default="/"),
    uploaded_file: UploadFile = File(..., alias="file"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    session: AsyncSession = Depends(get_async_session),
) -> FileRecordResponse:
    if not uploaded_file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                "message": "Uploaded file must have a filename",
            },
        )

    filename = PurePosixPath(uploaded_file.filename).name
    if not filename or filename in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.PATH_TRAVERSAL_DETECTED,
                "message": "Invalid filename",
            },
        )

    normalized = _normalize_api_path(path)
    file_size = uploaded_file.size if uploaded_file.size is not None else 0
    logger.info(
        "Upload requested for user {} at path {} filename {} (size={})",
        current_user.id,
        path,
        filename,
        file_size,
    )

    try:
        await _ensure_upload_quota(
            current_user.id,
            current_user.role,
            file_size,
            quota_repo,
            get_settings(),
        )
        record = await file_service.upload_file(
            user_id=current_user.id,
            path=normalized,
            filename=filename,
            data=_iter_upload_chunks(uploaded_file),
            size=file_size,
            section=FileSection.FILES,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Upload completed for user {} file {}", current_user.id, record.id)
    return _file_record_response(record)


@router.get("/download")
async def download_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
) -> StreamingResponse:
    normalized = _normalize_api_path(path)
    filename = PurePosixPath(normalized).name
    logger.info("Download requested for user {} path {}", current_user.id, path)

    try:
        stream = file_service.read_by_path(current_user.id, normalized)
    except Exception:
        raise

    media_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": build_attachment_content_disposition(filename)},
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    logger.info("Delete requested for user {} path {}", current_user.id, path)

    try:
        await file_service.delete_by_path(
            current_user.id,
            path,
            actor_role=current_user.role,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Deleted path {} for user {}", path, current_user.id)


@router.post("/mkdir", status_code=status.HTTP_201_CREATED)
async def create_directory(
    body: MkdirRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    normalized = _normalize_api_path(body.path)
    logger.info(
        "Mkdir requested for user {} at path {} name {}",
        current_user.id,
        body.path,
        body.name,
    )

    try:
        await file_service.create_directory(current_user.id, normalized, body.name)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    created_path = _build_api_path(body.path, body.name)
    logger.info("Directory created for user {} at {}", current_user.id, created_path)
    return {"path": created_path, "name": body.name}


@router.patch(
    "/rename",
    response_model=FileRecordResponse,
    responses={204: {"description": "Directory renamed"}},
)
async def rename_file(
    body: RenameRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> FileRecordResponse | Response:
    logger.info(
        "Rename requested for user {} path {} to {}",
        current_user.id,
        body.path,
        body.new_name,
    )

    try:
        record = await file_service.rename_by_path(
            current_user.id,
            body.path,
            body.new_name,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    if record is None:
        logger.info("Renamed directory {} for user {}", body.path, current_user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    logger.info("Renamed file {} for user {}", record.id, current_user.id)
    return _file_record_response(record)


@router.get("/search")
async def search_files(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
) -> None:
    logger.info("File search requested by user {} with query {}", current_user.id, q)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": SEARCH_NOT_IMPLEMENTED_MESSAGE,
        },
    )


shared_router = APIRouter(prefix="/api/shared", tags=["shared"])


@shared_router.get("", response_model=list[FileNodeResponse])
async def list_shared_files(
    path: str = Query(default="/"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
) -> list[FileNodeResponse]:
    normalized = _normalize_api_path(path)
    logger.info("Listing shared files for user {} at path {}", current_user.id, path)

    try:
        nodes = await file_service.list_directory(current_user.id, normalized)
    except Exception:
        raise

    api_base = path.strip().replace("\\", "/").rstrip("/") or "/"
    return [
        FileNodeResponse.from_node(
            node,
            _build_api_path(api_base, node.name),
        )
        for node in nodes
    ]


@shared_router.post("/upload", response_model=FileRecordResponse, status_code=status.HTTP_201_CREATED)
async def upload_shared_file(
    path: str = Query(default="/"),
    uploaded_file: UploadFile = File(..., alias="file"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    session: AsyncSession = Depends(get_async_session),
) -> FileRecordResponse:
    if not uploaded_file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                "message": "Uploaded file must have a filename",
            },
        )

    filename = PurePosixPath(uploaded_file.filename).name
    if not filename or filename in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.PATH_TRAVERSAL_DETECTED,
                "message": "Invalid filename",
            },
        )

    normalized = _normalize_api_path(path)
    file_size = uploaded_file.size if uploaded_file.size is not None else 0
    logger.info(
        "Shared upload requested for user {} at path {} filename {} (size={})",
        current_user.id,
        path,
        filename,
        file_size,
    )

    try:
        await _ensure_upload_quota(
            current_user.id,
            current_user.role,
            file_size,
            quota_repo,
            get_settings(),
        )
        record = await file_service.upload_file(
            user_id=current_user.id,
            path=normalized,
            filename=filename,
            data=_iter_upload_chunks(uploaded_file),
            size=file_size,
            section=FileSection.SHARED,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Shared upload completed for user {} file {}", current_user.id, record.id)
    return _file_record_response(record)


@shared_router.get("/download")
async def download_shared_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
) -> StreamingResponse:
    normalized = _normalize_api_path(path)
    filename = PurePosixPath(normalized).name
    logger.info("Shared download requested for user {} path {}", current_user.id, path)

    try:
        stream = file_service.read_by_path(current_user.id, normalized)
    except Exception:
        raise

    media_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": build_attachment_content_disposition(filename)},
    )


@shared_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shared_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    logger.info("Shared delete requested for user {} path {}", current_user.id, path)

    try:
        await file_service.delete_by_path(
            current_user.id,
            path,
            actor_role=current_user.role,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Deleted shared path {} for user {}", path, current_user.id)


@shared_router.post("/mkdir", status_code=status.HTTP_201_CREATED)
async def create_shared_directory(
    body: MkdirRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    normalized = _normalize_api_path(body.path)
    logger.info(
        "Shared mkdir requested for user {} at path {} name {}",
        current_user.id,
        body.path,
        body.name,
    )

    try:
        await file_service.create_directory(current_user.id, normalized, body.name)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    created_path = _build_api_path(body.path, body.name)
    logger.info("Shared directory created for user {} at {}", current_user.id, created_path)
    return {"path": created_path, "name": body.name}


@shared_router.patch(
    "/rename",
    response_model=FileRecordResponse,
    responses={204: {"description": "Directory renamed"}},
)
async def rename_shared_file(
    body: RenameRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_shared_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> FileRecordResponse | Response:
    logger.info(
        "Shared rename requested for user {} path {} to {}",
        current_user.id,
        body.path,
        body.new_name,
    )

    try:
        record = await file_service.rename_by_path(
            current_user.id,
            body.path,
            body.new_name,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    if record is None:
        logger.info("Renamed shared directory {} for user {}", body.path, current_user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    logger.info("Renamed shared file {} for user {}", record.id, current_user.id)
    return _file_record_response(record)


@shared_router.get("/search")
async def search_shared_files(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
) -> None:
    logger.info("Shared file search requested by user {} with query {}", current_user.id, q)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": SEARCH_NOT_IMPLEMENTED_MESSAGE,
        },
    )
