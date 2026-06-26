import mimetypes
from collections.abc import AsyncIterator
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.file_service import FileService
from app.application.private_service import PrivateService
from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.entities.user import User
from app.domain.exceptions import (
    AccessDeniedError,
    FileNotFoundError,
    PathTraversalError,
    QuotaExceededError,
    StorageUnavailableError,
)
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.session import get_async_session
from app.infrastructure.session_store import (
    UNLOCK_RATE_LIMIT_MAX_ATTEMPTS,
    UNLOCK_RATE_LIMIT_TTL_SECONDS,
    SessionStore,
    get_session_store,
)
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.private import (
    _get_session_id,
    get_private_file_service,
    get_private_service,
)
from app.presentation.schemas.files import (
    FileNodeResponse,
    FileRecordResponse,
    MkdirRequest,
    RenameRequest,
)
from app.presentation.schemas.private import (
    PrivateQuotaResponse,
    PrivateSessionResponse,
    UnlockRequest,
    UnlockResponse,
)
from app.presentation.utils.content_disposition import build_attachment_content_disposition

router = APIRouter(prefix="/api/private", tags=["private"])

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


def _raise_http_for_domain_error(exc: Exception) -> None:
    if isinstance(exc, PathTraversalError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.PATH_TRAVERSAL_DETECTED,
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, QuotaExceededError):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": ErrorCode.QUOTA_EXCEEDED,
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": ErrorCode.FILE_NOT_FOUND,
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, AccessDeniedError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, StorageUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": ErrorCode.DISK_UNAVAILABLE,
                "message": str(exc),
            },
        ) from exc
    raise exc


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


async def _ensure_private_upload_quota(
    user_id,
    size: int,
    private_service: PrivateService,
) -> None:
    quota = await private_service.get_quota(user_id)
    private_bytes = quota["private_bytes"]
    private_limit = quota["private_limit_bytes"]
    if private_limit > 0 and private_bytes + size > private_limit:
        available = max(0, private_limit - private_bytes)
        logger.warning(
            "Private quota exceeded for user {}: used={}, limit={}, requested={}",
            user_id,
            private_bytes,
            private_limit,
            size,
        )
        raise QuotaExceededError(
            f"Upload size {size} bytes exceeds available private quota ({available} bytes remaining)"
        )


async def _iter_upload_chunks(upload_file: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload_file.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@router.post("/unlock", response_model=UnlockResponse)
async def unlock_private_section(
    body: UnlockRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    private_service: PrivateService = Depends(get_private_service),
    session_store: SessionStore = Depends(get_session_store),
) -> UnlockResponse:
    session_id = _get_session_id(request)
    client_ip = _client_ip(request)

    attempts = await session_store.get_unlock_attempts(str(current_user.id), client_ip)
    if attempts >= UNLOCK_RATE_LIMIT_MAX_ATTEMPTS:
        logger.warning(
            "Unlock rate limit exceeded for user {} from IP {}",
            current_user.id,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": ErrorCode.TOO_MANY_ATTEMPTS,
                "message": "Too many unlock attempts",
                "retry_after": UNLOCK_RATE_LIMIT_TTL_SECONDS,
            },
        )

    success = await private_service.unlock(current_user.id, session_id, body.passphrase)
    if success:
        await session_store.reset_unlock_attempts(str(current_user.id), client_ip)
        logger.info("Private section unlocked for user {}", current_user.id)
        return UnlockResponse(success=True)

    await session_store.increment_unlock_attempts(str(current_user.id), client_ip)
    updated_attempts = await session_store.get_unlock_attempts(str(current_user.id), client_ip)
    if updated_attempts >= UNLOCK_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": ErrorCode.TOO_MANY_ATTEMPTS,
                "message": "Too many unlock attempts",
                "retry_after": UNLOCK_RATE_LIMIT_TTL_SECONDS,
            },
        )

    logger.warning("Private unlock failed for user {}", current_user.id)
    return UnlockResponse(success=False)


@router.post("/lock", status_code=status.HTTP_204_NO_CONTENT)
async def lock_private_section(
    request: Request,
    private_service: PrivateService = Depends(get_private_service),
) -> None:
    session_id = _get_session_id(request)
    await private_service.lock(session_id)
    logger.info("Private section locked for session")


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_private_storage(
    request: Request,
    current_user: User = Depends(get_current_user),
    private_service: PrivateService = Depends(get_private_service),
    session_store: SessionStore = Depends(get_session_store),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    session_id = _get_session_id(request)
    client_ip = _client_ip(request)

    attempts = await session_store.get_unlock_attempts(str(current_user.id), client_ip)
    if attempts < UNLOCK_RATE_LIMIT_MAX_ATTEMPTS:
        logger.warning(
            "Private reset denied for user {} from IP {}: rate limit not reached",
            current_user.id,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": "Private storage reset is available only after too many unlock attempts",
            },
        )

    try:
        await private_service.reset_storage(current_user.id, session_id)
        await session_store.reset_unlock_attempts(str(current_user.id), client_ip)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_domain_error(exc)

    logger.info("Private storage reset for user {}", current_user.id)


