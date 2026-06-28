from uuid import UUID

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.file_record import FileSection
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
            private_limit_bytes=0,
            photos_bytes=0,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Created quota usage record for user {}", user_id)
        return model

    async def increment(
        self,
        user_id: UUID,
        size_bytes: int,
        section: FileSection,
    ) -> UserQuotaUsage:
        await self.get_by_user_id(user_id)

        values: dict[str, object] = {
            "total_bytes": UserQuotaUsage.total_bytes + size_bytes,
        }
        if section == FileSection.PRIVATE:
            values["private_bytes"] = UserQuotaUsage.private_bytes + size_bytes
        elif section == FileSection.PHOTOS:
            values["photos_bytes"] = UserQuotaUsage.photos_bytes + size_bytes

        stmt = (
            update(UserQuotaUsage)
            .where(UserQuotaUsage.user_id == user_id)
            .values(**values)
            .returning(UserQuotaUsage)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        await self._session.refresh(model)
        logger.info(
            "Incremented quota for user {} by {} bytes (section={})",
            user_id,
            size_bytes,
            section.value,
        )
        return model

    async def decrement(
        self,
        user_id: UUID,
        size_bytes: int,
        section: FileSection,
    ) -> UserQuotaUsage:
        await self.get_by_user_id(user_id)

        values: dict[str, object] = {
            "total_bytes": UserQuotaUsage.total_bytes - size_bytes,
        }
        if section == FileSection.PRIVATE:
            values["private_bytes"] = UserQuotaUsage.private_bytes - size_bytes
        elif section == FileSection.PHOTOS:
            values["photos_bytes"] = UserQuotaUsage.photos_bytes - size_bytes

        stmt = (
            update(UserQuotaUsage)
            .where(UserQuotaUsage.user_id == user_id)
            .values(**values)
            .returning(UserQuotaUsage)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        await self._session.refresh(model)
        logger.info(
            "Decremented quota for user {} by {} bytes (section={})",
            user_id,
            size_bytes,
            section.value,
        )
        return model

    async def reset_private_usage(self, user_id: UUID) -> UserQuotaUsage:
        usage = await self.get_by_user_id(user_id)
        private_bytes = usage.private_bytes

        if private_bytes == 0:
            logger.info("Private quota already zero for user {}", user_id)
            return usage

        stmt = (
            update(UserQuotaUsage)
            .where(UserQuotaUsage.user_id == user_id)
            .values(
                total_bytes=UserQuotaUsage.total_bytes - private_bytes,
                private_bytes=0,
            )
            .returning(UserQuotaUsage)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        await self._session.refresh(model)
        logger.info(
            "Reset private quota for user {} (removed {} bytes from total usage)",
            user_id,
            private_bytes,
        )
        return model

    async def update_private_limit(self, user_id: UUID, limit_bytes: int) -> UserQuotaUsage:
        await self.get_by_user_id(user_id)

        stmt = (
            update(UserQuotaUsage)
            .where(UserQuotaUsage.user_id == user_id)
            .values(private_limit_bytes=limit_bytes)
            .returning(UserQuotaUsage)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        await self._session.refresh(model)
        logger.info(
            "Updated private limit for user {} to {} bytes",
            user_id,
            limit_bytes,
        )
        return model

    async def update_total_bytes(self, user_id: UUID, total_bytes: int) -> UserQuotaUsage:
        await self.get_by_user_id(user_id)

        stmt = (
            update(UserQuotaUsage)
            .where(UserQuotaUsage.user_id == user_id)
            .values(total_bytes=total_bytes)
            .returning(UserQuotaUsage)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one()
        await self._session.refresh(model)
        logger.info(
            "Updated total quota for user {} to {} bytes",
            user_id,
            total_bytes,
        )
        return model

    async def list_all_user_ids(self) -> list[UUID]:
        result = await self._session.execute(select(UserQuotaUsage.user_id))
        user_ids = list(result.scalars().all())
        logger.info("Listed {} user quota records", len(user_ids))
        return user_ids
