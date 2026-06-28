from functools import lru_cache

import redis.asyncio as aioredis
from loguru import logger

from config import get_settings

_PRIVATE_KEY_PREFIX = "private_key:"
_OAUTH_STATE_PREFIX = "oauth_state:"
_OAUTH_TICKET_PREFIX = "oauth_ticket:"
_OAUTH_STATE_TTL_SECONDS = 600
_OAUTH_TICKET_TTL_SECONDS = 120
_AUTH_RATE_LIMIT_PREFIX = "auth_rate:"
_UNLOCK_ATTEMPTS_PREFIX = "unlock_attempts:"
UNLOCK_RATE_LIMIT_MAX_ATTEMPTS = 5
UNLOCK_RATE_LIMIT_TTL_SECONDS = 900
_UNLOCK_MAX_ATTEMPTS = UNLOCK_RATE_LIMIT_MAX_ATTEMPTS
_UNLOCK_TTL_SECONDS = UNLOCK_RATE_LIMIT_TTL_SECONDS


class SessionStore:
    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        logger.info("SessionStore connected to Redis")

    async def set_private_key(self, session_id: str, key: str, ttl: int) -> None:
        redis_key = f"{_PRIVATE_KEY_PREFIX}{session_id}"
        await self._redis.setex(redis_key, ttl, key)
        logger.info("Stored private key for session {} with TTL {} seconds", session_id, ttl)

    async def get_private_key(self, session_id: str) -> str | None:
        redis_key = f"{_PRIVATE_KEY_PREFIX}{session_id}"
        value = await self._redis.get(redis_key)
        if value is None:
            logger.warning("Private key not found for session {}", session_id)
        return value

    async def delete_private_key(self, session_id: str) -> None:
        redis_key = f"{_PRIVATE_KEY_PREFIX}{session_id}"
        await self._redis.delete(redis_key)
        logger.info("Deleted private key for session {}", session_id)

    async def get_private_key_ttl(self, session_id: str) -> int:
        redis_key = f"{_PRIVATE_KEY_PREFIX}{session_id}"
        ttl = await self._redis.ttl(redis_key)
        if ttl < 0:
            return 0
        return ttl

    async def increment_unlock_attempts(self, user_id: str, client_ip: str) -> int:
        redis_key = f"{_UNLOCK_ATTEMPTS_PREFIX}{user_id}:{client_ip}"
        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, _UNLOCK_TTL_SECONDS)
        logger.warning(
            "Unlock attempt {} for user {} from IP {}",
            count,
            user_id,
            client_ip,
        )
        return count

    async def reset_unlock_attempts(self, user_id: str, client_ip: str) -> None:
        redis_key = f"{_UNLOCK_ATTEMPTS_PREFIX}{user_id}:{client_ip}"
        await self._redis.delete(redis_key)
        logger.info("Reset unlock attempts for user {} from IP {}", user_id, client_ip)

    async def get_unlock_attempts(self, user_id: str, client_ip: str) -> int:
        redis_key = f"{_UNLOCK_ATTEMPTS_PREFIX}{user_id}:{client_ip}"
        value = await self._redis.get(redis_key)
        return int(value) if value is not None else 0

    async def increment_auth_requests(
        self,
        endpoint: str,
        client_ip: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        redis_key = f"{_AUTH_RATE_LIMIT_PREFIX}{endpoint}:{client_ip}"
        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, window_seconds)
        ttl = await self._redis.ttl(redis_key)
        retry_after = ttl if ttl > 0 else window_seconds
        logger.info(
            "Auth request {} for endpoint {} from IP {} (window {}s)",
            count,
            endpoint,
            client_ip,
            window_seconds,
        )
        return count, retry_after

    async def store_oauth_state(self, state: str) -> None:
        redis_key = f"{_OAUTH_STATE_PREFIX}{state}"
        await self._redis.setex(redis_key, _OAUTH_STATE_TTL_SECONDS, "1")
        logger.info("Stored OAuth state with TTL {} seconds", _OAUTH_STATE_TTL_SECONDS)

    async def consume_oauth_state(self, state: str) -> bool:
        redis_key = f"{_OAUTH_STATE_PREFIX}{state}"
        deleted = await self._redis.delete(redis_key)
        if deleted:
            logger.info("OAuth state consumed successfully")
            return True
        logger.warning("OAuth state not found or already consumed")
        return False

    async def store_oauth_ticket(self, ticket: str, access_token: str) -> None:
        redis_key = f"{_OAUTH_TICKET_PREFIX}{ticket}"
        await self._redis.setex(redis_key, _OAUTH_TICKET_TTL_SECONDS, access_token)
        logger.info("Stored OAuth login ticket with TTL {} seconds", _OAUTH_TICKET_TTL_SECONDS)

    async def consume_oauth_ticket(self, ticket: str) -> str | None:
        redis_key = f"{_OAUTH_TICKET_PREFIX}{ticket}"
        value = await self._redis.getdel(redis_key)
        if value is None:
            logger.warning("OAuth login ticket not found or already consumed")
            return None
        logger.info("OAuth login ticket consumed successfully")
        return value

    async def close(self) -> None:
        await self._redis.aclose()
        logger.info("SessionStore Redis connection closed")


@lru_cache
def get_session_store() -> SessionStore:
    settings = get_settings()
    return SessionStore(settings.cache_db.redis_url)
