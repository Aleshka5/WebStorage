from loguru import logger
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.domain.value_objects.error_codes import ErrorCode
from app.infrastructure.session_store import get_session_store

_AUTH_LOGIN_PATH = "/api/auth/login"
_AUTH_REGISTER_PATH = "/api/auth/register"

_AUTH_RATE_LIMITS: dict[str, tuple[int, int]] = {
    _AUTH_LOGIN_PATH: (10, 60),
    _AUTH_REGISTER_PATH: (5, 60),
}


def _client_ip_from_scope(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


class AuthRateLimitMiddleware:
    """Pure ASGI middleware so large file uploads are not buffered in memory.

    Starlette's BaseHTTPMiddleware wraps the request body and is unsafe for
    multipart uploads of large files.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._session_store = get_session_store()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        rate_limit = _AUTH_RATE_LIMITS.get(path)
        if rate_limit is None:
            await self.app(scope, receive, send)
            return

        max_requests, window_seconds = rate_limit
        client_ip = _client_ip_from_scope(scope)
        count, retry_after = await self._session_store.increment_auth_requests(
            path,
            client_ip,
            window_seconds,
        )

        if count > max_requests:
            logger.warning(
                "Auth rate limit exceeded for {} from IP {}: {}/{}",
                path,
                client_ip,
                count,
                max_requests,
            )
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "error_code": ErrorCode.TOO_MANY_ATTEMPTS,
                        "message": "Too many requests",
                        "retry_after": retry_after,
                    },
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
