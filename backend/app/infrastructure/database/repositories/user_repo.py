from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User as UserEntity
from app.domain.value_objects.role import Role
from app.infrastructure.database.models import User as UserModel
from app.infrastructure.database.models import UserRole


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

    async def create(
        self,
        email: str,
        password_hash: str,
        role: Role = Role.STRANGER,
    ) -> UserEntity:
        model = UserModel(
            email=email,
            password_hash=password_hash,
            role=UserRole(role.value),
            is_active=True,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Created user {} with role {}", model.id, model.role.value)
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

    @staticmethod
    def _to_entity(model: UserModel) -> UserEntity:
        return UserEntity(
            id=model.id,
            email=model.email,
            password_hash=model.password_hash,
            role=Role(model.role.value),
            is_active=model.is_active,
            created_at=model.created_at,
        )