@router.get("/quota", response_model=PrivateQuotaResponse)
async def get_private_quota(
    current_user: User = Depends(get_current_user),
    private_service: PrivateService = Depends(get_private_service),
    session: AsyncSession = Depends(get_async_session),
) -> PrivateQuotaResponse:
    quota = await private_service.get_quota(current_user.id)
    await session.commit()
    logger.info("Private quota fetched for user {}", current_user.id)
    return PrivateQuotaResponse(**quota)


@router.get("/session", response_model=PrivateSessionResponse)
async def get_private_session(
    request: Request,
    private_service: PrivateService = Depends(get_private_service),
) -> PrivateSessionResponse:
    session_id = _get_session_id(request)
    status_payload = await private_service.get_session_status(session_id)
    return PrivateSessionResponse(**status_payload)


@router.get("", response_model=list[FileNodeResponse])
async def list_private_files(
    path: str = Query(default="/"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
) -> list[FileNodeResponse]:
    normalized = _normalize_api_path(path)
    logger.info("Listing private files for user {} at path {}", current_user.id, path)

    try:
        nodes = await file_service.list_directory(current_user.id, normalized)
    except Exception as exc:
        _raise_http_for_domain_error(exc)

    api_base = path.strip().replace("\\", "/").rstrip("/") or "/"
    return [
        FileNodeResponse.from_node(
            node,
            _build_api_path(api_base, node.name),
        )
        for node in nodes
    ]


@router.post("/upload", response_model=FileRecordResponse, status_code=status.HTTP_201_CREATED)
async def upload_private_file(
    path: str = Query(default="/"),
    uploaded_file: UploadFile = File(..., alias="file"),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
    private_service: PrivateService = Depends(get_private_service),
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
        "Private upload requested for user {} at path {} filename {} (size={})",
        current_user.id,
        path,
        filename,
        file_size,
    )

    try:
        await _ensure_private_upload_quota(
            current_user.id,
            file_size,
            private_service,
        )
        record = await file_service.upload_file(
            user_id=current_user.id,
            path=normalized,
            filename=filename,
            data=_iter_upload_chunks(uploaded_file),
            size=file_size,
            section=FileSection.PRIVATE,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_domain_error(exc)

    logger.info("Private upload completed for user {} file {}", current_user.id, record.id)
    return _file_record_response(record)


@router.get("/download")
async def download_private_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
) -> StreamingResponse:
    normalized = _normalize_api_path(path)
    filename = PurePosixPath(normalized).name
    logger.info("Private download requested for user {} path {}", current_user.id, path)

    try:
        stream = file_service.read_by_path(current_user.id, normalized)
    except Exception as exc:
        _raise_http_for_domain_error(exc)

    media_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": build_attachment_content_disposition(filename)},
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_private_file(
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    logger.info("Private delete requested for user {} path {}", current_user.id, path)

    try:
        await file_service.delete_by_path(current_user.id, path)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_domain_error(exc)

    logger.info("Deleted private path {} for user {}", path, current_user.id)


@router.post("/mkdir", status_code=status.HTTP_201_CREATED)
async def create_private_directory(
    body: MkdirRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    normalized = _normalize_api_path(body.path)
    logger.info(
        "Private mkdir requested for user {} at path {} name {}",
        current_user.id,
        body.path,
        body.name,
    )

    try:
        await file_service.create_directory(current_user.id, normalized, body.name)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_domain_error(exc)

    created_path = _build_api_path(body.path, body.name)
    logger.info("Private directory created for user {} at {}", current_user.id, created_path)
    return {"path": created_path, "name": body.name}


@router.patch(
    "/rename",
    response_model=FileRecordResponse,
    responses={204: {"description": "Directory renamed"}},
)
async def rename_private_file(
    body: RenameRequest,
    current_user: User = Depends(get_current_user),
    file_service: FileService = Depends(get_private_file_service),
    session: AsyncSession = Depends(get_async_session),
) -> FileRecordResponse | Response:
    logger.info(
        "Private rename requested for user {} path {} to {}",
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
    except Exception as exc:
        await session.rollback()
        _raise_http_for_domain_error(exc)

    if record is None:
        logger.info("Renamed private directory {} for user {}", body.path, current_user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    logger.info("Renamed private file {} for user {}", record.id, current_user.id)
    return _file_record_response(record)


@router.get("/search")
async def search_private_files(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
) -> None:
    logger.info("Private file search requested by user {} with query {}", current_user.id, q)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": SEARCH_NOT_IMPLEMENTED_MESSAGE,
        },
    )
