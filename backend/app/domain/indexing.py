"""Pure decision of which storage object is sent to the RAG index."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from app.domain.entities.file_record import FileRecord, FileSection, FileStatus


class IndexLinkState(StrEnum):
    """Lifecycle of one file_index_links row."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    REMOVED = "REMOVED"
    ARCHIVED = "ARCHIVED"
    REINDEX_PENDING = "REINDEX_PENDING"


DUE_INDEX_STATES: frozenset[IndexLinkState] = frozenset(
    {
        IndexLinkState.PENDING,
        IndexLinkState.SUBMITTED,
        IndexLinkState.REMOVED,
        IndexLinkState.ARCHIVED,
        IndexLinkState.REINDEX_PENDING,
    }
)


class RegistrationKind(StrEnum):
    """How an object is offered to RAG, if it is offered at all."""

    DOCUMENT = "document"
    IMAGE = "image"
    PREVIEW = "preview"
    SKIP = "skip"


class IndexSkipCode(StrEnum):
    """Local reason a row is not sent, or stops being sent."""

    UNSUPPORTED_TYPE = "unsupported_type"
    PREVIEW_MISSING = "preview_missing"
    ARCHIVED = "archived"
    EXCLUDED_SECTION = "excluded_section"


DOCUMENTS_COLLECTION = "documents"
IMAGES_COLLECTION = "images"

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JPEG_CONTENT_TYPE = "image/jpeg"
PNG_CONTENT_TYPE = "image/png"
WEBP_CONTENT_TYPE = "image/webp"
GIF_CONTENT_TYPE = "image/gif"
OCTET_STREAM_CONTENT_TYPE = "application/octet-stream"

SEARCH_QUERY_MAX_LENGTH = 4000

_DOCUMENT_CONTENT_TYPES: dict[str, str] = {
    ".pdf": PDF_CONTENT_TYPE,
    ".docx": DOCX_CONTENT_TYPE,
    ".xlsx": XLSX_CONTENT_TYPE,
}
_IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".jpg": JPEG_CONTENT_TYPE,
    ".jpeg": JPEG_CONTENT_TYPE,
    ".png": PNG_CONTENT_TYPE,
    ".webp": WEBP_CONTENT_TYPE,
    ".gif": GIF_CONTENT_TYPE,
}
_PREVIEW_SOURCE_EXTENSIONS = frozenset({".heic", ".heif"})
_ARCHIVE_OBJECT_SUFFIX = ".zst"
_INDEXED_SECTIONS = frozenset({FileSection.FILES, FileSection.PHOTOS})
_PREVIEW_FILENAME_SUFFIX = "_thumb.jpg"


class PrivateIndexRefused(Exception):
    """Private or encrypted bytes are never registered in the index."""


class SearchQueryInvalid(Exception):
    """Photo search text is empty or longer than the allowed length."""

    error_code = "SEARCH_QUERY_INVALID"


class IndexingUnavailable(Exception):
    """RAG cannot answer photo search."""

    error_code = "INDEXING_UNAVAILABLE"


@dataclass(frozen=True)
class RegistrationDecision:
    """Target key, type, and hash for one registration attempt."""

    kind: RegistrationKind
    s3_key: str
    content_type: str
    content_sha256: str | None
    skip_code: IndexSkipCode | None

    def __post_init__(self) -> None:
        if self.kind is RegistrationKind.SKIP:
            if self.skip_code is None:
                raise ValueError("A skip decision requires skip_code")
        elif self.skip_code is not None:
            raise ValueError("An indexable decision must not set skip_code")
        if self.kind is RegistrationKind.PREVIEW and self.content_sha256 is not None:
            raise ValueError("Preview content hash is read from the preview bytes later")
        if not self.s3_key:
            raise ValueError("Registration decision requires s3_key")
        if not self.content_type:
            raise ValueError("Registration decision requires content_type")

    @property
    def collection(self) -> str | None:
        if self.kind is RegistrationKind.DOCUMENT:
            return DOCUMENTS_COLLECTION
        if self.kind is RegistrationKind.IMAGE or self.kind is RegistrationKind.PREVIEW:
            return IMAGES_COLLECTION
        return None


