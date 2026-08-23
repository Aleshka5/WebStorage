"""US-AUTHZ-12: role matrix with fake AuthValidator (principal.role, not DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import AuthBlockedError, AuthUnauthenticatedError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import (
    get_auth_validator,
    get_current_user,
    get_user_repository,
)
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.middleware.check_role import check_role
from config import get_settings

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = "opaque-session-id-not-for-logs"
COOKIE_NAME = "auth_session"

FILES = "/api/files"
SHARED = "/api/shared"
ADMIN = "/api/admin/probe"
ENDPOINTS = (FILES, SHARED, ADMIN)


def _principal(role: Role) -> AuthPrincipal:
    return AuthPrincipal(
        id=USER_ID,
        email=f"{role.value.lower()}@example.test",
        name=f"{role.value} User",
        role=role,
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
    """Local upsert always persists STRANGER so gates must use principal.role."""

    def __init__(self) -> None:
        self.upserted: list[User] = []

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User:
        user = User(
            id=principal.id,
            email=principal.email,
            password_hash=None,
            google_id=None,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self.upserted.append(user)
        return user


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
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get(FILES)
    async def files(_user: User = Depends(get_current_user)) -> dict[str, bool]:
        return {"ok": True}

    @app.get(SHARED)
    async def shared(
        _user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    ) -> dict[str, bool]:
        return {"ok": True}

    @app.get(ADMIN)
    async def admin_probe(
        _user: User = Depends(check_role(Role.ADMIN)),
    ) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_auth_validator] = lambda: validator
    app.dependency_overrides[get_user_repository] = lambda: user_repo or FakeUserRepository()

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def _cookies() -> dict[str, str]:
    return {COOKIE_NAME: SESSION_ID}


def _assert_ok(response) -> None:
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def _assert_unauthorized(response) -> None:
    assert response.status_code == 401
    assert _error_code(response) == ErrorCode.UNAUTHORIZED


def _assert_forbidden(response) -> None:
    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_cookie_is_unauthorized_and_skips_validate(path: str) -> None:
    validator = FakeAuthValidator(principal=_principal(Role.ADMIN))
    client = TestClient(_build_app(validator))

    response = client.get(path)

    _assert_unauthorized(response)
    assert validator.validate_calls == 0


@pytest.mark.parametrize("path", ENDPOINTS)
def test_invalid_session_is_unauthorized(path: str) -> None:
    validator = FakeAuthValidator(error=AuthUnauthenticatedError("invalid session"))
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(validator, user_repo=user_repo))

    response = client.get(path, cookies=_cookies())

    _assert_unauthorized(response)
    assert validator.validate_calls == 1
    assert user_repo.upserted == []


@pytest.mark.parametrize(
    ("role", "files_ok", "shared_ok", "admin_ok"),
    [
        (Role.STRANGER, True, False, False),
        (Role.FAMILY, True, True, False),
        (Role.ADMIN, True, True, True),
    ],
)
def test_role_matrix_uses_principal_role_not_db(
    role: Role,
    files_ok: bool,
    shared_ok: bool,
    admin_ok: bool,
) -> None:
    validator = FakeAuthValidator(principal=_principal(role))
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(validator, user_repo=user_repo))
    cookies = _cookies()

    files = client.get(FILES, cookies=cookies)
    shared = client.get(SHARED, cookies=cookies)
    admin = client.get(ADMIN, cookies=cookies)

    (_assert_ok if files_ok else _assert_forbidden)(files)
    (_assert_ok if shared_ok else _assert_forbidden)(shared)
    (_assert_ok if admin_ok else _assert_forbidden)(admin)
    assert validator.validate_calls == 3
    assert user_repo.upserted
    assert all(user.role is Role.STRANGER for user in user_repo.upserted)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_blocked_session_is_forbidden(path: str) -> None:
    validator = FakeAuthValidator(error=AuthBlockedError("blocked"))
    client = TestClient(_build_app(validator))

    response = client.get(path, cookies=_cookies())

    _assert_forbidden(response)
    assert validator.validate_calls == 1


def test_stranger_requests_are_not_cached() -> None:
    validator = FakeAuthValidator(principal=_principal(Role.STRANGER))
    client = TestClient(_build_app(validator))
    cookies = _cookies()

    first = client.get(FILES, cookies=cookies)
    second = client.get(FILES, cookies=cookies)

    _assert_ok(first)
    _assert_ok(second)
    assert validator.validate_calls == 2
    assert validator.calls == [
        (SESSION_ID, get_settings().auth_grpc.caller_host),
        (SESSION_ID, get_settings().auth_grpc.caller_host),
    ]
