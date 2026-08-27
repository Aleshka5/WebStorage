from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.admin_service import AdminService
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.user import User
from app.domain.exceptions import AuthUnavailableError, UserNotFoundError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.user_repo import UserAdminRow
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.admin import get_admin_service
from app.presentation.dependencies.auth import get_auth_validator, get_user_repository
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.admin_router import router as admin_router
from config import get_settings

ADMIN_USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SESSION_ID = "opaque-session-id-not-for-logs"
COOKIE_NAME = "auth_session"

ADMIN_PRINCIPAL = AuthPrincipal(
    id=ADMIN_USER_ID,
    email="admin@example.test",
    name="Admin User",
    role=Role.ADMIN,
)
FAMILY_PRINCIPAL = AuthPrincipal(
    id=UUID("11111111-1111-1111-1111-111111111111"),
    email="family@example.test",
    name="Family User",
    role=Role.FAMILY,
)
USER_B_FAMILY = AuthPrincipal(
    id=USER_B_ID,
    email="user-b@example.test",
    name="User B",
    role=Role.FAMILY,
)
USER_B_STRANGER = AuthPrincipal(
    id=USER_B_ID,
    email="user-b@example.test",
    name="User B",
    role=Role.STRANGER,
)


class FakeAuthValidator:
    def __init__(
        self,
        *,
        principal: AuthPrincipal | None = None,
        error: Exception | None = None,
        listed: list[AuthPrincipal] | None = None,
        list_users_error: Exception | None = None,
    ) -> None:
        self.principal = principal
        self.error = error
        self.listed = listed if listed is not None else []
        self.list_users_error = list_users_error
        self.validate_calls: list[tuple[str, str]] = []
        self.list_users_calls: list[tuple[str, str]] = []

    async def validate(self, session_id: str, caller_host: str) -> AuthPrincipal:
        self.validate_calls.append((session_id, caller_host))
        if self.error is not None:
            raise self.error
        assert self.principal is not None
        return self.principal

    async def list_users(self, session_id: str, caller_host: str) -> list[AuthPrincipal]:
        self.list_users_calls.append((session_id, caller_host))
        if self.list_users_error is not None:
            raise self.list_users_error
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
            password_hash=None,
            google_id=None,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )

    async def get_admin_rows_by_ids(self, user_ids: list[UUID]) -> dict[UUID, UserAdminRow]:
        return {user_id: self.admin_rows[user_id] for user_id in user_ids if user_id in self.admin_rows}


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
    quota_repo: AsyncMock | None = None,
) -> FastAPI:
    repo = user_repo or FakeUserRepository()
    quotas = quota_repo or AsyncMock()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_router)
    app.dependency_overrides[get_auth_validator] = lambda: validator
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


def test_admin_list_users_uses_live_list_users_roles() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    user_repo = FakeUserRepository()
    user_repo.admin_rows[USER_B_ID] = UserAdminRow(
        user=User(
            id=USER_B_ID,
            email="stale-local@example.test",
            password_hash=None,
            google_id=None,
            role=Role.ADMIN,
            is_active=True,
            created_at=datetime.now(UTC),
        ),
        quota_used_bytes=1024,
        limit_bytes=50 * 1024 * 1024,
        private_limit_bytes=2048,
    )
    client = TestClient(_build_app(validator, user_repo=user_repo))

    response = client.get("/api/admin/users", cookies={COOKIE_NAME: SESSION_ID})

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
    assert len(validator.list_users_calls) == 1
    assert validator.list_users_calls == [
        (SESSION_ID, get_settings().auth_grpc.caller_host),
    ]


def test_admin_list_users_reloads_live_role_after_stub_change() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    client = TestClient(_build_app(validator))

    first = client.get("/api/admin/users", cookies={COOKIE_NAME: SESSION_ID})
    assert first.status_code == 200
    first_by_id = {item["id"]: item for item in first.json()["items"]}
    assert first_by_id[str(USER_B_ID)]["role"] == Role.FAMILY.value

    validator.listed = [ADMIN_PRINCIPAL, USER_B_STRANGER]
    second = client.get("/api/admin/users", cookies={COOKIE_NAME: SESSION_ID})

    assert second.status_code == 200
    second_by_id = {item["id"]: item for item in second.json()["items"]}
    assert second_by_id[str(USER_B_ID)]["role"] == Role.STRANGER.value
    assert len(validator.list_users_calls) == 2


