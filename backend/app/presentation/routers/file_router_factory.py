"""Build the standard nine file endpoints for a section-scoped FileService.

``/api/files`` and ``/api/shared`` keep their hand-written handlers; new
sections (``/api/resumes/files``) are mounted through this factory so the
contract is implemented once.
"""

import mimetypes
from collections.abc import Callable
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.entities.user import User
from app.domain.exceptions import QuotaExceededError
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.routers.file_router import (
    DEFAULT_MIME_TYPE,
    SEARCH_NOT_IMPLEMENTED_MESSAGE,
    _available_upload_bytes,
    _build_api_path,
    _ensure_upload_quota,
    _file_record_response,
    _iter_upload_chunks,
    _normalize_api_path,
    _spooled_upload_file,
    _validate_zip_archive,
)
from app.presentation.schemas.files import (
    FileNodeResponse,
    FileRecordResponse,
    MkdirRequest,
    RenameRequest,
    ZipUploadResponse,
)
from app.presentation.utils.content_disposition import build_attachment_content_disposition


def _validated_filename(upload_file: UploadFile) -> str:
    if not upload_file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                "message": "Uploaded file must have a filename",
            },
        )

    filename = PurePosixPath(upload_file.filename).name
    if not filename or filename in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.PATH_TRAVERSAL_DETECTED,
                "message": "Invalid filename",
            },
        )
    return filename


