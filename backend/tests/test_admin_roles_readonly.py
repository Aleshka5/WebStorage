from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.admin_service import AdminService
from app.application.ports.user_directory import DirectoryUser
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import UserNotFoundError, UserServiceUnavailableError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.user_repo import UserAdminRow
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.admin import get_admin_service
from app.presentation.dependencies.auth import get_user_directory, get_user_repository
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.admin_router import router as admin_router
from config import get_settings

ADMIN_USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
FAMILY_USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def _directory_user(
    user_id: UUID,
    email: str,
    role: Role,
    username: str = "user",
) -> DirectoryUser:
    return DirectoryUser(id=user_id, email=email, username=username, storage_role=role)


ADMIN_DIR = _directory_user(ADMIN_USER_ID, "admin@example.test", Role.ADMIN, "admin")
FAMILY_DIR = _directory_user(FAMILY_USER_ID, "family@example.test", Role.FAMILY, "family")
USER_B_FAMILY = _directory_user(USER_B_ID, "user-b@example.test", Role.FAMILY, "userb")
USER_B_STRANGER = _directory_user(USER_B_ID, "user-b@example.test", Role.STRANGER, "userb")


class FakeUserDirectory:
    def __init__(
        self,
        *,
        caller_role: Role = Role.ADMIN,
        listed: list[DirectoryUser] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.caller_role = caller_role
        self.listed = listed if listed is not None else []
        self.list_error = list_error
        self.role_calls: list[UUID] = []
        self.list_calls = 0

    async def get_storage_role(self, user_id: UUID) -> Role:
        self.role_calls.append(user_id)
        return self.caller_role

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        return DirectoryUser(
            id=user_id,
            email="caller@example.test",
            username="caller",
            storage_role=self.caller_role,
        )

    async def list_users(self) -> list[DirectoryUser]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.listed)


class FakeUserRepository:
    def __init__(self, *, upsert_returns_none: bool = False) -> None:
        self.principals: list[AuthPrincipal] = []
        self.admin_rows: dict[UUID, UserAdminRow] = {}
        self._upsert_returns_none = upsert_returns_none

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User | None:
        self.principals.append(principal)
        if self._upsert_returns_none:
            return None
        return User(
            id=principal.id,
            email=principal.email,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )

    async def get_admin_rows_by_ids(self, user_ids: list[UUID]) -> dict[UUID, UserAdminRow]:
        return {user_id: self.admin_rows[user_id] for user_id in user_ids if user_id in self.admin_rows}


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    get_user_directory.cache_clear()
    yield
    get_settings.cache_clear()
    get_user_directory.cache_clear()


def _identity(role: Role = Role.ADMIN, user_id: UUID = ADMIN_USER_ID) -> dict[str, str]:
    return {
        "X-User-Id": str(user_id),
        "X-Storage-Role": role.value,
        "X-Auth-Email": "admin@example.test",
    }


def _build_app(
    directory: FakeUserDirectory,
    user_repo: FakeUserRepository | None = None,
    quota_repo: AsyncMock | None = None,
) -> FastAPI:
    repo = user_repo or FakeUserRepository()
    quotas = quota_repo or AsyncMock()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router)
    app.dependency_overrides[get_user_directory] = lambda: directory
    app.dependency_overrides[get_user_repository] = lambda: repo

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    app.dependency_overrides[get_admin_service] = lambda: AdminService(
        user_repo=repo,
        quota_repo=quotas,
        file_repo=MagicMock(),
        disk_router=MagicMock(),
        settings=get_settings(),
    )
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def test_admin_list_users_empty() -> None:
    directory = FakeUserDirectory(listed=[])
    client = TestClient(_build_app(directory))

    response = client.get("/api/admin/users", headers=_identity())

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
    assert directory.list_calls == 1


