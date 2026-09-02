"""Storage role matrix from request identity, never from the local users table."""

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
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_user_directory, get_user_repository
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.middleware.check_role import check_role
from config import get_settings

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
FILES = "/api/files"
SHARED = "/api/shared"
ADMIN = "/api/admin/probe"
ENDPOINTS = (FILES, SHARED, ADMIN)


class FakeUserDirectory:
    async def get_storage_role(self, user_id: UUID) -> Role:
        raise AssertionError("role header must be used")

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        raise AssertionError("email header must be used")

    async def list_users(self) -> list[DirectoryUser]:
        return []


class FakeUserRepository:
    def __init__(self) -> None:
        self.upserted: list[User] = []

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User:
        user = User(
            id=principal.id,
            email=principal.email,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self.upserted.append(user)
        return user


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    get_user_directory.cache_clear()
    yield
    get_settings.cache_clear()
    get_user_directory.cache_clear()


def _headers(role: Role) -> dict[str, str]:
    return {
        "X-User-Id": str(USER_ID),
        "X-Storage-Role": role.value,
        "X-Auth-Email": f"{role.value.lower()}@example.test",
    }


def _build_app(user_repo: FakeUserRepository | None = None) -> FastAPI:
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

    app.dependency_overrides[get_user_directory] = FakeUserDirectory
    app.dependency_overrides[get_user_repository] = lambda: user_repo or FakeUserRepository()

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def _assert_ok(response) -> None:
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def _assert_forbidden(response) -> None:
    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_identity_is_unauthorized(path: str) -> None:
    client = TestClient(_build_app())

    response = client.get(path)

    assert response.status_code == 401
    assert _error_code(response) == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize(
    ("role", "files_ok", "shared_ok", "admin_ok"),
    [
        (Role.STRANGER, True, False, False),
        (Role.FAMILY, True, True, False),
        (Role.ADMIN, True, True, True),
    ],
)
def test_role_matrix_uses_header_role_not_db(
    role: Role,
    files_ok: bool,
    shared_ok: bool,
    admin_ok: bool,
) -> None:
    user_repo = FakeUserRepository()
    client = TestClient(_build_app(user_repo))
    headers = _headers(role)

    files = client.get(FILES, headers=headers)
    shared = client.get(SHARED, headers=headers)
    admin = client.get(ADMIN, headers=headers)

    (_assert_ok if files_ok else _assert_forbidden)(files)
    (_assert_ok if shared_ok else _assert_forbidden)(shared)
    (_assert_ok if admin_ok else _assert_forbidden)(admin)
    assert user_repo.upserted
    assert all(user.role is Role.STRANGER for user in user_repo.upserted)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_blocked_storage_role_is_forbidden(path: str) -> None:
    client = TestClient(_build_app())

    response = client.get(path, headers=_headers(Role.BLOCKED))

    _assert_forbidden(response)
