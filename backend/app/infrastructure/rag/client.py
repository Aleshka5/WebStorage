"""httpx client for the RAG edge index API."""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID, uuid4

import httpx
from loguru import logger

from app.application.ports.rag_index import (
    DOCUMENTS_COLLECTION,
    ERROR_CONTENT_CHANGED,
    ERROR_DEPENDENCY_UNAVAILABLE,
    ERROR_DUPLICATE_FILE,
    ERROR_FILE_BUSY,
    ERROR_TIMEOUT,
    IMAGES_COLLECTION,
    STORAGE_ORIGIN,
    RagDeleteResult,
    RagFileCard,
    RagImageHit,
    RagImageSearchResult,
    RagIndex,
    RagIndexError,
    RagIndexHttpError,
    RagIndexUnavailableError,
    RagManifest,
    RagObjectKey,
)

_DOCUMENTS_PATH = "/api/files/documents"
_IMAGES_PATH = "/api/files/images"
_IMAGE_SEARCH_PATH = "/api/chats/image-search"
_HEALTH_PATH = "/health"
_OUTAGE_STATUSES = frozenset({502, 503, 504})
_DEFAULT_TIMEOUT_SECONDS = 10.0

_COLLECTION_PATHS = {
    DOCUMENTS_COLLECTION: _DOCUMENTS_PATH,
    IMAGES_COLLECTION: _IMAGES_PATH,
}


