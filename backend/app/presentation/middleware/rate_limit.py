from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.session_store import get_session_store

_AUTH_LOGIN_PATH = "/api/auth/login"
_AUTH_REGISTER_PATH = "/api/auth/register"

_AUTH_RATE_LIMITS: dict[str, tuple[int, int]] = {
    _AUTH_LOGIN_PATH: (10, 60),
    _AUTH_REGISTER_PATH: (5, 60),
}


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._session_store = get_session_store()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST":
            return await call_next(request)

        rate_limit = _AUTH_RATE_LIMITS.get(request.url.path)
        if rate_limit is None:
            return await call_next(request)

        max_requests, window_seconds = rate_limit
        client_ip = _client_ip(request)
        count, retry_after = await self._session_store.increment_auth_requests(
            request.url.path,
            client_ip,
            window_seconds,
        )

        if count > max_requests:
            logger.warning(
                "Auth rate limit exceeded for {} from IP {}: {}/{}",
                request.url.path,
                client_ip,
                count,
                max_requests,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "error_code": ErrorCode.TOO_MANY_ATTEMPTS,
                        "message": "Too many requests",
                        "retry_after": retry_after,
                    },
                },
            )

        return await call_next(request)