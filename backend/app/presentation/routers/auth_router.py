from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_service import AuthService
from app.domain.entities.user import User
from app.domain.exceptions import EmailAlreadyExistsError, InvalidCredentialsError
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import (
    ACCESS_TOKEN_COOKIE,
    get_auth_service,
    get_current_user,
)
from app.presentation.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user: User) -> UserResponse:
    return UserResponse.from_user(user_id=user.id, email=user.email, role=user.role)


def _set_access_token_cookie(response: Response, token: str, request: Request) -> None:
    auth_settings = get_settings().auth
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=auth_settings.session_ttl_seconds,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
    session: AsyncSession = Depends(get_async_session),
) -> UserResponse:
    try:
        user = await auth_service.register(body.email, body.password)
        await session.commit()
        logger.info("Registration completed for user {}", user.id)
        return _user_response(user)
    except EmailAlreadyExistsError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": ErrorCode.EMAIL_ALREADY_EXISTS,
                "message": str(exc),
            },
        ) from exc


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        token = await auth_service.login(body.email, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": ErrorCode.INVALID_CREDENTIALS,
                "message": str(exc),
            },
        ) from exc

    _set_access_token_cookie(response, token, request)
    user = await auth_service.get_user_from_token(token)
    assert user is not None
    logger.info("Login completed for user {}", user.id)
    return _user_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, httponly=True, samesite="strict")
    logger.info("User logged out")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _user_response(current_user)
