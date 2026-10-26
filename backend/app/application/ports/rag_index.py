"""Port for the RAG edge index API.

Text files (PDF, DOCX, XLSX) belong to the ``documents`` collection.
Images belong to the ``images`` collection. The HTTP paths select the
collection; callers do not send a collection name on register.

Content replace is two calls: ``delete_vectors`` then ``register_document``
or ``register_image``. Rename and move are ``rekey`` and do not delete.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

DOCUMENTS_COLLECTION = "documents"
IMAGES_COLLECTION = "images"
STORAGE_ORIGIN = "storage"

ERROR_TIMEOUT = "timeout"
ERROR_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
ERROR_DUPLICATE_FILE = "duplicate_file"
ERROR_FILE_BUSY = "file_busy"
ERROR_CONTENT_CHANGED = "content_changed"


class RagIndexError(Exception):
    """Base error from the RAG index client.

    Not a FastAPI HTTPException. Sync and search catch the subclasses.
    """


class RagIndexUnavailableError(RagIndexError):
    """RAG timed out, could not be connected to, or returned 502/503/504.

    ``error_code`` is the RAG ``error.code`` when the body has one.
    A client-side timeout uses ``timeout``. A connection failure, or an
    outage body without a code, uses ``dependency_unavailable``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class RagIndexHttpError(RagIndexError):
    """RAG completed the call with an application HTTP error.

    ``status_code`` is the HTTP status. ``error_code`` is ``error.code``
    when the body uses the RAG envelope, otherwise ``None``. ``details``
    is ``error.details`` (``duplicate_file`` carries ``file_id``).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str | None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.details: dict[str, Any] = dict(details or {})


@dataclass(frozen=True)
class RagManifest:
    """JSON manifest for document or image registration.

    ``origin`` is always ``storage`` and is added by the client.
    """

    s3_key: str
    file_name: str
    content_sha256: str
    file_size: int
    content_type: str


@dataclass(frozen=True)
class RagObjectKey:
    """New object coordinates for PUT /api/files/{file_id}/object."""

    s3_key: str
    file_size: int
    content_sha256: str


@dataclass(frozen=True)
class RagFileCard:
    """File card returned by register, re-key, and status poll."""

    file_id: UUID
    status: str
    error: str | None
    content_type: str | None
    file_name: str | None
    file_size: int | None
    page_count: int | None


@dataclass(frozen=True)
class RagDeleteResult:
    """Accepted delete. ``status`` is ``deleting`` on HTTP 202."""

    file_id: UUID
    status: str


@dataclass(frozen=True)
class RagImageHit:
    """One image-search hit. Storage keys and content paths are omitted."""

    file_id: UUID
    file_name: str
    score: float


@dataclass(frozen=True)
class RagImageSearchResult:
    """Page from GET /api/chats/image-search."""

    items: tuple[RagImageHit, ...]
    limit: int
    offset: int
    next_offset: int | None


class RagIndex(Protocol):
    """Application port for RAG register, poll, re-key, delete, and search."""

    async def register_document(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        """POST /api/files/documents. Collection ``documents``. HTTP 202.

        HTTP 409 ``duplicate_file`` raises ``RagIndexHttpError`` and
        ``details["file_id"]`` is the existing RAG id.
        """

    async def register_image(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        """POST /api/files/images. Collection ``images``. HTTP 202.

        A JPEG preview is registered with this method, not as a document.
        """

    async def get_file(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        """GET /api/files/{rag_file_id}. HTTP 200, including status ``error``.

        HTTP 404 raises ``RagIndexHttpError``.
        """

    async def rekey(
        self,
        rag_file_id: UUID,
        target: RagObjectKey,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        """PUT /api/files/{rag_file_id}/object. Same file id, new key. HTTP 202.

        HTTP 409 ``content_changed`` and HTTP 404 raise ``RagIndexHttpError``.
        """

    async def delete_vectors(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagDeleteResult:
        """DELETE /api/files/{rag_file_id}. HTTP 202 means deletion was accepted.

        HTTP 404 and HTTP 409 ``file_busy`` raise ``RagIndexHttpError``.
        Callers treat those two, together with 202, as vectors gone.
        """

    async def search_images(
        self,
        q: str,
        *,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        request_id: UUID | None = None,
    ) -> RagImageSearchResult:
        """GET /api/chats/image-search?q=&limit=&offset=.

        ``q`` is forwarded as given. An empty page is a successful result.
        HTTP 502/503/504 raise ``RagIndexUnavailableError``.
        """

    async def health(self, *, request_id: UUID | None = None) -> bool:
        """GET /health without ``X-Auth-User-Id``. True when status is ok.

        Outages raise ``RagIndexUnavailableError``.
        """

    async def aclose(self) -> None:
        """Close the HTTP client this adapter owns."""
