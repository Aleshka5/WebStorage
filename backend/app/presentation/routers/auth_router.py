from secrets import token_urlsafe

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth_service import AuthService
from app.domain.entities.user import User
from app.domain.exceptions import EmailAlreadyExistsError, InvalidCredentialsError
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.database.session import get_async_session
from app.infrastructure.oauth_client import GoogleOAuthClient
from app.infrastructure.session_store import SessionStore, get_session_store
from app.presentation.dependencies.auth import (
    ACCESS_TOKEN_COOKIE,
    get_auth_service,
    get_current_user,
    get_google_oauth_client,
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
        samesite="lax",
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


@router.get("/google")
async def google_auth_start(
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
    session_store: SessionStore = Depends(get_session_store),
) -> RedirectResponse:
    auth_settings = get_settings().auth
    if not auth_settings.google_client_id or not auth_settings.google_client_secret:
        logger.error("Google OAuth is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": ErrorCode.UNAUTHORIZED,
                "message": "Google OAuth is not configured",
            },
        )

    state = token_urlsafe(32)
    await session_store.store_oauth_state(state)
    auth_url = oauth_client.get_auth_url(state)
    logger.info("Redirecting user to Google OAuth")
    return RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/google/callback")
async def google_auth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    auth_service: AuthService = Depends(get_auth_service),
    oauth_client: GoogleOAuthClient = Depends(get_google_oauth_client),
    session_store: SessionStore = Depends(get_session_store),
    db_session: AsyncSession = Depends(get_async_session),
) -> RedirectResponse:
    auth_settings = get_settings().auth

    if not await session_store.consume_oauth_state(state):
        logger.warning("Google OAuth callback received invalid state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.INVALID_CREDENTIALS,
                "message": "Invalid OAuth state",
            },
        )

    try:
        google_user_info = await oauth_client.exchange_code(code, state)
        token = await auth_service.login_or_create_google_user(google_user_info)
        await db_session.commit()
    except InvalidCredentialsError as exc:
        await db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": ErrorCode.INVALID_CREDENTIALS,
                "message": str(exc),
            },
        ) from exc
    except Exception:
        await db_session.rollback()
        logger.exception("Google OAuth callback failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_code": ErrorCode.UNAUTHORIZED,
                "message": "Google authentication failed",
            },
        ) from None

    ticket = token_urlsafe(32)
    await session_store.store_oauth_ticket(ticket, token)
    session_url = f"{auth_settings.frontend_url}/api/auth/google/session?ticket={ticket}"
    logger.info("Google OAuth callback completed, redirecting to session bridge")
    return RedirectResponse(url=session_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/google/session")
async def google_auth_session(
    request: Request,
    ticket: str = Query(...),
    session_store: SessionStore = Depends(get_session_store),
) -> RedirectResponse:
    auth_settings = get_settings().auth
    token = await session_store.consume_oauth_ticket(ticket)
    if token is None:
        logger.warning("Google OAuth session bridge received invalid ticket")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.INVALID_CREDENTIALS,
                "message": "Invalid or expired OAuth session ticket",
            },
        )

    response = RedirectResponse(
        url=f"{auth_settings.frontend_url}/files",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    _set_access_token_cookie(response, token, request)
    logger.info("Google OAuth session established, redirecting to /files")
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, httponly=True, samesite="lax")
    logger.info("User logged out")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _user_response(current_user)
