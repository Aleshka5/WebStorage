from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.user_directory import UserDirectory
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import (
    AuthBlockedError,
    AuthMisconfiguredError,
    AuthUnauthenticatedError,
)
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.user_service.client import UserServiceClient
from config import get_settings

HEADER_USER_ID = "X-User-Id"
HEADER_AUTH_USER_ID = "X-Auth-User-Id"
HEADER_AUTH_EMAIL = "X-Auth-Email"
HEADER_AUTH_NAME = "X-Auth-Name"
HEADER_STORAGE_ROLE = "X-Storage-Role"


def get_user_repository(
    session: AsyncSession = Depends(get_async_session),
) -> UserRepository:
    return UserRepository(session)


def get_quota_repository(
    session: AsyncSession = Depends(get_async_session),
) -> QuotaRepository:
    return QuotaRepository(session)


@lru_cache
def get_user_directory() -> UserDirectory:
    return UserServiceClient()


def _parse_uuid(raw: str | None) -> UUID | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _resolve_user_id(request: Request) -> UUID:
    preferred = _parse_uuid(request.headers.get(HEADER_USER_ID))
    if preferred is not None:
        return preferred
    fallback = _parse_uuid(request.headers.get(HEADER_AUTH_USER_ID))
    if fallback is not None:
        return fallback
    raw_preferred = request.headers.get(HEADER_USER_ID)
    raw_fallback = request.headers.get(HEADER_AUTH_USER_ID)
    if raw_preferred or raw_fallback:
        logger.error("Identity header is present but is not a valid UUID")
        raise AuthUnauthenticatedError("Authentication required")
    logger.warning("Authentication required: missing user id headers")
    raise AuthUnauthenticatedError("Authentication required")


def _header_storage_role(request: Request) -> Role | None:
    raw = request.headers.get(HEADER_STORAGE_ROLE)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip()
    try:
        return Role(value)
    except ValueError as exc:
        logger.error("Malformed X-Storage-Role header value")
        raise AuthMisconfiguredError("Invalid X-Storage-Role") from exc


def _user_from_principal(principal: AuthPrincipal, local: User | None) -> User:
    if local is None:
        return User(
            id=principal.id,
            email=principal.email,
            role=principal.role,
            is_active=True,
            created_at=datetime.now(UTC),
        )
    return User(
        id=principal.id,
        email=principal.email or local.email,
        role=principal.role,
        is_active=local.is_active,
        created_at=local.created_at,
    )


async def get_current_user(
    request: Request,
    user_directory: UserDirectory = Depends(get_user_directory),
    user_repo: UserRepository = Depends(get_user_repository),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    user_id = _resolve_user_id(request)
    role = _header_storage_role(request)
    if role is None:
        logger.info("X-Storage-Role missing; resolving storage role from User-Service user_id={}", user_id)
        role = await user_directory.get_storage_role(user_id)
    else:
        logger.info("Using gateway X-Storage-Role for user_id={} role={}", user_id, role.value)

    if role is Role.BLOCKED:
        logger.warning("Storage role BLOCKED for user_id={}", user_id)
        raise AuthBlockedError("Access denied")

    email = (request.headers.get(HEADER_AUTH_EMAIL) or "").strip()
    name = (request.headers.get(HEADER_AUTH_NAME) or "").strip()
    if not email:
        logger.info("X-Auth-Email missing; fetching user from User-Service user_id={}", user_id)
        directory_user = await user_directory.get_user(user_id)
        email = directory_user.email
        if not name:
            name = directory_user.username

    principal = AuthPrincipal(id=user_id, email=email, name=name, role=role)
    local_user = await user_repo.upsert_from_principal(principal)
    await session.commit()
    if local_user is None:
        logger.error("Cannot project directory user_id={} into local users", principal.id)
        raise RuntimeError("Cannot project directory user into local users")

    user = _user_from_principal(principal, local_user)
    logger.info("Resolved current user user_id={} role={}", user.id, user.role.value)
    return user
