from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_service import AuthService
from app.application.ports.auth_validator import AuthValidator
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.auth_grpc.client import AuthGrpcClient
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.oauth_client import GoogleOAuthClient
from config import get_settings


def get_user_repository(
    session: AsyncSession = Depends(get_async_session),
) -> UserRepository:
    return UserRepository(session)


def get_quota_repository(
    session: AsyncSession = Depends(get_async_session),
) -> QuotaRepository:
    return QuotaRepository(session)


def get_auth_service(
    user_repo: UserRepository = Depends(get_user_repository),
) -> AuthService:
    return AuthService(user_repo, get_settings())


def get_google_oauth_client() -> GoogleOAuthClient:
    return GoogleOAuthClient(get_settings())


@lru_cache
def get_auth_validator() -> AuthValidator:
    return AuthGrpcClient()


def _user_from_principal(principal: AuthPrincipal, local: User | None) -> User:
    if local is None:
        return User(
            id=principal.id,
            email=principal.email,
            password_hash=None,
            google_id=None,
            role=principal.role,
            is_active=True,
            created_at=datetime.now(UTC),
        )
    return User(
        id=principal.id,
        email=principal.email or local.email,
        password_hash=local.password_hash,
        google_id=local.google_id,
        role=principal.role,
        is_active=local.is_active,
        created_at=local.created_at,
    )


async def get_current_user(
    request: Request,
    auth_validator: AuthValidator = Depends(get_auth_validator),
    user_repo: UserRepository = Depends(get_user_repository),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    settings = get_settings()
    session_id = request.cookies.get(settings.auth_grpc.cookie_name)
    if not session_id:
        logger.warning("Authentication required: missing session cookie")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": ErrorCode.UNAUTHORIZED,
                "message": "Authentication required",
            },
        )

    principal = await auth_validator.validate(session_id, settings.auth_grpc.caller_host)
    local_user = await user_repo.upsert_from_principal(principal)
    await session.commit()
    if local_user is None:
        logger.error(
            "Cannot project Auth user_id={} into local users (email/UUID conflict; US-AUTHZ-11)",
            principal.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": ErrorCode.INTERNAL_ERROR,
                "message": "Internal server error",
            },
        )

    user = _user_from_principal(principal, local_user)
    logger.info(
        "Resolved current user user_id={} role={}",
        user.id,
        user.role.value,
    )
    return user
