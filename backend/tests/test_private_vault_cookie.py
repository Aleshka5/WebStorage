"""US-GWUS-04: private vault keys on auth_session, not identity headers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.domain.entities.user import User
from app.domain.exceptions import PrivateSessionExpiredError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.private import _get_session_id
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.private_router import router as private_router
from config import get_settings

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
COOKIE_NAME = "auth_session"
SESSION_ID = "opaque-vault-sid"


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _family_user() -> User:
    return User(
        id=USER_ID,
        email="family@example.test",
        role=Role.FAMILY,
        is_active=True,
        created_at=datetime.now(UTC),
    )


def test_get_session_id_uses_auth_session_cookie() -> None:
    request = MagicMock()
    request.cookies = {"access_token": "old-jwt", COOKIE_NAME: SESSION_ID}

    assert _get_session_id(request) == SESSION_ID


def test_get_session_id_missing_cookie_is_unauthorized() -> None:
    request = MagicMock()
    request.cookies = {}

    with pytest.raises(HTTPException) as exc_info:
        _get_session_id(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error_code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.asyncio
async def test_private_file_service_expired_key_is_private_session_expired() -> None:
    from unittest.mock import AsyncMock

    from app.application.private_service import PrivateService

    store = MagicMock()
    store.get_private_key = AsyncMock(return_value=None)
    service = PrivateService(
        session_store=store,
        quota_repo=MagicMock(),
        file_repo=MagicMock(),
    )

    with pytest.raises(PrivateSessionExpiredError):
        await service.get_file_service(USER_ID, SESSION_ID)


def test_private_session_without_cookie_is_unauthorized_not_expired() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(private_router)
    app.dependency_overrides[get_current_user] = _family_user

    client = TestClient(app)
    response = client.get(
        "/api/private/session",
        headers={"X-User-Id": str(USER_ID), "X-Storage-Role": "FAMILY"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == ErrorCode.UNAUTHORIZED