def build_file_router(
    *,
    prefix: str,
    tags: list[str],
    file_service_dependency: Callable[..., FileService],
    section: FileSection,
    user_dependency: Callable[..., User] = get_current_user,
    log_label: str = "File",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)

    @router.get("", response_model=list[FileNodeResponse])
    async def list_files(
        path: str = Query(default="/"),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
    ) -> list[FileNodeResponse]:
        normalized = _normalize_api_path(path)
        logger.info("{} listing for user {} at path {}", log_label, current_user.id, path)

        nodes = await file_service.list_directory(current_user.id, normalized)

        api_base = path.strip().replace("\\", "/").rstrip("/") or "/"
        return [
            FileNodeResponse.from_node(node, _build_api_path(api_base, node.name))
            for node in nodes
        ]

    @router.post(
        "/upload",
        response_model=FileRecordResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_file(
        path: str = Query(default="/"),
        uploaded_file: UploadFile = File(..., alias="file"),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
        quota_repo: QuotaRepository = Depends(get_quota_repository),
        session: AsyncSession = Depends(get_async_session),
    ) -> FileRecordResponse:
        filename = _validated_filename(uploaded_file)
        normalized = _normalize_api_path(path)
        file_size = uploaded_file.size if uploaded_file.size is not None else 0
        logger.info(
            "{} upload requested for user {} at path {} filename {} (size={})",
            log_label,
            current_user.id,
            path,
            filename,
            file_size,
        )

        try:
            await _ensure_upload_quota(current_user.id, file_size, quota_repo)
            record = await file_service.upload_file(
                user_id=current_user.id,
                path=normalized,
                filename=filename,
                data=_iter_upload_chunks(uploaded_file),
                size=file_size,
                section=section,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        logger.info(
            "{} upload completed for user {} file {}",
            log_label,
            current_user.id,
            record.id,
        )
        return _file_record_response(record)

    @router.post(
        "/upload-zip",
        response_model=ZipUploadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_zip_folder(
        path: str = Query(default="/"),
        zip_file: UploadFile = File(..., alias="file"),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
        quota_repo: QuotaRepository = Depends(get_quota_repository),
        session: AsyncSession = Depends(get_async_session),
    ) -> ZipUploadResponse:
        filename = _validated_filename(zip_file)
        if not filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                    "message": "Only .zip files are supported for folder upload",
                },
            )

        normalized = _normalize_api_path(path)
        async with _spooled_upload_file(zip_file) as spool:
            total_uncompressed = _validate_zip_archive(spool)
            logger.info(
                "{} ZIP folder upload requested for user {} at path {} "
                "(zip_size={}, uncompressed={})",
                log_label,
                current_user.id,
                path,
                zip_file.size,
                total_uncompressed,
            )

            try:
                quota = await _ensure_upload_quota(
                    current_user.id,
                    total_uncompressed,
                    quota_repo,
                )
                result = await file_service.upload_zip_folder(
                    user_id=current_user.id,
                    path=normalized,
                    zip_filename=filename,
                    zip_source=spool,
                    total_uncompressed_bytes=total_uncompressed,
                    available_bytes=_available_upload_bytes(quota, total_uncompressed),
                )
                await session.commit()
            except QuotaExceededError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={
                        "error_code": ErrorCode.QUOTA_EXCEEDED,
                        "message": str(exc),
                        "available_bytes": exc.available_bytes,
                    },
                )
            except ValueError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                        "message": str(exc),
                    },
                )
            except Exception:
                await session.rollback()
                raise

        logger.info(
            "{} ZIP folder upload completed for user {}: {} files, {} dirs",
            log_label,
            current_user.id,
            result["files"],
            result["dirs"],
        )
        return ZipUploadResponse(
            files=result["files"],
            dirs=result["dirs"],
            total_bytes=result["total_bytes"],
        )

    @router.get("/download")
    async def download_file(
        path: str = Query(...),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
    ) -> StreamingResponse:
        normalized = _normalize_api_path(path)
        filename = PurePosixPath(normalized).name
        logger.info(
            "{} download requested for user {} path {}",
            log_label,
            current_user.id,
            path,
        )

        stream = file_service.read_by_path(current_user.id, normalized)
        media_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE
        return StreamingResponse(
            stream,
            media_type=media_type,
            headers={"Content-Disposition": build_attachment_content_disposition(filename)},
        )

    @router.get("/download-folder", response_class=StreamingResponse)
    async def download_folder(
        path: str = Query(...),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
    ) -> StreamingResponse:
        normalized = _normalize_api_path(path)
        dir_name = PurePosixPath(normalized).name or "folder"
        logger.info(
            "{} download folder requested for user {} path {}",
            log_label,
            current_user.id,
            path,
        )

        stream = file_service.download_directory_as_zip(current_user.id, normalized)
        return StreamingResponse(
            stream,
            media_type="application/zip",
            headers={
                "Content-Disposition": build_attachment_content_disposition(
                    f"{dir_name}.zip",
                )
            },
        )

    @router.delete("", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_file(
        path: str = Query(...),
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
        session: AsyncSession = Depends(get_async_session),
    ) -> None:
        logger.info(
            "{} delete requested for user {} path {}",
            log_label,
            current_user.id,
            path,
        )

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

        logger.info("{} deleted path {} for user {}", log_label, path, current_user.id)

    @router.post("/mkdir", status_code=status.HTTP_201_CREATED)
    async def create_directory(
        body: MkdirRequest,
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
        session: AsyncSession = Depends(get_async_session),
    ) -> dict[str, str]:
        normalized = _normalize_api_path(body.path)
        logger.info(
            "{} mkdir requested for user {} at path {} name {}",
            log_label,
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
        logger.info(
            "{} directory created for user {} at {}",
            log_label,
            current_user.id,
            created_path,
        )
        return {"path": created_path, "name": body.name}

    @router.patch(
        "/rename",
        response_model=FileRecordResponse,
        responses={204: {"description": "Directory renamed"}},
    )
    async def rename_file(
        body: RenameRequest,
        current_user: User = Depends(user_dependency),
        file_service: FileService = Depends(file_service_dependency),
        session: AsyncSession = Depends(get_async_session),
    ) -> FileRecordResponse | Response:
        logger.info(
            "{} rename requested for user {} path {} to {}",
            log_label,
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
            logger.info(
                "{} renamed directory {} for user {}",
                log_label,
                body.path,
                current_user.id,
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        logger.info("{} renamed file {} for user {}", log_label, record.id, current_user.id)
        return _file_record_response(record)

    @router.get("/search")
    async def search_files(
        q: str = Query(..., min_length=1),
        current_user: User = Depends(user_dependency),
    ) -> None:
        logger.info(
            "{} search requested by user {} with query {}",
            log_label,
            current_user.id,
            q,
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error_code": "NOT_IMPLEMENTED",
                "message": SEARCH_NOT_IMPLEMENTED_MESSAGE,
            },
        )

    return router
