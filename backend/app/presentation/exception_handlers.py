from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

from app.domain.exceptions import (
    AccessDeniedError,
    FileNotFoundError,
    PathTraversalError,
    PrivateSessionExpiredError,
    QuotaExceededError,
    StorageUnavailableError,
)
from app.domain.value_objects.error_codes import ErrorCode


def _error_response(status_code: int, detail: dict) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StorageUnavailableError)
    async def storage_unavailable_handler(
        _request: Request,
        exc: StorageUnavailableError,
    ) -> JSONResponse:
        logger.warning("Storage unavailable: {}", exc)
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "error_code": ErrorCode.DISK_UNAVAILABLE,
                "message": str(exc),
            },
        )

    @app.exception_handler(QuotaExceededError)
    async def quota_exceeded_handler(
        _request: Request,
        exc: QuotaExceededError,
    ) -> JSONResponse:
        logger.warning("Quota exceeded: {}", exc)
        detail: dict = {
            "error_code": ErrorCode.QUOTA_EXCEEDED,
            "message": str(exc),
        }
        if exc.available_bytes is not None:
            detail["available_bytes"] = exc.available_bytes
        return _error_response(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail)

    @app.exception_handler(PathTraversalError)
    async def path_traversal_handler(
        _request: Request,
        exc: PathTraversalError,
    ) -> JSONResponse:
        logger.warning("Path traversal detected: {}", exc)
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            {
                "error_code": ErrorCode.PATH_TRAVERSAL_DETECTED,
                "message": str(exc),
            },
        )

    @app.exception_handler(PrivateSessionExpiredError)
    async def private_session_expired_handler(
        _request: Request,
        exc: PrivateSessionExpiredError,
    ) -> JSONResponse:
        logger.warning("Private session expired: {}", exc)
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            {
                "error_code": ErrorCode.PRIVATE_SESSION_EXPIRED,
                "message": str(exc),
            },
        )

    @app.exception_handler(FileNotFoundError)
    async def file_not_found_handler(
        _request: Request,
        exc: FileNotFoundError,
    ) -> JSONResponse:
        logger.warning("File not found: {}", exc)
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            {
                "error_code": ErrorCode.FILE_NOT_FOUND,
                "message": str(exc),
            },
        )

    @app.exception_handler(AccessDeniedError)
    async def access_denied_handler(
        _request: Request,
        exc: AccessDeniedError,
    ) -> JSONResponse:
        logger.warning("Access denied: {}", exc)
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            {
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc),
            },
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(
        _request: Request,
        exc: PermissionError,
    ) -> JSONResponse:
        logger.warning("Permission denied: {}", exc)
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            {
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc) or "Access denied",
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled exception for {} {}",
            request.method,
            request.url.path,
        )
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {
                "error_code": ErrorCode.INTERNAL_ERROR,
                "message": "Internal server error",
            },
        )