def test_admin_list_users_joins_quota_and_storage_role() -> None:
    directory = FakeUserDirectory(listed=[ADMIN_DIR, USER_B_FAMILY])
    user_repo = FakeUserRepository()
    user_repo.admin_rows[USER_B_ID] = UserAdminRow(
        user=User(
            id=USER_B_ID,
            email="stale-local@example.test",
            role=Role.ADMIN,
            is_active=True,
            created_at=datetime.now(UTC),
        ),
        quota_used_bytes=1024,
        limit_bytes=50 * 1024 * 1024,
        private_limit_bytes=2048,
    )
    client = TestClient(_build_app(directory, user_repo=user_repo))

    response = client.get("/api/admin/users", headers=_identity())

    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body["items"]}
    assert body["total"] == 2
    assert by_id[str(USER_B_ID)]["role"] == Role.FAMILY.value
    assert by_id[str(USER_B_ID)]["email"] == USER_B_FAMILY.email
    assert by_id[str(USER_B_ID)]["quota_used_bytes"] == 1024
    assert by_id[str(USER_B_ID)]["limit_bytes"] == 50 * 1024 * 1024
    assert by_id[str(ADMIN_USER_ID)]["role"] == Role.ADMIN.value
    assert by_id[str(ADMIN_USER_ID)]["limit_bytes"] == 100 * 1024 * 1024
    assert directory.list_calls == 1


def test_admin_list_users_omitted_storage_service_is_stranger() -> None:
    directory = FakeUserDirectory(listed=[ADMIN_DIR, USER_B_STRANGER])
    client = TestClient(_build_app(directory))

    response = client.get("/api/admin/users", headers=_identity())

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert by_id[str(USER_B_ID)]["role"] == Role.STRANGER.value


def test_patch_user_role_is_gone() -> None:
    directory = FakeUserDirectory(listed=[ADMIN_DIR])
    client = TestClient(_build_app(directory))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/role",
        json={"role": "STRANGER"},
        headers=_identity(),
    )

    assert response.status_code == 410
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert directory.list_calls == 0


def test_admin_list_users_unavailable_is_user_service_unavailable() -> None:
    directory = FakeUserDirectory(list_error=UserServiceUnavailableError("directory down"))
    client = TestClient(_build_app(directory))

    response = client.get("/api/admin/users", headers=_identity())

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.USER_SERVICE_UNAVAILABLE
    assert directory.list_calls == 1


def test_family_cannot_list_admin_users() -> None:
    directory = FakeUserDirectory(caller_role=Role.FAMILY, listed=[ADMIN_DIR, USER_B_FAMILY])
    client = TestClient(_build_app(directory))

    response = client.get(
        "/api/admin/users",
        headers=_identity(role=Role.FAMILY, user_id=FAMILY_USER_ID),
    )

    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert directory.list_calls == 0


def test_patch_quota_creates_local_projection() -> None:
    directory = FakeUserDirectory(listed=[ADMIN_DIR, USER_B_FAMILY])
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(directory, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        headers=_identity(),
    )

    assert response.status_code == 204
    assert user_repo.principals[-1].id == USER_B_ID
    quota_repo.update_private_limit.assert_awaited_once_with(USER_B_ID, 10 * 1024 * 1024 * 1024)
    quota_repo.update_limit.assert_not_called()
    assert directory.list_calls == 1


def test_patch_quota_unknown_directory_user_is_not_found() -> None:
    directory = FakeUserDirectory(listed=[ADMIN_DIR])
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(directory, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        headers=_identity(),
    )

    assert response.status_code == 404
    assert _error_code(response) == ErrorCode.USER_NOT_FOUND
    assert all(principal.id != USER_B_ID for principal in user_repo.principals)
    quota_repo.update_private_limit.assert_not_called()


def test_patch_quota_list_unavailable_is_user_service_unavailable() -> None:
    directory = FakeUserDirectory(list_error=UserServiceUnavailableError("directory down"))
    client = TestClient(_build_app(directory))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        headers=_identity(),
    )

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.USER_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_update_user_quota_rejects_user_missing_from_directory() -> None:
    service = AdminService(
        user_repo=FakeUserRepository(),
        quota_repo=AsyncMock(),
        file_repo=MagicMock(),
        disk_router=MagicMock(),
        settings=get_settings(),
    )

    with pytest.raises(UserNotFoundError):
        await service.update_user_quota(
            ADMIN_USER_ID,
            USER_B_ID,
            [ADMIN_DIR],
            private_limit_gb=10.0,
        )
