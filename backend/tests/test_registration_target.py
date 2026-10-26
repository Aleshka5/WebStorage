from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.indexing import (
    DOCUMENTS_COLLECTION,
    DOCX_CONTENT_TYPE,
    DUE_INDEX_STATES,
    GIF_CONTENT_TYPE,
    IMAGES_COLLECTION,
    JPEG_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    PNG_CONTENT_TYPE,
    SEARCH_QUERY_MAX_LENGTH,
    WEBP_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    IndexLinkState,
    IndexSkipCode,
    IndexingUnavailable,
    PrivateIndexRefused,
    RegistrationKind,
    SearchQueryInvalid,
    decide_registration_target,
    preview_object_key,
    validate_search_query,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
CHECKSUM = "a" * 64


def _record(**overrides: object) -> FileRecord:
    user_id = uuid4()
    file_id = uuid4()
    fields: dict[str, object] = {
        "id": file_id,
        "user_id": user_id,
        "disk_id": "primary",
        "relative_path": f"users/{user_id}/files/docs/report.pdf",
        "original_name": "report.pdf",
        "size_bytes": 2048,
        "mime_type": PDF_CONTENT_TYPE,
        "is_encrypted": False,
        "section": FileSection.FILES,
        "status": FileStatus.COMMITTED,
        "checksum_sha256": CHECKSUM,
        "created_at": datetime(2026, 10, 26, tzinfo=UTC),
        "last_accessed_at": datetime(2026, 10, 26, tzinfo=UTC),
        "is_archived": False,
        "archive_path": None,
    }
    fields.update(overrides)
    return FileRecord(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        (".pdf", PDF_CONTENT_TYPE),
        (".docx", DOCX_CONTENT_TYPE),
        (".xlsx", XLSX_CONTENT_TYPE),
        (".PDF", PDF_CONTENT_TYPE),
    ],
)
def test_files_documents_go_to_the_documents_collection(
    extension: str,
    content_type: str,
) -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name=f"Quarter{extension}",
        relative_path=f"users/{user_id}/files/reports/Quarter{extension}",
        mime_type="application/octet-stream",
        size_bytes=MAX_IMAGE_BYTES + 1,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.DOCUMENT
    assert decision.collection == DOCUMENTS_COLLECTION
    assert decision.s3_key == record.relative_path
    assert decision.content_type == content_type
    assert decision.content_sha256 == CHECKSUM
    assert decision.skip_code is None


def test_extension_falls_back_to_the_object_key() -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name="report",
        relative_path=f"users/{user_id}/files/reports/report.docx",
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.DOCUMENT
    assert decision.content_type == DOCX_CONTENT_TYPE


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        (".jpg", JPEG_CONTENT_TYPE),
        (".jpeg", JPEG_CONTENT_TYPE),
        (".png", PNG_CONTENT_TYPE),
        (".webp", WEBP_CONTENT_TYPE),
        (".gif", GIF_CONTENT_TYPE),
    ],
)
def test_files_images_at_the_size_cap_use_the_original(
    extension: str,
    content_type: str,
) -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name=f"scan{extension}",
        relative_path=f"users/{user_id}/files/scans/scan{extension}",
        mime_type="application/octet-stream",
        size_bytes=MAX_IMAGE_BYTES,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.IMAGE
    assert decision.collection == IMAGES_COLLECTION
    assert decision.s3_key == record.relative_path
    assert decision.content_type == content_type
    assert decision.content_sha256 == CHECKSUM
    assert decision.skip_code is None


def test_files_image_over_the_cap_is_skipped() -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name="scan.jpg",
        relative_path=f"users/{user_id}/files/scans/scan.jpg",
        mime_type=JPEG_CONTENT_TYPE,
        size_bytes=MAX_IMAGE_BYTES + 1,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.UNSUPPORTED_TYPE
    assert decision.collection is None
    assert decision.s3_key == record.relative_path
    assert decision.content_type == JPEG_CONTENT_TYPE