class RagIndexClient(RagIndex):
    """Calls RAG at a base URL supplied by the caller.

    Sends ``X-Auth-User-Id`` and an optional ``X-Request-Id``. Does not send
    a cookie, ``Authorization``, or ``X-Auth-Role``.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized = _normalize_base_url(base_url)
        if timeout_seconds <= 0:
            logger.error(
                "RAG index client timeout_seconds must be positive timeout_seconds={}",
                timeout_seconds,
            )
            raise RagIndexError("RAG timeout must be positive")
        self._base_url = normalized
        self._timeout_seconds = timeout_seconds
        self._owned_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        logger.info(
            "RAG index client configured base_url={} timeout_seconds={}",
            self._base_url,
            timeout_seconds,
        )

    async def register_document(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        return await self._register(
            DOCUMENTS_COLLECTION,
            "register_document",
            manifest,
            user_id=user_id,
            request_id=request_id,
        )

    async def register_image(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        return await self._register(
            IMAGES_COLLECTION,
            "register_image",
            manifest,
            user_id=user_id,
            request_id=request_id,
        )

    async def get_file(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        response, sent_request_id = await self._send(
            "GET",
            f"/api/files/{rag_file_id}",
            operation="get_file",
            user_id=user_id,
            rag_file_id=rag_file_id,
            request_id=request_id,
            ok_statuses=frozenset({200}),
        )
        card = _parse_file_card(
            response,
            operation="get_file",
            user_id=user_id,
            request_id=sent_request_id,
        )
        logger.info(
            "RAG get_file succeeded user_id={} rag_file_id={} status={} request_id={}",
            user_id,
            card.file_id,
            card.status,
            sent_request_id,
        )
        return card

    async def rekey(
        self,
        rag_file_id: UUID,
        target: RagObjectKey,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        response, sent_request_id = await self._send(
            "PUT",
            f"/api/files/{rag_file_id}/object",
            operation="rekey",
            user_id=user_id,
            rag_file_id=rag_file_id,
            request_id=request_id,
            json_body={
                "s3_key": target.s3_key,
                "file_size": target.file_size,
                "content_sha256": target.content_sha256,
            },
            ok_statuses=frozenset({202}),
        )
        card = _parse_file_card(
            response,
            operation="rekey",
            user_id=user_id,
            request_id=sent_request_id,
        )
        logger.info(
            "RAG rekey succeeded user_id={} rag_file_id={} status={} request_id={}",
            user_id,
            card.file_id,
            card.status,
            sent_request_id,
        )
        return card

    async def delete_vectors(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagDeleteResult:
        response, sent_request_id = await self._send(
            "DELETE",
            f"/api/files/{rag_file_id}",
            operation="delete_vectors",
            user_id=user_id,
            rag_file_id=rag_file_id,
            request_id=request_id,
            ok_statuses=frozenset({202}),
        )
        result = _parse_delete(
            response,
            fallback_file_id=rag_file_id,
            user_id=user_id,
            request_id=sent_request_id,
        )
        logger.info(
            "RAG delete_vectors succeeded user_id={} rag_file_id={} status={} request_id={}",
            user_id,
            result.file_id,
            result.status,
            sent_request_id,
        )
        return result

    async def search_images(
        self,
        q: str,
        *,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        request_id: UUID | None = None,
    ) -> RagImageSearchResult:
        response, sent_request_id = await self._send(
            "GET",
            _IMAGE_SEARCH_PATH,
            operation="search_images",
            user_id=user_id,
            rag_file_id=None,
            request_id=request_id,
            params={"q": q, "limit": limit, "offset": offset},
            ok_statuses=frozenset({200}),
        )
        result = _parse_image_search(
            response,
            user_id=user_id,
            request_id=sent_request_id,
            fallback_limit=limit,
            fallback_offset=offset,
        )
        if not result.items:
            logger.warning(
                "RAG search_images returned no hits user_id={} request_id={} limit={} offset={}",
                user_id,
                sent_request_id,
                result.limit,
                result.offset,
            )
        else:
            logger.info(
                "RAG search_images succeeded user_id={} request_id={} hit_count={}",
                user_id,
                sent_request_id,
                len(result.items),
            )
        return result

    async def health(self, *, request_id: UUID | None = None) -> bool:
        response, sent_request_id = await self._send(
            "GET",
            _HEALTH_PATH,
            operation="health",
            user_id=None,
            rag_file_id=None,
            request_id=request_id,
            ok_statuses=frozenset({200}),
        )
        payload = _json_object(response)
        if payload is None or payload.get("status") != "ok":
            logger.error(
                "RAG health returned an unexpected body request_id={} status={}",
                sent_request_id,
                response.status_code,
            )
            raise RagIndexUnavailableError(
                "RAG health check did not report ok",
                status_code=response.status_code,
                error_code=ERROR_DEPENDENCY_UNAVAILABLE,
            )
        logger.info("RAG health succeeded request_id={}", sent_request_id)
        return True

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()
            logger.info("RAG index HTTP client closed")

    async def _register(
        self,
        collection: str,
        operation: str,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None,
    ) -> RagFileCard:
        path = _COLLECTION_PATHS[collection]
        response, sent_request_id = await self._send(
            "POST",
            path,
            operation=operation,
            user_id=user_id,
            rag_file_id=None,
            request_id=request_id,
            json_body={
                "s3_key": manifest.s3_key,
                "file_name": manifest.file_name,
                "content_sha256": manifest.content_sha256,
                "file_size": manifest.file_size,
                "content_type": manifest.content_type,
                "origin": STORAGE_ORIGIN,
            },
            ok_statuses=frozenset({202}),
        )
        card = _parse_file_card(
            response,
            operation=operation,
            user_id=user_id,
            request_id=sent_request_id,
        )
        logger.info(
            "RAG {} succeeded user_id={} rag_file_id={} status={} request_id={}",
            operation,
            user_id,
            card.file_id,
            card.status,
            sent_request_id,
        )
        return card

    async def _send(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        user_id: UUID | None,
        rag_file_id: UUID | None,
        request_id: UUID | None,
        ok_statuses: frozenset[int],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response, UUID]:
        sent_request_id = request_id or uuid4()
        headers = {"X-Request-Id": str(sent_request_id)}
        if user_id is not None:
            headers["X-Auth-User-Id"] = str(user_id)
        url = f"{self._base_url}{path}"
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self._timeout_seconds,
        }
        if json_body is not None:
            request_kwargs["json"] = json_body
        if params is not None:
            request_kwargs["params"] = params
        try:
            response = await self._client.request(method, url, **request_kwargs)
        except httpx.TimeoutException as exc:
            logger.error(
                "RAG {} timed out user_id={} rag_file_id={} request_id={}",
                operation,
                _log_id(user_id),
                _log_id(rag_file_id),
                sent_request_id,
            )
            raise RagIndexUnavailableError(
                "RAG request timed out",
                error_code=ERROR_TIMEOUT,
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "RAG {} is unreachable user_id={} rag_file_id={} request_id={}",
                operation,
                _log_id(user_id),
                _log_id(rag_file_id),
                sent_request_id,
            )
            raise RagIndexUnavailableError(
                "RAG is unreachable",
                error_code=ERROR_DEPENDENCY_UNAVAILABLE,
            ) from exc

        if response.status_code in _OUTAGE_STATUSES:
            error_code, _details = _error_parts(response)
            resolved = error_code or _outage_code(response.status_code)
            logger.error(
                "RAG {} unavailable user_id={} rag_file_id={} status={} error_code={} request_id={}",
                operation,
                _log_id(user_id),
                _log_id(rag_file_id),
                response.status_code,
                resolved,
                sent_request_id,
            )
            raise RagIndexUnavailableError(
                f"RAG returned status {response.status_code}",
                status_code=response.status_code,
                error_code=resolved,
            )

        if response.status_code not in ok_statuses:
            error_code, details = _error_parts(response)
            self._log_http_failure(
                operation=operation,
                user_id=user_id,
                rag_file_id=rag_file_id,
                status_code=response.status_code,
                error_code=error_code,
                request_id=sent_request_id,
            )
            raise RagIndexHttpError(
                f"RAG returned status {response.status_code}",
                status_code=response.status_code,
                error_code=error_code,
                details=details,
            )

        return response, sent_request_id

    def _log_http_failure(
        self,
        *,
        operation: str,
        user_id: UUID | None,
        rag_file_id: UUID | None,
        status_code: int,
        error_code: str | None,
        request_id: UUID,
    ) -> None:
        expected = _is_expected_http_outcome(operation, status_code, error_code)
        log = logger.warning if expected else logger.error
        log(
            "RAG {} failed user_id={} rag_file_id={} status={} error_code={} request_id={}",
            operation,
            _log_id(user_id),
            _log_id(rag_file_id),
            status_code,
            error_code,
            request_id,
        )


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    if not normalized:
        logger.error("RAG index client base URL is empty")
        raise RagIndexError("RAG base URL is empty")
    return normalized


def _log_id(value: UUID | None) -> str:
    if value is None:
        return "-"
    return str(value)


def _outage_code(status_code: int) -> str:
    if status_code == 504:
        return ERROR_TIMEOUT
    return ERROR_DEPENDENCY_UNAVAILABLE


def _is_expected_http_outcome(operation: str, status_code: int, error_code: str | None) -> bool:
    if operation == "delete_vectors" and (
        status_code == 404 or (status_code == 409 and error_code == ERROR_FILE_BUSY)
    ):
        return True
    if operation == "get_file" and status_code == 404:
        return True
    if operation in {"register_document", "register_image"} and (
        status_code == 409 and error_code == ERROR_DUPLICATE_FILE
    ):
        return True
    return operation == "rekey" and status_code == 409 and error_code == ERROR_CONTENT_CHANGED


def _error_parts(response: httpx.Response) -> tuple[str | None, dict[str, Any]]:
    payload = _json_object(response)
    if payload is None:
        return None, {}
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, {}
    code = error.get("code")
    details = error.get("details")
    return (
        code if isinstance(code, str) and code else None,
        dict(details) if isinstance(details, dict) else {},
    )


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _parse_file_card(
    response: httpx.Response,
    *,
    operation: str,
    user_id: UUID,
    request_id: UUID,
) -> RagFileCard:
    payload = _json_object(response)
    if payload is None:
        _raise_unreadable(
            operation=operation,
            user_id=user_id,
            rag_file_id=None,
            status_code=response.status_code,
            request_id=request_id,
        )
    try:
        file_id = _require_uuid(payload.get("file_id"))
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("status")
        return RagFileCard(
            file_id=file_id,
            status=status,
            error=_optional_str(payload.get("error")),
            content_type=_optional_str(payload.get("content_type")),
            file_name=_optional_str(payload.get("file_name")),
            file_size=_optional_int(payload.get("file_size")),
            page_count=_optional_int(payload.get("page_count")),
        )
    except ValueError:
        _raise_unreadable(
            operation=operation,
            user_id=user_id,
            rag_file_id=None,
            status_code=response.status_code,
            request_id=request_id,
        )


def _parse_delete(
    response: httpx.Response,
    *,
    fallback_file_id: UUID,
    user_id: UUID,
    request_id: UUID,
) -> RagDeleteResult:
    payload = _json_object(response)
    if payload is None:
        _raise_unreadable(
            operation="delete_vectors",
            user_id=user_id,
            rag_file_id=fallback_file_id,
            status_code=response.status_code,
            request_id=request_id,
        )
    try:
        raw_id = payload.get("file_id")
        file_id = _require_uuid(raw_id) if raw_id is not None else fallback_file_id
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("status")
        return RagDeleteResult(file_id=file_id, status=status)
    except ValueError:
        _raise_unreadable(
            operation="delete_vectors",
            user_id=user_id,
            rag_file_id=fallback_file_id,
            status_code=response.status_code,
            request_id=request_id,
        )


def _parse_image_search(
    response: httpx.Response,
    *,
    user_id: UUID,
    request_id: UUID,
    fallback_limit: int,
    fallback_offset: int,
) -> RagImageSearchResult:
    payload = _json_object(response)
    if payload is None:
        _raise_unreadable(
            operation="search_images",
            user_id=user_id,
            rag_file_id=None,
            status_code=response.status_code,
            request_id=request_id,
        )
    try:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("items")
        items = tuple(_parse_hit(item) for item in raw_items)
        limit = _optional_int(payload.get("limit"))
        offset = _optional_int(payload.get("offset"))
        if "next_offset" in payload and payload.get("next_offset") is not None:
            next_offset = _optional_int(payload.get("next_offset"))
        else:
            next_offset = None
        return RagImageSearchResult(
            items=items,
            limit=fallback_limit if limit is None else limit,
            offset=fallback_offset if offset is None else offset,
            next_offset=next_offset,
        )
    except ValueError:
        _raise_unreadable(
            operation="search_images",
            user_id=user_id,
            rag_file_id=None,
            status_code=response.status_code,
            request_id=request_id,
        )


def _parse_hit(item: object) -> RagImageHit:
    if not isinstance(item, dict):
        raise ValueError("hit")
    file_name = item.get("file_name")
    if not isinstance(file_name, str):
        raise ValueError("file_name")
    score = item.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score")
    return RagImageHit(file_id=_require_uuid(item.get("file_id")), file_name=file_name, score=float(score))


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("uuid")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError("uuid") from exc


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("str")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("int")
    return value


def _raise_unreadable(
    *,
    operation: str,
    user_id: UUID | None,
    rag_file_id: UUID | None,
    status_code: int,
    request_id: UUID,
) -> NoReturn:
    logger.error(
        "RAG {} returned an unreadable body user_id={} rag_file_id={} status={} request_id={}",
        operation,
        _log_id(user_id),
        _log_id(rag_file_id),
        status_code,
        request_id,
    )
    raise RagIndexHttpError(
        "RAG returned an unreadable body",
        status_code=status_code,
        error_code=None,
    )
