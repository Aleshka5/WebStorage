from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.private_service import PrivateService
from app.domain.entities.user import User
from app.domain.exceptions import QuotaExceededError
from app.domain.value_objects.role import Role
from app.domain.value_objects.storage_quota import StorageQuota
from app.infrastructure.database.models import UserQuotaUsage
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.file_router import _ensure_upload_quota
from app.presentation.routers.private_router import _ensure_private_upload_quota
from app.presentation.routers.quota_router import router as quota_router
from config import get_settings

USER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
DEFAULT_LIMIT_BYTES = 100 * 1024 * 1024


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _family_user() -> User:
    return User(
        id=USER_ID,
        email="family@example.test",
        password_hash=None,
        google_id=None,
        role=Role.FAMILY,
        is_active=True,
        created_at=datetime.now(UTC),
    )


class FakeQuotaRepo:
    def __init__(self, usage: SimpleNamespace) -> None:
        self.usage = usage

    async def get_by_user_id(self, user_id: UUID) -> SimpleNamespace:
        return self.usage


def test_default_user_quota_mb_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFAULT_USER_QUOTA_MB", raising=False)
    monkeypatch.delenv("STRANGER_QUOTA_MB", raising=False)

    settings = get_settings()

    assert settings.business_logic.default_user_quota_mb == 100
    assert settings.business_logic.default_user_quota_bytes == DEFAULT_LIMIT_BYTES


def test_default_user_quota_mb_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_USER_QUOTA_MB", "250")

    settings = get_settings()

    assert settings.business_logic.default_user_quota_mb == 250


def test_stranger_quota_mb_is_deprecated_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFAULT_USER_QUOTA_MB", raising=False)
    monkeypatch.setenv("STRANGER_QUOTA_MB", "80")

    settings = get_settings()

    assert settings.business_logic.default_user_quota_mb == 80


def test_storage_quota_unlimited_does_not_exceed() -> None:
    quota = StorageQuota(used_bytes=10_000, limit_bytes=0)

    assert quota.is_unlimited() is True
    assert quota.is_exceeded() is False
    assert quota.would_exceed(1_000_000) is False


def test_storage_quota_would_exceed_at_limit() -> None:
    quota = StorageQuota(used_bytes=90, limit_bytes=100)

    assert quota.would_exceed(11) is True
    assert quota.would_exceed(10) is False


@pytest.mark.asyncio
async def test_get_by_user_id_creates_row_with_default_limit() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    repo = QuotaRepository(session)

    usage = await repo.get_by_user_id(USER_ID)

    created = session.add.call_args[0][0]
    assert isinstance(created, UserQuotaUsage)
    assert created.limit_bytes == DEFAULT_LIMIT_BYTES
    assert usage.limit_bytes == DEFAULT_LIMIT_BYTES


@pytest.mark.asyncio
async def test_ensure_upload_quota_rejects_over_limit() -> None:
    usage = SimpleNamespace(total_bytes=90, limit_bytes=100)
    repo = FakeQuotaRepo(usage)

    with pytest.raises(QuotaExceededError) as exc_info:
        await _ensure_upload_quota(USER_ID, 20, repo)

    assert exc_info.value.available_bytes == 10


@pytest.mark.asyncio
async def test_ensure_upload_quota_allows_unlimited() -> None:
    usage = SimpleNamespace(total_bytes=90, limit_bytes=0)
    repo = FakeQuotaRepo(usage)

    quota = await _ensure_upload_quota(USER_ID, 1_000_000, repo)

    assert quota.is_unlimited() is True


@pytest.mark.asyncio
async def test_private_upload_rejected_when_total_exceeded() -> None:
    usage = SimpleNamespace(total_bytes=90, limit_bytes=100)
    repo = FakeQuotaRepo(usage)
    private_service = AsyncMock(spec=PrivateService)
    private_service.get_quota.return_value = {
        "private_bytes": 0,
        "private_limit_bytes": 10 * 1024 * 1024 * 1024,
    }

    with pytest.raises(QuotaExceededError) as exc_info:
        await _ensure_private_upload_quota(USER_ID, 20, private_service, repo)

    assert exc_info.value.available_bytes == 10
    private_service.get_quota.assert_not_awaited()


def test_get_quota_me_returns_stored_limit_for_family() -> None:
    usage = SimpleNamespace(
        total_bytes=2048,
        limit_bytes=50 * 1024 * 1024,
        private_bytes=0,
        private_limit_bytes=0,
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(quota_router)
    app.dependency_overrides[get_current_user] = _family_user
    app.dependency_overrides[get_quota_repository] = lambda: FakeQuotaRepo(usage)

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = fake_session
    client = TestClient(app)

    response = client.get("/api/quota/me")

    assert response.status_code == 200
    body = response.json()
    assert body["used_bytes"] == 2048
    assert body["limit_bytes"] == 50 * 1024 * 1024
    assert body["private_bytes"] == 0
    assert body["private_limit_bytes"] == 0