def test_patch_user_role_is_gone() -> None:
    validator = FakeAuthValidator(principal=ADMIN_PRINCIPAL, listed=[ADMIN_PRINCIPAL])
    client = TestClient(_build_app(validator))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/role",
        json={"role": "STRANGER"},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 410
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert validator.list_users_calls == []


def test_admin_list_users_unavailable_is_auth_unavailable() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        list_users_error=AuthUnavailableError("auth down"),
    )
    client = TestClient(_build_app(validator))

    response = client.get("/api/admin/users", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.AUTH_UNAVAILABLE
    assert len(validator.list_users_calls) == 1


def test_family_cannot_list_admin_users() -> None:
    validator = FakeAuthValidator(
        principal=FAMILY_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    client = TestClient(_build_app(validator))

    response = client.get("/api/admin/users", cookies={COOKIE_NAME: SESSION_ID})

    assert response.status_code == 403
    assert _error_code(response) == ErrorCode.ACCESS_DENIED
    assert len(validator.validate_calls) == 1
    assert validator.list_users_calls == []


def test_patch_quota_creates_local_projection_for_auth_user() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(validator, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 204
    assert user_repo.principals[-1].id == USER_B_ID
    quota_repo.update_private_limit.assert_awaited_once_with(USER_B_ID, 10 * 1024 * 1024 * 1024)
    quota_repo.update_limit.assert_not_called()
    assert validator.list_users_calls == [
        (SESSION_ID, get_settings().auth_grpc.caller_host),
    ]


def test_patch_quota_limit_mb_only() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(validator, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"limit_mb": 500},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 204
    quota_repo.update_limit.assert_awaited_once_with(USER_B_ID, 500 * 1024 * 1024)
    quota_repo.update_private_limit.assert_not_called()


def test_patch_quota_both_limits() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(validator, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"limit_mb": 250, "private_limit_gb": 2.0},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 204
    quota_repo.update_limit.assert_awaited_once_with(USER_B_ID, 250 * 1024 * 1024)
    quota_repo.update_private_limit.assert_awaited_once_with(USER_B_ID, 2 * 1024 * 1024 * 1024)


def test_patch_quota_empty_body_is_unprocessable() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        listed=[ADMIN_PRINCIPAL, USER_B_FAMILY],
    )
    client = TestClient(_build_app(validator))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 422


def test_patch_quota_unknown_auth_user_is_not_found() -> None:
    validator = FakeAuthValidator(principal=ADMIN_PRINCIPAL, listed=[ADMIN_PRINCIPAL])
    user_repo = FakeUserRepository()
    quota_repo = AsyncMock()
    client = TestClient(_build_app(validator, user_repo=user_repo, quota_repo=quota_repo))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 404
    assert _error_code(response) == ErrorCode.USER_NOT_FOUND
    assert all(principal.id != USER_B_ID for principal in user_repo.principals)
    quota_repo.update_private_limit.assert_not_called()


def test_patch_quota_list_users_unavailable_is_auth_unavailable() -> None:
    validator = FakeAuthValidator(
        principal=ADMIN_PRINCIPAL,
        list_users_error=AuthUnavailableError("auth down"),
    )
    client = TestClient(_build_app(validator))

    response = client.patch(
        f"/api/admin/users/{USER_B_ID}/quota",
        json={"private_limit_gb": 10.0},
        cookies={COOKIE_NAME: SESSION_ID},
    )

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.AUTH_UNAVAILABLE


@pytest.mark.asyncio
async def test_update_user_quota_rejects_user_missing_from_list_users() -> None:
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
            [ADMIN_PRINCIPAL],
            private_limit_gb=10.0,
        )
