from pathlib import Path
from uuid import UUID

from fastapi import Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.entities.user import User
from app.domain.exceptions import StorageUnavailableError
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from config import get_settings


def get_file_repository(
    session: AsyncSession = Depends(get_async_session),
) -> FileRepository:
    return FileRepository(session)


async def _resolve_user_disk_id(user_id: UUID, file_repo: FileRepository) -> str:
    records = await file_repo.list_by_user_section(user_id, FileSection.FILES)
    if records:
        return records[0].disk_id

    disk_router = DiskRouter(get_settings())
    try:
        return disk_router.get_write_disk().id
    except StorageUnavailableError:
        logger.error("No storage disk available for user {} files directory", user_id)
        raise


def _user_files_base_path(user_id: UUID, disk_id: str) -> Path:
    disk_router = DiskRouter(get_settings())
    disk = disk_router.get_disk_by_id(disk_id)
    return disk.mount_path / "users" / str(user_id) / "files"


async def get_file_service(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    file_repo: FileRepository = Depends(get_file_repository),
) -> FileService:
    try:
        disk_id = await _resolve_user_disk_id(current_user.id, file_repo)
        base_path = _user_files_base_path(current_user.id, disk_id)
        base_path.mkdir(parents=True, exist_ok=True)
        adapter = PlainStorageAdapter(base_path, disk_id=disk_id)
    except StorageUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": ErrorCode.DISK_UNAVAILABLE,
                "message": str(exc),
            },
        ) from exc

    logger.info(
        "FileService initialized for user {} on disk {} at {}",
        current_user.id,
        disk_id,
        base_path,
    )
    return FileService(adapter, quota_repo, file_repo)
