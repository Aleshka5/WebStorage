from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.schemas.quota import QuotaResponse

router = APIRouter(prefix="/api/quota", tags=["quota"])


@router.get("/me", response_model=QuotaResponse)
async def get_my_quota(
    current_user: User = Depends(get_current_user),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    session: AsyncSession = Depends(get_async_session),
) -> QuotaResponse:
    usage = await quota_repo.get_by_user_id(current_user.id)
    await session.commit()

    logger.info(
        "Quota fetched for user {}: used_bytes={}, limit_bytes={}",
        current_user.id,
        usage.total_bytes,
        usage.limit_bytes,
    )

    return QuotaResponse(
        used_bytes=usage.total_bytes,
        limit_bytes=usage.limit_bytes,
        private_bytes=usage.private_bytes,
        private_limit_bytes=usage.private_limit_bytes,
    )
