from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User as UserEntity
from app.domain.value_objects.role import Role
from app.infrastructure.database.models import User as UserModel
from app.infrastructure.database.models import UserQuotaUsage
from config import get_settings


@dataclass(frozen=True)
class UserAdminRow:
    user: UserEntity
    quota_used_bytes: int
    limit_bytes: int
    private_limit_bytes: int


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> UserEntity | None:
        result = await self._session.execute(select(UserModel).where(UserModel.email == email))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_by_id(self, user_id: UUID) -> UserEntity | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            return None
        return self._to_entity(model)

    async def upsert_from_principal(self, principal: AuthPrincipal) -> UserEntity | None:
        """Project directory identity into the local users table (id + email only).

        Authorization must use ``principal.role``. Returns None when email belongs
        to a different UUID.
        """
        existing = await self.get_by_id(principal.id)
        if existing is not None:
            return await self._sync_projection(principal)

        email_owner = await self.get_by_email(principal.email)
        if email_owner is not None:
            logger.error(
                "Directory principal user_id={} email conflicts with local user_id={}",
                principal.id,
                email_owner.id,
            )
            return None

        model = UserModel(
            id=principal.id,
            email=principal.email,
            is_active=True,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError:
            logger.error(
                "Directory principal user_id={} could not be inserted due to a unique constraint",
                principal.id,
            )
            return None

        await self._session.refresh(model)
        logger.info("Created local user projection user_id={}", model.id)
        return self._to_entity(model, role=principal.role)

    async def _sync_projection(self, principal: AuthPrincipal) -> UserEntity:
        model = await self._session.get(UserModel, principal.id)
        if model is None:
            logger.error("User {} disappeared during directory projection sync", principal.id)
            raise RuntimeError(f"User {principal.id} not found during projection sync")

        if principal.email and model.email != principal.email:
            model.email = principal.email
        if not model.is_active:
            model.is_active = True
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Updated local user projection user_id={}", model.id)
        return self._to_entity(model, role=principal.role)

    async def set_active(self, user_id: UUID, is_active: bool) -> UserEntity | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            logger.warning("User {} not found for active status update", user_id)
            return None
        model.is_active = is_active
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Updated active status for user {} to {}", user_id, is_active)
        return self._to_entity(model)

    def _to_admin_row(self, user_model: UserModel, quota_model: UserQuotaUsage | None) -> UserAdminRow:
        default_limit = get_settings().business_logic.default_user_quota_bytes
        return UserAdminRow(
            user=self._to_entity(user_model),
            quota_used_bytes=quota_model.total_bytes if quota_model else 0,
            limit_bytes=quota_model.limit_bytes if quota_model else default_limit,
            private_limit_bytes=quota_model.private_limit_bytes if quota_model else 0,
        )

    async def get_admin_rows_by_ids(self, user_ids: list[UUID]) -> dict[UUID, UserAdminRow]:
        if not user_ids:
            return {}

        stmt = (
            select(UserModel, UserQuotaUsage)
            .outerjoin(UserQuotaUsage, UserQuotaUsage.user_id == UserModel.id)
            .where(UserModel.id.in_(user_ids))
        )
        result = await self._session.execute(stmt)
        rows: dict[UUID, UserAdminRow] = {}
        for user_model, quota_model in result.all():
            rows[user_model.id] = self._to_admin_row(user_model, quota_model)
        logger.info("Loaded {} local admin projections for {} directory users", len(rows), len(user_ids))
        return rows

    async def delete(self, user_id: UUID) -> bool:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            logger.warning("User {} not found for deletion", user_id)
            return False

        await self._session.delete(model)
        await self._session.flush()
        logger.info("Deleted user {}", user_id)
        return True

    @staticmethod
    def _to_entity(model: UserModel, role: Role = Role.STRANGER) -> UserEntity:
        created_at = model.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return UserEntity(
            id=model.id,
            email=model.email,
            role=role,
            is_active=model.is_active,
            created_at=created_at,
        )
