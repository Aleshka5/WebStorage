"""RAG index HTTP client: register, poll, re-key, delete, and image search."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import httpx
import pytest
from loguru import logger

from app.application.ports.rag_index import (
    ERROR_CONTENT_CHANGED,
    ERROR_DEPENDENCY_UNAVAILABLE,
    ERROR_DUPLICATE_FILE,
    ERROR_FILE_BUSY,
    ERROR_TIMEOUT,
    STORAGE_ORIGIN,
    RagFileCard,
    RagIndexError,
    RagIndexHttpError,
    RagIndexUnavailableError,
    RagManifest,
    RagObjectKey,
)
from app.infrastructure.rag.client import RagIndexClient

USER_ID = UUID("91a0b1c2-3d4e-4f50-8617-18293a4b5c6d")
FILE_ID = UUID("6f1c0c3e-3c8a-4f0e-9a4a-1d2b3c4d5e6f")
OTHER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
REQUEST_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
GIF = "image/gif"

Handler = Callable[[httpx.Request], httpx.Response]


def _manifest(content_type: str = PDF, name: str = "report.pdf") -> RagManifest:
    return RagManifest(
        s3_key=f"users/{USER_ID}/files/{name}",
        file_name=name,
        content_sha256="ab" * 32,
        file_size=12345,
        content_type=content_type,
    )


def _card(
    status: str = "uploaded",
    error: str | None = None,
    content_type: str = PDF,
) -> dict[str, object]:
    return {
        "file_id": str(FILE_ID),
        "file_name": "report.pdf",
        "file_size": 12345,
        "status": status,
        "page_count": 4,
        "error": error,
        "progress": None,
        "content_type": content_type,
        "created_at": "2026-10-18T20:00:00Z",
        "updated_at": "2026-10-18T20:00:00Z",
    }


def _rag_error(status: int, code: str, details: dict[str, object] | None = None) -> httpx.Response:
    error: dict[str, object] = {"code": code, "message": code}
    if details is not None:
        error["details"] = details
    return httpx.Response(status, json={"error": error})


def _client(handler: Handler, base_url: str = "http://rag-edge:8080") -> tuple[RagIndexClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RagIndexClient(base_url, http_client=http), http


async def _close(client: RagIndexClient, http: httpx.AsyncClient) -> None:
    await client.aclose()
    await http.aclose()


def _assert_identity(request: httpx.Request, user_id: UUID | None) -> None:
    names = {key.lower() for key in request.headers}
    assert "cookie" not in names
    assert "authorization" not in names
    assert "x-auth-role" not in names
    if user_id is None:
        assert "x-auth-user-id" not in names
    else:
        assert request.headers["X-Auth-User-Id"] == str(user_id)


def test_exceptions_are_not_fastapi_http_exceptions() -> None:
    fastapi = pytest.importorskip("fastapi")
    assert not issubclass(RagIndexError, fastapi.HTTPException)
    assert not issubclass(RagIndexUnavailableError, fastapi.HTTPException)
    assert not issubclass(RagIndexHttpError, fastapi.HTTPException)


def test_empty_base_url_is_rejected() -> None:
    with pytest.raises(RagIndexError) as caught:
        RagIndexClient("  ")
    assert type(caught.value) is RagIndexError


def test_non_positive_timeout_is_rejected() -> None:
    with pytest.raises(RagIndexError) as caught:
        RagIndexClient("http://rag-edge:8080", timeout_seconds=0)
    assert type(caught.value) is RagIndexError


@pytest.mark.parametrize("content_type", [PDF, DOCX, XLSX])
@pytest.mark.asyncio
async def test_register_document_posts_documents_collection(content_type: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json=_card(content_type=content_type))

    client, http = _client(handler, base_url="http://rag-edge:8080/")
    try:
        card = await client.register_document(
            _manifest(content_type),
            user_id=USER_ID,
            request_id=REQUEST_ID,
        )
    finally:
        await _close(client, http)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://rag-edge:8080/api/files/documents"
    body = request.read()
    assert b"ab" * 32 in body
    payload = httpx.Response(200, content=body).json()
    assert payload["origin"] == STORAGE_ORIGIN
    assert payload["content_type"] == content_type
    assert payload["file_size"] == 12345
    assert set(payload) == {
        "s3_key",
        "file_name",
        "content_sha256",
        "file_size",
        "content_type",
        "origin",
    }
    _assert_identity(request, USER_ID)
    assert request.headers["X-Request-Id"] == str(REQUEST_ID)
    assert card == RagFileCard(
        file_id=FILE_ID,
        status="uploaded",
        error=None,
        content_type=content_type,
        file_name="report.pdf",
        file_size=12345,
        page_count=4,
    )


@pytest.mark.parametrize("content_type", [JPEG, PNG, WEBP, GIF])
@pytest.mark.asyncio
async def test_register_image_posts_images_collection(content_type: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json=_card(content_type=content_type))

    client, http = _client(handler)
    try:
        card = await client.register_image(
            _manifest(content_type, name="photo.jpg"),
            user_id=USER_ID,
        )
    finally:
        await _close(client, http)

    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/files/images"
    assert httpx.Response(200, content=request.read()).json()["content_type"] == content_type
    _assert_identity(request, USER_ID)
    assert card.file_id == FILE_ID
    assert card.content_type == content_type


@pytest.mark.asyncio
async def test_rekey_puts_object_without_deleting() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json=_card(status="processing"))

    client, http = _client(handler)
    target = RagObjectKey(
        s3_key=f"users/{USER_ID}/files/moved/report.pdf",
        file_size=12345,
        content_sha256="ab" * 32,
    )
    try:
        card = await client.rekey(FILE_ID, target, user_id=USER_ID, request_id=REQUEST_ID)
    finally:
        await _close(client, http)

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "PUT"
    assert request.url.path == f"/api/files/{FILE_ID}/object"
    payload = httpx.Response(200, content=request.read()).json()
    assert payload == {
        "s3_key": target.s3_key,
        "file_size": 12345,
        "content_sha256": "ab" * 32,
    }
    _assert_identity(request, USER_ID)
    assert card.status == "processing"
    assert card.file_id == FILE_ID


@pytest.mark.asyncio
async def test_get_file_returns_pipeline_error_on_http_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/api/files/{FILE_ID}"
        _assert_identity(request, USER_ID)
        return httpx.Response(200, json=_card(status="error", error="no_extractable_text"))

    client, http = _client(handler)
    try:
        card = await client.get_file(FILE_ID, user_id=USER_ID)
    finally:
        await _close(client, http)

    assert card.status == "error"
    assert card.error == "no_extractable_text"
    assert card.file_id == FILE_ID


@pytest.mark.asyncio
async def test_delete_vectors_accepts_202() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == f"/api/files/{FILE_ID}"
        assert request.content == b""
        _assert_identity(request, USER_ID)
        return httpx.Response(202, json={"file_id": str(FILE_ID), "status": "deleting"})

    client, http = _client(handler)
    try:
        result = await client.delete_vectors(FILE_ID, user_id=USER_ID)
    finally:
        await _close(client, http)

    assert result.file_id == FILE_ID
    assert result.status == "deleting"


@pytest.mark.asyncio
async def test_image_search_query_shape_and_hits() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "file_id": str(FILE_ID),
                        "file_name": "a.jpg",
                        "s3_key": f"users/{USER_ID}/photos/originals/secret.jpg",
                        "s3_url": "s3://storage/users/secret.jpg",
                        "score": 0.77,
                        "content_path": f"/api/files/{FILE_ID}/content",
                    }
                ],
                "limit": 20,
                "offset": 0,
                "next_offset": None,
            },
        )

    client, http = _client(handler)
    try:
        result = await client.search_images("sunset over the sea", user_id=USER_ID, limit=20)
    finally:
        await _close(client, http)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/api/chats/image-search"
    assert dict(request.url.params) == {
        "q": "sunset over the sea",
        "limit": "20",
        "offset": "0",
    }
    _assert_identity(request, USER_ID)
    assert len(result.items) == 1
    hit = result.items[0]
    assert hit.file_id == FILE_ID
    assert hit.file_name == "a.jpg"
    assert hit.score == pytest.approx(0.77)
    assert result.limit == 20
    assert result.offset == 0
    assert result.next_offset is None
    assert "secret.jpg" not in repr(result)
    assert not hasattr(hit, "s3_key")
    assert not hasattr(hit, "s3_url")
    assert not hasattr(hit, "content_path")


@pytest.mark.asyncio
async def test_image_search_empty_page_is_success() -> None:
    records: list[dict[str, object]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "limit": 20, "offset": 0, "next_offset": None},
        )

    client, http = _client(handler)
    try:
        result = await client.search_images("sunset over the sea", user_id=USER_ID)
    finally:
        await _close(client, http)
        logger.remove(sink_id)

    assert result.items == ()
    warnings = [record for record in records if record["level"].name == "WARNING"]
    assert warnings
    assert "sunset" not in warnings[-1]["message"]
    assert all(record["exception"] is None for record in records)


@pytest.mark.asyncio
async def test_connection_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexUnavailableError) as caught:
            await client.register_document(_manifest(), user_id=USER_ID)
    finally:
        await _close(client, http)

    assert type(caught.value) is RagIndexUnavailableError
    assert caught.value.status_code is None
    assert caught.value.error_code == ERROR_DEPENDENCY_UNAVAILABLE


@pytest.mark.asyncio
async def test_timeout_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexUnavailableError) as caught:
            await client.get_file(FILE_ID, user_id=USER_ID)
    finally:
        await _close(client, http)

    assert caught.value.error_code == ERROR_TIMEOUT
    assert caught.value.status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (502, "upstream_failed"),
        (503, ERROR_DEPENDENCY_UNAVAILABLE),
        (504, "upstream_timeout"),
    ],
)
async def test_search_outage_statuses_are_unavailable(status: int, code: str) -> None:
    records: list[dict[str, object]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")

    def handler(request: httpx.Request) -> httpx.Response:
        return _rag_error(status, code)

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexUnavailableError) as caught:
            await client.search_images("sunset over the sea", user_id=USER_ID, limit=5, offset=0)
    finally:
        await _close(client, http)
        logger.remove(sink_id)

    assert caught.value.status_code == status
    assert caught.value.error_code == code
    errors = [record for record in records if record["level"].name == "ERROR"]
    assert errors
    assert all(record["exception"] is None for record in errors)
    assert "sunset" not in errors[-1]["message"]


@pytest.mark.asyncio
async def test_duplicate_file_exposes_existing_id() -> None:
    records: list[dict[str, object]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")

    def handler(request: httpx.Request) -> httpx.Response:
        return _rag_error(409, ERROR_DUPLICATE_FILE, {"file_id": str(OTHER_ID)})

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexHttpError) as caught:
            await client.register_document(_manifest(), user_id=USER_ID)
    finally:
        await _close(client, http)
        logger.remove(sink_id)

    error = caught.value
    assert type(error) is RagIndexHttpError
    assert error.status_code == 409
    assert error.error_code == ERROR_DUPLICATE_FILE
    assert error.details["file_id"] == str(OTHER_ID)
    assert any(record["level"].name == "WARNING" for record in records)


@pytest.mark.asyncio
async def test_unsupported_media_is_http_error_not_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _rag_error(415, "unsupported_media_type")

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexHttpError) as caught:
            await client.register_document(_manifest(), user_id=USER_ID)
    finally:
        await _close(client, http)

    assert caught.value.status_code == 415
    assert caught.value.error_code == "unsupported_media_type"
    assert not isinstance(caught.value, RagIndexUnavailableError)


@pytest.mark.asyncio
async def test_delete_not_found_and_file_busy_are_http_errors() -> None:
    cases = (
        _rag_error(404, "file_not_found"),
        _rag_error(409, ERROR_FILE_BUSY),
    )

    for response in cases:
        def handler(request: httpx.Request, response: httpx.Response = response) -> httpx.Response:
            return response

        client, http = _client(handler)
        try:
            with pytest.raises(RagIndexHttpError) as caught:
                await client.delete_vectors(FILE_ID, user_id=USER_ID)
        finally:
            await _close(client, http)
        assert caught.value.status_code == response.status_code


@pytest.mark.asyncio
async def test_rekey_content_changed_is_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _rag_error(409, ERROR_CONTENT_CHANGED)

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexHttpError) as caught:
            await client.rekey(
                FILE_ID,
                RagObjectKey(s3_key="users/x/files/a.pdf", file_size=10, content_sha256="cd" * 32),
                user_id=USER_ID,
            )
    finally:
        await _close(client, http)

    assert caught.value.status_code == 409
    assert caught.value.error_code == ERROR_CONTENT_CHANGED


@pytest.mark.asyncio
async def test_health_ok_sends_no_user_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client, http = _client(handler)
    try:
        assert await client.health(request_id=REQUEST_ID) is True
    finally:
        await _close(client, http)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/health"
    assert request.headers["X-Request-Id"] == str(REQUEST_ID)
    _assert_identity(request, None)


@pytest.mark.asyncio
async def test_health_connection_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexUnavailableError) as caught:
            await client.health()
    finally:
        await _close(client, http)

    assert caught.value.error_code == ERROR_DEPENDENCY_UNAVAILABLE


@pytest.mark.asyncio
async def test_unreadable_success_body_is_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"not-json")

    client, http = _client(handler)
    try:
        with pytest.raises(RagIndexHttpError) as caught:
            await client.register_document(_manifest(), user_id=USER_ID)
    finally:
        await _close(client, http)

    assert caught.value.status_code == 202
    assert caught.value.error_code is None


@pytest.mark.asyncio
async def test_success_logs_omit_file_name_and_digest() -> None:
    records: list[dict[str, object]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json=_card())

    client, http = _client(handler)
    try:
        await client.register_document(_manifest(), user_id=USER_ID, request_id=REQUEST_ID)
    finally:
        await _close(client, http)
        logger.remove(sink_id)

    text = "\n".join(str(record["message"]) for record in records)
    assert "report.pdf" not in text
    assert "ab" * 32 not in text
    assert str(FILE_ID) in text
    assert str(USER_ID) in text
    assert str(REQUEST_ID) in text
