"""US-GWUS-01: identity from gateway headers + cached User-Service fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.application.ports.user_directory import DirectoryUser
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import UserServiceUnavailableError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_user_directory, get_user_repository
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.middleware.check_role import check_role
from app.presentation.routers.auth_router import router as auth_router
from config import get_settings

FAMILY_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
FILES = "/api/files"
SHARED = "/api/shared"
ADMIN = "/api/admin/probe"


class FakeUserDirectory:
    def __init__(
        self,
        *,
        role: Role = Role.FAMILY,
        email: str = "family@example.test",
        username: str = "family",
        role_error: Exception | None = None,
        user_error: Exception | None = None,
        listed: list[DirectoryUser] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.role = role
        self.email = email
        self.username = username
        self.role_error = role_error
        self.user_error = user_error
        self.listed = listed or []
        self.list_error = list_error
        self.role_calls: list[UUID] = []
        self.user_calls: list[UUID] = []
        self.list_calls = 0

    async def get_storage_role(self, user_id: UUID) -> Role:
        self.role_calls.append(user_id)
        if self.role_error is not None:
            raise self.role_error
        return self.role

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        self.user_calls.append(user_id)
        if self.user_error is not None:
            raise self.user_error
        return DirectoryUser(
            id=user_id,
            email=self.email,
            username=self.username,
            storage_role=self.role,
        )

    async def list_users(self) -> list[DirectoryUser]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.listed)


class FakeUserRepository:
    def __init__(self) -> None:
        self.principals: list[AuthPrincipal] = []

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User:
        self.principals.append(principal)
        return User(
            id=principal.id,
            email=principal.email,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    get_user_directory.cache_clear()
    yield
    get_settings.cache_clear()
    get_user_directory.cache_clear()


def _headers(
    *,
    user_id: UUID | None = FAMILY_USER_ID,
    storage_role: str | None = Role.FAMILY.value,
    auth_user_id: UUID | None = None,
    email: str | None = "family@example.test",
    auth_role: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if user_id is not None:
        headers["X-User-Id"] = str(user_id)
    if auth_user_id is not None:
        headers["X-Auth-User-Id"] = str(auth_user_id)
    if storage_role is not None:
        headers["X-Storage-Role"] = storage_role
    if email is not None:
        headers["X-Auth-Email"] = email
    if auth_role is not None:
        headers["X-Auth-Role"] = auth_role
    return headers


def _build_app(directory: FakeUserDirectory, user_repo: FakeUserRepository | None = None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)

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

    app.dependency_overrides[get_user_directory] = lambda: directory
    app.dependency_overrides[get_user_repository] = lambda: user_repo or FakeUserRepository()

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def test_header_user_id_and_storage_role_skips_user_service() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(FILES, headers=_headers())

    assert response.status_code == 200
    assert directory.role_calls == []
    assert directory.user_calls == []


def test_user_id_only_fetches_role_once_then_cache_is_client_concern() -> None:
    """Missing X-Storage-Role → one GET role per request; client cache is tested separately."""
    directory = FakeUserDirectory(role=Role.FAMILY)
    client = TestClient(_build_app(directory))
    headers = _headers(storage_role=None)

    first = client.get(FILES, headers=headers)
    second = client.get(FILES, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert directory.role_calls == [FAMILY_USER_ID, FAMILY_USER_ID]


def test_x_auth_role_ignored_when_user_service_down() -> None:
    directory = FakeUserDirectory(
        role_error=UserServiceUnavailableError("user directory down"),
    )
    client = TestClient(_build_app(directory))

    response = client.get(
        FILES,
        headers=_headers(storage_role=None, auth_role="ADMIN"),
    )

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.USER_SERVICE_UNAVAILABLE
    assert directory.role_calls == [FAMILY_USER_ID]


def test_invalid_storage_role_header_is_error_not_stranger() -> None:
    directory = FakeUserDirectory()
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(directory, user_repo=user_repo))

    response = client.get(FILES, headers=_headers(storage_role="not-a-role"))

    assert response.status_code == 500
    assert _error_code(response) == ErrorCode.INTERNAL_ERROR
    assert directory.role_calls == []
    assert user_repo.principals == []


def test_no_user_id_headers_is_unauthorized_without_user_service() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(FILES)

    assert response.status_code == 401
    assert _error_code(response) == ErrorCode.UNAUTHORIZED
    assert directory.role_calls == []
    assert directory.user_calls == []


def test_x_auth_user_id_used_when_x_user_id_missing() -> None:
    directory = FakeUserDirectory()
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(directory, user_repo=user_repo))

    response = client.get(
        "/api/auth/me",
        headers=_headers(user_id=None, auth_user_id=FAMILY_USER_ID),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(FAMILY_USER_ID)
    assert body["email"] == "family@example.test"
    assert body["role"] == Role.FAMILY.value
    assert directory.role_calls == []
    assert user_repo.principals[0].role is Role.FAMILY


def test_stranger_header_forbidden_on_shared() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(SHARED, headers=_headers(storage_role=Role.STRANGER.value))

    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert directory.role_calls == []


def test_admin_header_allowed_on_admin() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(ADMIN, headers=_headers(storage_role=Role.ADMIN.value))

    assert response.status_code == 200
    assert directory.role_calls == []


def test_blocked_storage_role_is_forbidden() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(FILES, headers=_headers(storage_role=Role.BLOCKED.value))

    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert directory.role_calls == []


def test_me_returns_storage_role_not_hub_role() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.get(
        "/api/auth/me",
        headers=_headers(storage_role=Role.FAMILY.value, auth_role="ADMIN"),
    )

    assert response.status_code == 200
    assert response.json()["role"] == Role.FAMILY.value
    assert directory.role_calls == []


def test_logout_is_gone() -> None:
    directory = FakeUserDirectory()
    client = TestClient(_build_app(directory))

    response = client.post("/api/auth/logout")

    assert response.status_code == 410
    assert _error_code(response) == ErrorCode.ACCESS_DENIED


def test_missing_email_fetches_user_from_directory() -> None:
    directory = FakeUserDirectory(email="from-us@example.test")
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(directory, user_repo=user_repo))

    response = client.get("/api/auth/me", headers=_headers(email=None))

    assert response.status_code == 200
    assert response.json()["email"] == "from-us@example.test"
    assert directory.user_calls == [FAMILY_USER_ID]
    assert directory.role_calls == []
