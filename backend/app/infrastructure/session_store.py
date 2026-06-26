from functools import lru_cache

import redis.asyncio as aioredis
from loguru import logger

from config import get_settings

_PRIVATE_KEY_PREFIX = "private_key:"


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

    async def close(self) -> None:
        await self._redis.aclose()
        logger.info("SessionStore Redis connection closed")


@lru_cache
def get_session_store() -> SessionStore:
    settings = get_settings()
    return SessionStore(settings.cache_db.redis_url)
