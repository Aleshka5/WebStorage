from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx
from loguru import logger

from app.application.ports.user_directory import (
    STORAGE_SERVICE_KEY,
    DirectoryUser,
    UserDirectory,
)
from app.domain.exceptions import UserServiceUnavailableError
from app.domain.value_objects.role import Role
from config import Settings, get_settings


class _TtlCache:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)


def _parse_role(raw: object) -> Role:
    if not isinstance(raw, str):
        raise UserServiceUnavailableError("User-Service returned a non-string role")
    try:
        return Role(raw)
    except ValueError as exc:
        raise UserServiceUnavailableError(f"User-Service returned invalid role {raw!r}") from exc


def _storage_role_from_services(services: object) -> Role:
    if not isinstance(services, list):
        return Role.STRANGER
    for item in services:
        if not isinstance(item, dict):
            continue
        if item.get("service") == STORAGE_SERVICE_KEY:
            return _parse_role(item.get("role"))
    return Role.STRANGER


class UserServiceClient(UserDirectory):
    """HTTP client for User-Service. Network trust only; no JWT."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owned_client = http_client is None
        timeout = self._settings.user_service.timeout_ms / 1000.0
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._cache = _TtlCache(self._settings.user_service.cache_ttl_seconds)
        self._base = self._settings.user_service.url.rstrip("/")
        logger.info(
            "User-Service client configured base_url={} timeout_ms={} cache_ttl_seconds={}",
            self._base,
            self._settings.user_service.timeout_ms,
            self._settings.user_service.cache_ttl_seconds,
        )

    async def get_storage_role(self, user_id: UUID) -> Role:
        cache_key = f"storage_role:{user_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("User-Service storage role cache hit user_id={}", user_id)
            return cached

        path = f"/users/{user_id}/roles/{STORAGE_SERVICE_KEY}"
        payload = await self._get_json(path, user_id)
        role = _parse_role(payload.get("role"))
        self._cache.set(cache_key, role)
        logger.info(
            "User-Service storage role fetched user_id={} role={}",
            user_id,
            role.value,
        )
        return role

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        cache_key = f"user:{user_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("User-Service user cache hit user_id={}", user_id)
            return cached

        payload = await self._get_json(f"/users/{user_id}", user_id)
        directory_user = self._user_from_payload(payload, default_role=Role.STRANGER)
        self._cache.set(cache_key, directory_user)
        logger.info("User-Service user fetched user_id={}", user_id)
        return directory_user

    async def list_users(self) -> list[DirectoryUser]:
        payload = await self._get_json("/users", None)
        raw_users = payload.get("users")
        if not isinstance(raw_users, list):
            logger.error("User-Service GET /users returned a payload without a users array")
            raise UserServiceUnavailableError("User-Service list users payload is invalid")

        users: list[DirectoryUser] = []
        for item in raw_users:
            if not isinstance(item, dict):
                logger.error("User-Service GET /users contained a non-object item")
                raise UserServiceUnavailableError("User-Service list users payload is invalid")
            users.append(
                self._user_from_payload(
                    item,
                    default_role=_storage_role_from_services(item.get("services")),
                )
            )
        logger.info("User-Service listed {} directory users", len(users))
        return users

    def _user_from_payload(self, payload: dict[str, Any], *, default_role: Role) -> DirectoryUser:
        raw_id = payload.get("id")
        email = payload.get("email")
        username = payload.get("username") or ""
        if not isinstance(raw_id, str) or not isinstance(email, str) or not email:
            raise UserServiceUnavailableError("User-Service user payload is missing id or email")
        try:
            user_id = UUID(raw_id)
        except ValueError as exc:
            raise UserServiceUnavailableError("User-Service user payload has an invalid id") from exc
        return DirectoryUser(
            id=user_id,
            email=email,
            username=username if isinstance(username, str) else "",
            storage_role=default_role,
        )

    async def _get_json(self, path: str, user_id: UUID | None) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            response = await self._client.get(url)
        except httpx.TimeoutException as exc:
            logger.error(
                "User-Service request timed out path={} user_id={}",
                path,
                user_id,
            )
            raise UserServiceUnavailableError("User-Service request timed out") from exc
        except httpx.HTTPError as exc:
            logger.error(
                "User-Service network error path={} user_id={}",
                path,
                user_id,
            )
            raise UserServiceUnavailableError("User-Service is unreachable") from exc

        if response.status_code >= 500 or response.status_code == 404:
            logger.error(
                "User-Service lookup failed path={} user_id={} status={}",
                path,
                user_id,
                response.status_code,
            )
            raise UserServiceUnavailableError(
                f"User-Service returned status {response.status_code}"
            )
        if response.status_code != 200:
            logger.error(
                "User-Service unexpected status path={} user_id={} status={}",
                path,
                user_id,
                response.status_code,
            )
            raise UserServiceUnavailableError(
                f"User-Service returned status {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("User-Service returned non-JSON path={} user_id={}", path, user_id)
            raise UserServiceUnavailableError("User-Service returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise UserServiceUnavailableError("User-Service returned a non-object JSON body")
        return payload

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
            logger.info("User-Service HTTP client closed")
