from fastapi import Depends, HTTPException, status
from loguru import logger

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.entities.user import User
from app.domain.exceptions import StorageUnavailableError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.archive_providers import (
    get_archive_disk_router,
    get_archive_manager,
)
from app.presentation.dependencies.auth import get_quota_repository
from app.presentation.dependencies.files import get_file_repository
from app.presentation.dependencies.storage_factory import (
    build_section_adapter,
    shared_root_prefix,
)
from app.presentation.middleware.check_role import check_role
from config import get_settings


async def _resolve_shared_disk_id(file_repo: FileRepository) -> str:
    records = await file_repo.list_by_section(FileSection.SHARED, limit=1)
    if records:
        return records[0].disk_id

    disk_router = DiskRouter(get_settings())
    try:
        return disk_router.get_write_disk().id
    except StorageUnavailableError:
        logger.error("No storage disk available for shared directory")
        raise


async def get_shared_file_service(
    _current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    file_repo: FileRepository = Depends(get_file_repository),
) -> FileService:
    try:
        disk_id = await _resolve_shared_disk_id(file_repo)
        root_prefix = shared_root_prefix()
        adapter = await build_section_adapter(disk_id, root_prefix)
    except StorageUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": ErrorCode.DISK_UNAVAILABLE,
                "message": str(exc),
            },
        ) from exc

    logger.info(
        "Shared FileService initialized on disk {} root_prefix={}",
        disk_id,
        root_prefix,
    )
    return FileService(
        adapter,
        quota_repo,
        file_repo,
        section=FileSection.SHARED,
        archive_manager=get_archive_manager(),
        disk_router=get_archive_disk_router(),
    )