def preview_object_key(user_id: UUID, file_id: UUID) -> str:
    """JPEG preview key Photos already writes for one photo."""
    return f"users/{user_id}/photos/previews/{file_id}{_PREVIEW_FILENAME_SUFFIX}"


def validate_search_query(query: str) -> str:
    """Return the trimmed photo-search query, or refuse it."""
    normalized = query.strip()
    if not normalized or len(normalized) > SEARCH_QUERY_MAX_LENGTH:
        raise SearchQueryInvalid(
            "Search query must contain between 1 and 4000 characters",
        )
    return normalized


def decide_registration_target(
    record: FileRecord,
    *,
    max_image_bytes: int,
) -> RegistrationDecision:
    """Choose the RAG target for one file record.

    ``max_image_bytes`` is the caller's image cap (RAG's embedding limit).
    Preview hashes are left empty: they are read from the preview object later.
    """
    if record.section is FileSection.PRIVATE or record.is_encrypted:
        raise PrivateIndexRefused("Private objects are never indexed")

    extension = _extension(record)
    content_type = _content_type_for_extension(extension)

    if record.section not in _INDEXED_SECTIONS:
        return _skip(record, IndexSkipCode.EXCLUDED_SECTION, content_type)

    if _is_archived(record):
        return _skip(record, IndexSkipCode.ARCHIVED, content_type)

    if extension in _DOCUMENT_CONTENT_TYPES and record.section is FileSection.FILES:
        return _send(record, RegistrationKind.DOCUMENT, content_type)

    if extension in _IMAGE_CONTENT_TYPES and record.size_bytes <= max_image_bytes:
        return _send(record, RegistrationKind.IMAGE, content_type)

    if record.section is FileSection.PHOTOS and _uses_photo_preview(
        extension,
        record,
        max_image_bytes,
    ):
        return _preview(record)

    return _skip(record, IndexSkipCode.UNSUPPORTED_TYPE, content_type)


def _extension(record: FileRecord) -> str:
    suffix = PurePosixPath(record.original_name).suffix.lower()
    if suffix:
        return suffix
    return PurePosixPath(record.relative_path).suffix.lower()


def _content_type_for_extension(extension: str) -> str:
    document_type = _DOCUMENT_CONTENT_TYPES.get(extension)
    if document_type is not None:
        return document_type
    image_type = _IMAGE_CONTENT_TYPES.get(extension)
    if image_type is not None:
        return image_type
    return OCTET_STREAM_CONTENT_TYPE


def _uses_photo_preview(extension: str, record: FileRecord, max_image_bytes: int) -> bool:
    if extension in _PREVIEW_SOURCE_EXTENSIONS:
        return True
    return extension in _IMAGE_CONTENT_TYPES and record.size_bytes > max_image_bytes


def _is_archived(record: FileRecord) -> bool:
    if record.is_archived or record.status is FileStatus.ARCHIVED:
        return True
    return record.relative_path.lower().endswith(_ARCHIVE_OBJECT_SUFFIX)


def _send(
    record: FileRecord,
    kind: RegistrationKind,
    content_type: str,
) -> RegistrationDecision:
    return RegistrationDecision(
        kind=kind,
        s3_key=record.relative_path,
        content_type=content_type,
        content_sha256=record.checksum_sha256,
        skip_code=None,
    )


def _preview(record: FileRecord) -> RegistrationDecision:
    return RegistrationDecision(
        kind=RegistrationKind.PREVIEW,
        s3_key=preview_object_key(record.user_id, record.id),
        content_type=JPEG_CONTENT_TYPE,
        content_sha256=None,
        skip_code=None,
    )


def _skip(
    record: FileRecord,
    code: IndexSkipCode,
    content_type: str,
) -> RegistrationDecision:
    return RegistrationDecision(
        kind=RegistrationKind.SKIP,
        s3_key=record.relative_path,
        content_type=content_type,
        content_sha256=record.checksum_sha256,
        skip_code=code,
    )
