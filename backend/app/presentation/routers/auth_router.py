import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger

from app.domain.entities.user import User
from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.session_store import SessionStore, get_session_store, session_ref
from app.presentation.dependencies.auth import get_current_user
from app.presentation.schemas.auth import UserResponse
from config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_HUB_COOKIE_PARENT_DOMAIN = ".filenkov.store"
_RETIRED_AUTH_MESSAGE = (
    "This authentication endpoint is retired. Sign in via the Auth hub."
)


def _user_response(user: User) -> UserResponse:
    return UserResponse.from_user(user_id=user.id, email=user.email, role=user.role)


def _retired_auth_endpoint() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error_code": ErrorCode.UNAUTHORIZED,
            "message": _RETIRED_AUTH_MESSAGE,
        },
    )


def _hub_cookie_domain(caller_host: str) -> str | None:
    host = caller_host.split("/", 1)[0].split(":", 1)[0].lower()
    if host == "filenkov.store" or host.endswith(_HUB_COOKIE_PARENT_DOMAIN):
        return _HUB_COOKIE_PARENT_DOMAIN
    return None


def _clear_auth_session_cookie(response: Response, request: Request) -> None:
    settings = get_settings()
    cookie_name = settings.auth_grpc.cookie_name
    secure = request.url.scheme == "https"
    response.delete_cookie(
        key=cookie_name,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    domain = _hub_cookie_domain(settings.auth_grpc.caller_host)
    if domain is not None:
        response.delete_cookie(
            key=cookie_name,
            domain=domain,
            httponly=True,
            samesite="lax",
            secure=secure,
        )


async def _forward_hub_logout(session_id: str) -> None:
    settings = get_settings()
    logout_url = f"{settings.auth_grpc.logout_url.rstrip('/')}/api/logout"
    cookie_header = f"{settings.auth_grpc.cookie_name}={session_id}"
    timeout = settings.auth_grpc.timeout_ms / 1000.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(logout_url, headers={"Cookie": cookie_header})
        if response.is_success:
            logger.info(
                "Forwarded logout to Auth-Service session_ref={}",
                session_ref(session_id),
            )
            return
        logger.warning(
            "Auth-Service logout returned status={} session_ref={}",
            response.status_code,
            session_ref(session_id),
        )
    except httpx.HTTPError:
        logger.exception(
            "Failed to forward logout to Auth-Service session_ref={}",
            session_ref(session_id),
        )


@router.post("/register")
async def register() -> None:
    _retired_auth_endpoint()


@router.post("/login")
async def login() -> None:
    _retired_auth_endpoint()


@router.get("/google")
async def google_auth_start() -> None:
    _retired_auth_endpoint()


@router.get("/google/callback")
async def google_auth_callback() -> None:
    _retired_auth_endpoint()


@router.get("/google/session")
async def google_auth_session() -> None:
    _retired_auth_endpoint()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session_store: SessionStore = Depends(get_session_store),
) -> None:
    settings = get_settings()
    session_id = request.cookies.get(settings.auth_grpc.cookie_name)
    if session_id:
        await _forward_hub_logout(session_id)
        try:
            await session_store.delete_private_key(session_id)
        except Exception:
            logger.exception(
                "Failed to delete private vault key on logout session_ref={}",
                session_ref(session_id),
            )
    _clear_auth_session_cookie(response, request)
    logger.info("User logged out")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _user_response(current_user)
