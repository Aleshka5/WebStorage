from dataclasses import dataclass
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User as UserEntity
from app.domain.value_objects.role import Role
from app.infrastructure.database.models import User as UserModel
from app.infrastructure.database.models import UserQuotaUsage
from app.infrastructure.database.models import UserRole


@dataclass(frozen=True)
class UserAdminRow:
    user: UserEntity
    quota_used_bytes: int
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

    async def get_by_google_id(self, google_id: str) -> UserEntity | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.google_id == google_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_by_id(self, user_id: UUID) -> UserEntity | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            return None
        return self._to_entity(model)

    async def create(
        self,
        email: str,
        password_hash: str | None = None,
        google_id: str | None = None,
        role: Role = Role.STRANGER,
    ) -> UserEntity:
        model = UserModel(
            email=email,
            password_hash=password_hash,
            google_id=google_id,
            role=UserRole(role.value),
            is_active=True,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Created user {} with role {}", model.id, model.role.value)
        return self._to_entity(model)

    async def link_google_id(self, user_id: UUID, google_id: str) -> UserEntity | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            logger.warning("User {} not found for Google ID linking", user_id)
            return None
        model.google_id = google_id
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Linked Google ID for user {}", user_id)
        return self._to_entity(model)

    async def update_role(self, user_id: UUID, role: Role) -> UserEntity | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            logger.warning("User {} not found for role update", user_id)
            return None
        model.role = UserRole(role.value)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Updated role for user {} to {}", user_id, role.value)
        return self._to_entity(model)

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

    async def list_for_admin(
        self,
        page: int,
        limit: int,
        role_filter: Role | None = None,
        email_search: str | None = None,
    ) -> tuple[list[UserAdminRow], int]:
        filters = []
        if role_filter is not None:
            filters.append(UserModel.role == UserRole(role_filter.value))
        if email_search:
            filters.append(UserModel.email.ilike(f"%{email_search}%"))

        count_stmt = select(func.count()).select_from(UserModel).where(*filters)
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        offset = (page - 1) * limit
        stmt = (
            select(UserModel, UserQuotaUsage)
            .outerjoin(UserQuotaUsage, UserQuotaUsage.user_id == UserModel.id)
            .where(*filters)
            .order_by(UserModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows: list[UserAdminRow] = []
        for user_model, quota_model in result.all():
            rows.append(
                UserAdminRow(
                    user=self._to_entity(user_model),
                    quota_used_bytes=quota_model.total_bytes if quota_model else 0,
                    private_limit_bytes=quota_model.private_limit_bytes if quota_model else 0,
                )
            )

        logger.info(
            "Listed {} admin users (page={}, limit={}, total={})",
            len(rows),
            page,
            limit,
            total,
        )
        return rows, total

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
    def _to_entity(model: UserModel) -> UserEntity:
        return UserEntity(
            id=model.id,
            email=model.email,
            password_hash=model.password_hash,
            google_id=model.google_id,
            role=Role(model.role.value),
            is_active=model.is_active,
            created_at=model.created_at,
        )
