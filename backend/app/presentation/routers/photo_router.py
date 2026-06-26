from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.photo_service import PhotoItem, PhotoService, PhotosPage
from app.domain.entities.user import User
from app.domain.exceptions import UnsupportedFormatError
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.dependencies.photos import get_photo_service
from app.presentation.routers.file_router import _ensure_upload_quota, _raise_http_for_domain_error
from app.presentation.schemas.photos import PhotoItemResponse, PhotoListResponse
from app.presentation.utils.content_disposition import build_attachment_content_disposition
from config import get_settings

router = APIRouter(prefix="/api/photos", tags=["photos"])


def _photo_item_response(item: PhotoItem) -> PhotoItemResponse:
    return PhotoItemResponse(
        id=item.id,
        preview_url=item.preview_url,
        original_url=item.original_url,
        created_at=item.created_at,
        size=item.size,
    )


def _photo_list_response(page: PhotosPage) -> PhotoListResponse:
    return PhotoListResponse(
        items=[_photo_item_response(item) for item in page.items],
        total=page.total,
        has_next=page.has_next,
    )


def _raise_http_for_photo_error(exc: Exception) -> None:
    if isinstance(exc, UnsupportedFormatError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.UNSUPPORTED_FORMAT,
                "message": str(exc),
            },
        ) from exc
    _raise_http_for_domain_error(exc)


async def _iter_upload_chunks(upload_file: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload_file.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@router.get("", response_model=PhotoListResponse)
async def list_photos(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    photo_service: PhotoService = Depends(get_photo_service),
) -> PhotoListResponse:
    settings = get_settings()
    page_limit = limit if limit is not None else settings.business_logic.photo_batch_size
    logger.info(
        "Listing photos for user {} page {} limit {}",
        current_user.id,
        page,
        page_limit,
    )

    try:
        result = await photo_service.list_photos(current_user.id, page, page_limit)
    except Exception as exc:
        _raise_http_for_photo_error(exc)

    return _photo_list_response(result)


@router.post("/upload", response_model=PhotoItemResponse, status_code=status.HTTP_201_CREATED)
async def upload_photo(
    uploaded_file: UploadFile = File(..., alias="file"),
    current_user: User = Depends(get_current_user),
    photo_service: PhotoService = Depends(get_photo_service),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    session: AsyncSession = Depends(get_async_session),
) -> PhotoItemResponse:
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

    file_size = uploaded_file.size if uploaded_file.size is not None else 0
    logger.info(
        "Photo upload requested for user {} filename {} (size={})",
        current_user.id,
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
        record = await photo_service.upload_photo(
            user_id=current_user.id,
            filename=filename,
            data=_iter_upload_chunks(uploaded_file),
            size=file_size,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_photo_error(exc)

    item = PhotoItem(
        id=record.id,
        preview_url=f"/api/photos/{record.id}/preview",
        original_url=f"/api/photos/{record.id}/original",
        created_at=record.created_at,
        size=record.size_bytes,
    )
    logger.info("Photo upload completed for user {} file {}", current_user.id, record.id)
    return _photo_item_response(item)


@router.get("/{photo_id}/preview")
async def get_photo_preview(
    photo_id: UUID,
    current_user: User = Depends(get_current_user),
    photo_service: PhotoService = Depends(get_photo_service),
) -> Response:
    logger.info("Preview requested for photo {} user {}", photo_id, current_user.id)

    try:
        content, media_type = await photo_service.get_preview(current_user.id, photo_id)
    except Exception as exc:
        _raise_http_for_photo_error(exc)

    return Response(content=content, media_type=media_type)


@router.get("/{photo_id}/original")
async def get_photo_original(
    photo_id: UUID,
    current_user: User = Depends(get_current_user),
    photo_service: PhotoService = Depends(get_photo_service),
) -> StreamingResponse:
    logger.info("Original requested for photo {} user {}", photo_id, current_user.id)

    try:
        mime_type, filename, stream = await photo_service.stream_original(
            current_user.id,
            photo_id,
        )
    except Exception as exc:
        _raise_http_for_photo_error(exc)

    return StreamingResponse(
        stream,
        media_type=mime_type,
        headers={"Content-Disposition": build_attachment_content_disposition(filename)},
    )


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    photo_id: UUID,
    current_user: User = Depends(get_current_user),
    photo_service: PhotoService = Depends(get_photo_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    logger.info("Delete requested for photo {} user {}", photo_id, current_user.id)

    try:
        await photo_service.delete_photo(current_user.id, photo_id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        _raise_http_for_photo_error(exc)

    logger.info("Deleted photo {} for user {}", photo_id, current_user.id)
