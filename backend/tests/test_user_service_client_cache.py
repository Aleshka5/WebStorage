"""User-Service HTTP client cache and fail-closed mapping."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from app.domain.exceptions import UserServiceUnavailableError
from app.domain.value_objects.role import Role
from app.infrastructure.user_service.client import UserServiceClient
from config import get_settings

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler: httpx.MockTransport) -> UserServiceClient:
    http = httpx.AsyncClient(transport=handler, base_url="http://user_service:8000")
    return UserServiceClient(get_settings(), http_client=http)


@pytest.mark.asyncio
async def test_storage_role_cache_hit_skips_second_http_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"user_id": str(USER_ID), "service": "storage", "role": "FAMILY"},
        )

    client = _client(httpx.MockTransport(handler))
    first = await client.get_storage_role(USER_ID)
    second = await client.get_storage_role(USER_ID)

    assert first is Role.FAMILY
    assert second is Role.FAMILY
    assert calls["n"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_storage_role_stranger_from_user_service_is_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user_id": str(USER_ID), "service": "storage", "role": "STRANGER"},
        )

    client = _client(httpx.MockTransport(handler))
    role = await client.get_storage_role(USER_ID)
    assert role is Role.STRANGER
    await client.aclose()


@pytest.mark.asyncio
async def test_storage_role_5xx_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "INTERNAL_ERROR"}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(UserServiceUnavailableError):
        await client.get_storage_role(USER_ID)
    await client.aclose()


@pytest.mark.asyncio
async def test_storage_role_404_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(UserServiceUnavailableError):
        await client.get_storage_role(USER_ID)
    await client.aclose()


@pytest.mark.asyncio
async def test_list_users_omitted_storage_service_is_stranger() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "users": [
                    {
                        "id": str(USER_ID),
                        "google_sub": "110000000000000000001",
                        "email": "ada@example.com",
                        "username": "ada",
                        "global_role": "ADMIN",
                        "services": [],
                        "created_at": "2026-08-29T11:00:00Z",
                        "updated_at": "2026-08-29T11:00:00Z",
                    }
                ]
            },
        )

    client = _client(httpx.MockTransport(handler))
    users = await client.list_users()
    assert len(users) == 1
    assert users[0].storage_role is Role.STRANGER
    assert users[0].email == "ada@example.com"
    await client.aclose()
