from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import UserQuotaUsage


class QuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: UUID) -> UserQuotaUsage:
        model = await self._session.get(UserQuotaUsage, user_id)
        if model is not None:
            return model

        model = UserQuotaUsage(
            user_id=user_id,
            total_bytes=0,
            private_bytes=0,
            photos_bytes=0,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Created quota usage record for user {}", user_id)
        return model
