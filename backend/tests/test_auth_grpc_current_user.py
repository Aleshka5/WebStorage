from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import (
    AuthBlockedError,
    AuthUnauthenticatedError,
    AuthUnavailableError,
)
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.infrastructure.session_store import get_session_store
from app.presentation.dependencies.auth import get_auth_validator, get_user_repository
from app.presentation.dependencies.private import _get_session_id
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.auth_router import router as auth_router
from config import get_settings

FAMILY_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = "opaque-session-id-not-for-logs"
COOKIE_NAME = "auth_session"

FAMILY_PRINCIPAL = AuthPrincipal(
    id=FAMILY_USER_ID,
    email="family@example.test",
    name="Family User",
    role=Role.FAMILY,
)


class FakeAuthValidator:
    def __init__(
        self,
        *,
        principal: AuthPrincipal | None = None,
        error: Exception | None = None,
    ) -> None:
        self.principal = principal
        self.error = error
        self.calls: list[tuple[str, str]] = []

    @property
    def validate_calls(self) -> int:
        return len(self.calls)

    async def validate(self, session_id: str, caller_host: str) -> AuthPrincipal:
        self.calls.append((session_id, caller_host))
        if self.error is not None:
            raise self.error
        assert self.principal is not None
        return self.principal

    async def list_users(self, session_id: str, caller_host: str) -> list[AuthPrincipal]:
        return []


class FakeUserRepository:
    def __init__(self) -> None:
        self.principals: list[AuthPrincipal] = []

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User:
        self.principals.append(principal)
        return User(
            id=principal.id,
            email=principal.email,
            password_hash=None,
            google_id=None,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )


class FakeSessionStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_private_key(self, session_id: str) -> None:
        self.deleted.append(session_id)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    get_auth_validator.cache_clear()
    yield
    get_settings.cache_clear()
    get_auth_validator.cache_clear()


def _build_app(
    validator: FakeAuthValidator,
    user_repo: FakeUserRepository | None = None,
    session_store: FakeSessionStore | None = None,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_auth_validator] = lambda: validator
    app.dependency_overrides[get_user_repository] = lambda: user_repo or FakeUserRepository()

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    if session_store is not None:
        app.dependency_overrides[get_session_store] = lambda: session_store
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def test_me_validates_on_every_request() -> None:
    validator = FakeAuthValidator(principal=FAMILY_PRINCIPAL)
    client = TestClient(_build_app(validator))

    first = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})
    second = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})

    assert first.status_code == 200
    assert second.status_code == 200
    assert validator.validate_calls == 2
    assert validator.calls == [
        (SESSION_ID, get_settings().auth_grpc.caller_host),
        (SESSION_ID, get_settings().auth_grpc.caller_host),
    ]


def test_me_without_cookie_is_unauthorized_and_skips_validate() -> None:
    validator = FakeAuthValidator(principal=FAMILY_PRINCIPAL)
    client = TestClient(_build_app(validator))

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert _error_code(response) == ErrorCode.UNAUTHORIZED
    assert validator.validate_calls == 0


def test_me_unauthenticated_from_validator() -> None:
    validator = FakeAuthValidator(error=AuthUnauthenticatedError("invalid session"))
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(validator, user_repo=user_repo))

    response = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 401
    assert _error_code(response) == ErrorCode.UNAUTHORIZED
    assert validator.validate_calls == 1
    assert user_repo.principals == []


def test_me_blocked_from_validator() -> None:
    validator = FakeAuthValidator(error=AuthBlockedError("blocked"))
    client = TestClient(_build_app(validator))

    response = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED


def test_me_unavailable_from_validator() -> None:
    validator = FakeAuthValidator(error=AuthUnavailableError("auth down"))
    client = TestClient(_build_app(validator))

    response = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.AUTH_UNAVAILABLE


def test_me_family_role_comes_from_principal_not_db() -> None:
    validator = FakeAuthValidator(principal=FAMILY_PRINCIPAL)
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(validator, user_repo=user_repo))

    response = client.get("/api/auth/me", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(FAMILY_USER_ID)
    assert body["email"] == FAMILY_PRINCIPAL.email
    assert body["role"] == Role.FAMILY.value
    assert user_repo.principals[0].role == Role.FAMILY


def test_login_is_gone() -> None:
    client = TestClient(_build_app(FakeAuthValidator()))

    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.test", "password": "secret"},
    )

    assert response.status_code == 410
    assert _error_code(response) == ErrorCode.UNAUTHORIZED
    assert "access_token" not in response.headers.get("set-cookie", "")


def test_register_is_gone() -> None:
    client = TestClient(_build_app(FakeAuthValidator()))

    response = client.post(
        "/api/auth/register",
        json={"email": "user@example.test", "password": "secret"},
    )

    assert response.status_code == 410
    assert _error_code(response) == ErrorCode.UNAUTHORIZED
    assert "access_token" not in response.headers.get("set-cookie", "")


def test_logout_forwards_to_hub_and_clears_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[tuple[str, dict[str, str] | None]] = []

    class FakeHttpxClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> FakeHttpxClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, headers: dict[str, str] | None = None) -> SimpleNamespace:
            posts.append((url, headers))
            return SimpleNamespace(status_code=204, is_success=True)

    monkeypatch.setattr(
        "app.presentation.routers.auth_router.httpx.AsyncClient",
        FakeHttpxClient,
    )
    store = FakeSessionStore()
    client = TestClient(_build_app(FakeAuthValidator(), session_store=store))

    response = client.post("/api/auth/logout", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 204
    assert posts == [
        (
            "http://api:8080/api/logout",
            {"Cookie": f"{COOKIE_NAME}={SESSION_ID}"},
        )
    ]
    assert store.deleted == [SESSION_ID]
    set_cookies = [value.lower() for value in response.headers.get_list("set-cookie")]
    assert set_cookies
    assert all(COOKIE_NAME in value for value in set_cookies)
    assert any("max-age=0" in value for value in set_cookies)
    assert any("domain=.filenkov.store" in value for value in set_cookies)
    assert all("access_token" not in value for value in set_cookies)


def test_get_session_id_uses_auth_session_cookie_not_access_token() -> None:
    request = MagicMock()
    request.cookies = {
        "access_token": "old-jwt",
        COOKIE_NAME: SESSION_ID,
    }

    assert _get_session_id(request) == SESSION_ID


def test_get_session_id_missing_cookie_is_unauthorized() -> None:
    request = MagicMock()
    request.cookies = {"access_token": "old-jwt"}

    with pytest.raises(HTTPException) as exc_info:
        _get_session_id(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error_code"] == ErrorCode.UNAUTHORIZED