@pytest.mark.parametrize("extension", [".heic", ".heif"])
def test_files_heic_has_no_preview_and_is_skipped(extension: str) -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name=f"phone{extension}",
        relative_path=f"users/{user_id}/files/phone{extension}",
        mime_type="image/heic",
        size_bytes=1024,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        (".jpg", JPEG_CONTENT_TYPE),
        (".jpeg", JPEG_CONTENT_TYPE),
        (".png", PNG_CONTENT_TYPE),
        (".webp", WEBP_CONTENT_TYPE),
        (".gif", GIF_CONTENT_TYPE),
    ],
)
def test_photos_images_at_the_size_cap_use_the_original(
    extension: str,
    content_type: str,
) -> None:
    user_id = uuid4()
    file_id = uuid4()
    record = _record(
        id=file_id,
        user_id=user_id,
        section=FileSection.PHOTOS,
        original_name=f"sunset{extension}",
        relative_path=f"users/{user_id}/photos/originals/{file_id}{extension}",
        mime_type="application/octet-stream",
        size_bytes=MAX_IMAGE_BYTES,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.IMAGE
    assert decision.collection == IMAGES_COLLECTION
    assert decision.s3_key == record.relative_path
    assert decision.content_type == content_type
    assert decision.content_sha256 == CHECKSUM
    assert decision.skip_code is None


@pytest.mark.parametrize("extension", [".heic", ".heif"])
def test_photos_heic_uses_the_jpeg_preview(extension: str) -> None:
    user_id = uuid4()
    file_id = uuid4()
    record = _record(
        id=file_id,
        user_id=user_id,
        section=FileSection.PHOTOS,
        original_name=f"sunset{extension}",
        relative_path=f"users/{user_id}/photos/originals/{file_id}{extension}",
        mime_type="image/heic",
        checksum_sha256=CHECKSUM,
        size_bytes=1024,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.PREVIEW
    assert decision.collection == IMAGES_COLLECTION
    assert decision.s3_key == preview_object_key(user_id, file_id)
    assert decision.s3_key == f"users/{user_id}/photos/previews/{file_id}_thumb.jpg"
    assert decision.content_type == JPEG_CONTENT_TYPE
    assert decision.content_sha256 is None
    assert decision.skip_code is None


def test_photos_image_over_the_cap_uses_the_jpeg_preview() -> None:
    user_id = uuid4()
    file_id = uuid4()
    record = _record(
        id=file_id,
        user_id=user_id,
        section=FileSection.PHOTOS,
        original_name="sunset.png",
        relative_path=f"users/{user_id}/photos/originals/{file_id}.png",
        mime_type=PNG_CONTENT_TYPE,
        size_bytes=MAX_IMAGE_BYTES + 1,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.PREVIEW
    assert decision.s3_key == preview_object_key(user_id, file_id)
    assert decision.content_type == JPEG_CONTENT_TYPE
    assert decision.content_sha256 is None


@pytest.mark.parametrize(
    "section",
    [FileSection.SHARED, FileSection.RESUMES, FileSection.GENERATED],
)
def test_excluded_sections_are_never_indexed(section: FileSection) -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        section=section,
        original_name="notes.pdf",
        relative_path=f"users/{user_id}/{section.value.lower()}/notes.pdf",
        mime_type=PDF_CONTENT_TYPE,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.EXCLUDED_SECTION
    assert decision.collection is None
    assert decision.content_type == PDF_CONTENT_TYPE


def test_private_section_is_refused_even_when_the_flag_is_clear() -> None:
    record = _record(
        section=FileSection.PRIVATE,
        is_encrypted=False,
        original_name="secret.pdf",
        relative_path="users/someone/private/secret.pdf",
    )

    with pytest.raises(PrivateIndexRefused, match="never indexed") as raised:
        decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    message = str(raised.value)
    assert "secret" not in message
    assert record.relative_path not in message


def test_encrypted_bytes_outside_private_are_refused() -> None:
    record = _record(is_encrypted=True, original_name="vault.pdf")

    with pytest.raises(PrivateIndexRefused):
        decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)


@pytest.mark.parametrize(
    "name",
    ["legacy.doc", "legacy.xls", "notes.txt", "bundle.zip", "clip.mp4", "meta.yaml"],
)
def test_unsupported_types_are_skipped(name: str) -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name=name,
        relative_path=f"users/{user_id}/files/{name}",
        mime_type="application/octet-stream",
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.UNSUPPORTED_TYPE
    assert decision.s3_key == record.relative_path
    assert decision.content_sha256 == CHECKSUM
    assert decision.collection is None


def test_office_document_in_photos_is_unsupported() -> None:
    record = _record(
        section=FileSection.PHOTOS,
        original_name="sheet.xlsx",
        relative_path="users/someone/photos/originals/sheet.xlsx",
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_archived": True, "status": FileStatus.COMMITTED},
        {"is_archived": False, "status": FileStatus.ARCHIVED},
    ],
)
def test_archived_files_are_skipped(overrides: dict[str, object]) -> None:
    record = _record(**overrides)

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.ARCHIVED
    assert decision.content_type == PDF_CONTENT_TYPE


def test_zst_object_key_is_not_sent() -> None:
    user_id = uuid4()
    record = _record(
        user_id=user_id,
        original_name="report.pdf",
        relative_path=f"users/{user_id}/files/reports/report.pdf.zst",
        is_archived=False,
        status=FileStatus.COMMITTED,
    )

    decision = decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)

    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.ARCHIVED


def test_due_states_are_the_five_active_queue_states() -> None:
    assert DUE_INDEX_STATES == frozenset(
        {
            IndexLinkState.PENDING,
            IndexLinkState.SUBMITTED,
            IndexLinkState.REMOVED,
            IndexLinkState.ARCHIVED,
            IndexLinkState.REINDEX_PENDING,
        }
    )


def test_search_query_is_trimmed_and_bounded() -> None:
    assert validate_search_query("  sunset over the sea  ") == "sunset over the sea"
    assert validate_search_query("a" * SEARCH_QUERY_MAX_LENGTH) == "a" * SEARCH_QUERY_MAX_LENGTH

    with pytest.raises(SearchQueryInvalid) as empty:
        validate_search_query("   ")
    assert "   " not in str(empty.value)

    with pytest.raises(SearchQueryInvalid):
        validate_search_query("a" * (SEARCH_QUERY_MAX_LENGTH + 1))


def test_indexing_unavailable_carries_its_error_code() -> None:
    error = IndexingUnavailable("Indexing search is unavailable")

    assert error.error_code == "INDEXING_UNAVAILABLE"
    assert SearchQueryInvalid.error_code == "SEARCH_QUERY_INVALID"
