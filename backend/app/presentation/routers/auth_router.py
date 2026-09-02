from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.domain.entities.user import User
from app.domain.value_objects.error_codes import ErrorCode
from app.presentation.dependencies.auth import get_current_user
from app.presentation.schemas.auth import UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

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


@router.post("/logout", status_code=status.HTTP_410_GONE)
async def logout() -> None:
    logger.warning("Rejected logout: logout is not offered by WebStorage")
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error_code": ErrorCode.ACCESS_DENIED,
            "message": "Logout is handled by the Auth hub, not WebStorage",
        },
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return _user_response(current_user)
