from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User
from app.domain.exceptions import StorageUnavailableError
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.schemas.quota import QuotaResponse
from config import Settings, get_settings

router = APIRouter(prefix="/api/quota", tags=["quota"])


def _total_free_space(disk_router: DiskRouter) -> int:
    total = 0
    for disk in disk_router.get_all_disks():
        try:
            total += disk_router.get_free_space(disk.id)
        except StorageUnavailableError:
            logger.warning("Disk {} unavailable when computing quota limit", disk.id)
    return total


def _resolve_limit_bytes(role: Role, settings: Settings) -> int:
    if role is Role.STRANGER:
        return settings.business_logic.stranger_quota_mb * 1024 * 1024

    disk_router = DiskRouter(settings)
    return _total_free_space(disk_router)


@router.get("/me", response_model=QuotaResponse)
async def get_my_quota(
    current_user: User = Depends(get_current_user),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    session: AsyncSession = Depends(get_async_session),
) -> QuotaResponse:
    usage = await quota_repo.get_by_user_id(current_user.id)
    await session.commit()

    limit_bytes = _resolve_limit_bytes(current_user.role, get_settings())
    logger.info(
        "Quota fetched for user {}: used_bytes={}, limit_bytes={}",
        current_user.id,
        usage.total_bytes,
        limit_bytes,
    )

    return QuotaResponse(
        used_bytes=usage.total_bytes,
        limit_bytes=limit_bytes,
        private_bytes=usage.private_bytes,
        private_limit_bytes=usage.private_limit_bytes,
    )
