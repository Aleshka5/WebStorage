"""One-pass index backfill. The repository is a fake; RAG is not called."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from loguru import logger

from app.application.indexing.backfill import BackfillResult, BackfillStatus, backfill
from app.application.ports.index_links import BackfillCursor, IndexLink
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.indexing import (
    PDF_CONTENT_TYPE,
    IndexLinkState,
    PrivateIndexRefused,
    RegistrationDecision,
    RegistrationKind,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
CHECKSUM = "b" * 64
T0 = datetime(2026, 10, 26, tzinfo=UTC)


class _LogCapture:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self._sink_id = logger.add(self._sink, level="DEBUG")

    def _sink(self, message: Any) -> None:
        self.records.append(message.record)

    def messages(self, level: str) -> list[str]:
        return [
            record["message"]
            for record in self.records
            if record["level"].name == level
        ]

    def close(self) -> None:
        logger.remove(self._sink_id)


@pytest.fixture
def captured_logs() -> Iterator[_LogCapture]:
    capture = _LogCapture()
    yield capture
    capture.close()


@pytest.fixture
def settings_must_not_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise AssertionError("backfill must use the explicit arguments in this test")

    monkeypatch.setattr("app.application.indexing.backfill.get_settings", _boom)


def _record(**overrides: object) -> FileRecord:
    user_id = uuid4()
    file_id = uuid4()
    created_at = T0
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
        "created_at": created_at,
        "last_accessed_at": created_at,
        "is_archived": False,
        "archive_path": None,
    }
    fields.update(overrides)
    return FileRecord(**fields)  # type: ignore[arg-type]


def _link(record: FileRecord, decision: RegistrationDecision, state: IndexLinkState) -> IndexLink:
    return IndexLink(
        id=uuid4(),
        file_record_id=record.id,
        user_id=record.user_id,
        section=record.section,
        s3_key=decision.s3_key,
        content_sha256=decision.content_sha256,
        content_type=decision.content_type,
        rag_file_id=None,
        state=state,
        attempts=0,
        next_attempt_at=None,
        last_error_code=decision.skip_code.value if decision.skip_code is not None else None,
        created_at=record.created_at,
        updated_at=record.created_at,
    )


class FakeIndexLinks:
    """Candidates, one upsert, and a keyset remaining count. No second page."""

    def __init__(
        self,
        records: list[FileRecord],
        *,
        fail_ids: set[UUID] | None = None,
    ) -> None:
        self._records = sorted(records, key=lambda record: (record.created_at, record.id))
        self._fail_ids = fail_ids or set()
        self.candidate_calls: list[tuple[int, BackfillCursor | None]] = []
        self.remaining_calls: list[BackfillCursor | None] = []
        self.upserts: list[tuple[FileRecord, RegistrationDecision]] = []
        self.stored: list[IndexLink] = []

    async def backfill_candidates(
        self,
        limit: int,
        after: BackfillCursor | None = None,
    ) -> list[FileRecord]:
        self.candidate_calls.append((limit, after))
        linked = {link.file_record_id for link in self.stored}
        selected: list[FileRecord] = []
        for record in self._records:
            if record.id in linked:
                continue
            if after is not None and not _is_after(record, after):
                continue
            selected.append(record)
            if len(selected) == limit:
                break
        return selected

    async def count_remaining(self, after_enqueued: BackfillCursor | None = None) -> int:
        self.remaining_calls.append(after_enqueued)
        linked = {link.file_record_id for link in self.stored}
        return sum(
            1
            for record in self._records
            if record.id not in linked and _is_after(record, after_enqueued)
        )

    async def upsert_pending(
        self,
        record: FileRecord,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        del now
        self.upserts.append((record, decision))
        if record.id in self._fail_ids:
            raise RuntimeError("index backfill failed")
        if record.section is FileSection.PRIVATE or record.is_encrypted:
            raise PrivateIndexRefused("Private objects are never indexed")
        if record.section not in {FileSection.FILES, FileSection.PHOTOS}:
            return None
        state = (
            IndexLinkState.SKIPPED
            if decision.kind is RegistrationKind.SKIP
            else IndexLinkState.PENDING
        )
        link = _link(record, decision, state)
        self.stored.append(link)
        return link

    async def claim_due(self, limit: int, now: datetime) -> list[IndexLink]:
        del limit, now
        raise AssertionError("backfill must not drain claim_due")


def _is_after(record: FileRecord, cursor: BackfillCursor | None) -> bool:
    if cursor is None:
        return True
    return (record.created_at, record.id) > (cursor.created_at, cursor.record_id)


@pytest.mark.asyncio
async def test_disabled_backfill_writes_nothing(
    captured_logs: _LogCapture,
    settings_must_not_be_read: None,
) -> None:
    private = _record(
        section=FileSection.PRIVATE,
        is_encrypted=True,
        original_name="secret.pdf",
        relative_path="users/hidden/secret.pdf",
    )
    fake = FakeIndexLinks([private, _record()])

    result = await backfill(
        fake,
        backfill_enabled=False,
        index_batch_size=20,
        max_image_bytes=MAX_IMAGE_BYTES,
    )

    assert result == BackfillResult(
        examined=0,
        enqueued=0,
        skipped=0,
        next_cursor=None,
        remaining=0,
        status=BackfillStatus.SKIPPED,
    )
    assert fake.candidate_calls == []
    assert fake.remaining_calls == []
    assert fake.upserts == []
    assert fake.stored == []
    assert captured_logs.messages("WARNING") == [
        "Index backfill is disabled; skipping this pass",
    ]
    assert captured_logs.messages("INFO") == []
    assert "secret.pdf" not in " ".join(captured_logs.messages("WARNING"))


@pytest.mark.asyncio
async def test_one_pass_enqueues_document_and_photo_and_returns_next_cursor(
    captured_logs: _LogCapture,
    settings_must_not_be_read: None,
) -> None:
    user_id = uuid4()
    document = _record(
        user_id=user_id,
        original_name="report.pdf",
        relative_path=f"users/{user_id}/files/docs/report.pdf",
        created_at=T0,
        last_accessed_at=T0,
    )
    photo_id = uuid4()
    photo = _record(
        id=photo_id,
        user_id=user_id,
        original_name="shore.jpg",
        relative_path=f"users/{user_id}/photos/originals/{photo_id}.jpg",
        mime_type="image/jpeg",
        section=FileSection.PHOTOS,
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    notes = _record(
        user_id=user_id,
        original_name="notes.txt",
        relative_path=f"users/{user_id}/files/notes.txt",
        mime_type="text/plain",
        created_at=T0 + timedelta(minutes=2),
        last_accessed_at=T0,
    )
    later = _record(
        user_id=user_id,
        original_name="later.pdf",
        relative_path=f"users/{user_id}/files/later.pdf",
        created_at=T0 + timedelta(minutes=3),
        last_accessed_at=T0,
    )
    fake = FakeIndexLinks([document, photo, notes, later])

    result = await backfill(
        fake,
        limit=3,
        backfill_enabled=True,
        index_batch_size=20,
        max_image_bytes=MAX_IMAGE_BYTES,
    )

    assert fake.candidate_calls == [(3, None)]
    assert [link.file_record_id for link in fake.stored] == [
        document.id,
        photo.id,
        notes.id,
    ]
    assert [link.state for link in fake.stored] == [
        IndexLinkState.PENDING,
        IndexLinkState.PENDING,
        IndexLinkState.SKIPPED,
    ]
    kinds = {record.id: decision.kind for record, decision in fake.upserts}
    assert kinds[document.id] is RegistrationKind.DOCUMENT
    assert kinds[photo.id] is RegistrationKind.IMAGE
    assert kinds[notes.id] is RegistrationKind.SKIP
    expected_cursor = BackfillCursor(created_at=notes.created_at, record_id=notes.id)
    assert result == BackfillResult(
        examined=3,
        enqueued=2,
        skipped=1,
        next_cursor=expected_cursor,
        remaining=1,
        status=BackfillStatus.COMPLETED,
    )
    assert fake.remaining_calls == [expected_cursor]
    assert any(
        "examined=3" in message and "enqueued=2" in message
        for message in captured_logs.messages("INFO")
    )
    rendered = " ".join(
        captured_logs.messages("INFO")
        + captured_logs.messages("WARNING")
        + captured_logs.messages("ERROR")
    )
    assert "report.pdf" not in rendered
    assert "shore.jpg" not in rendered
    assert "notes.txt" not in rendered


@pytest.mark.asyncio
async def test_private_and_excluded_produce_no_row(
    captured_logs: _LogCapture,
    settings_must_not_be_read: None,
) -> None:
    private = _record(
        section=FileSection.PRIVATE,
        is_encrypted=True,
        original_name="secret.pdf",
        relative_path="users/hidden/secret.pdf",
        created_at=T0,
        last_accessed_at=T0,
    )
    shared = _record(
        section=FileSection.SHARED,
        original_name="shared-budget.pdf",
        relative_path="shared/docs/shared-budget.pdf",
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    document = _record(
        original_name="report.pdf",
        created_at=T0 + timedelta(minutes=2),
        last_accessed_at=T0,
    )
    fake = FakeIndexLinks([private, shared, document])

    result = await backfill(
        fake,
        limit=10,
        backfill_enabled=True,
        index_batch_size=20,
        max_image_bytes=MAX_IMAGE_BYTES,
    )

    assert fake.candidate_calls == [(10, None)]
    assert [link.file_record_id for link in fake.stored] == [document.id]
    assert result.examined == 3
    assert result.enqueued == 1
    assert result.skipped == 2
    assert result.next_cursor == BackfillCursor(
        created_at=document.created_at,
        record_id=document.id,
    )
    assert result.status is BackfillStatus.COMPLETED
    rendered = " ".join(
        captured_logs.messages("INFO")
        + captured_logs.messages("WARNING")
        + captured_logs.messages("ERROR")
    )
    assert "secret.pdf" not in rendered
    assert "shared-budget.pdf" not in rendered
    assert captured_logs.messages("ERROR") == []
    assert all(record["exception"] is None for record in captured_logs.records)


@pytest.mark.asyncio
async def test_one_bad_record_is_logged_and_the_pass_continues(
    captured_logs: _LogCapture,
    settings_must_not_be_read: None,
) -> None:
    document = _record(
        original_name="report.pdf",
        created_at=T0,
        last_accessed_at=T0,
    )
    poisoned = _record(
        original_name="poison.pdf",
        relative_path="users/owner/files/poison.pdf",
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    photo_id = uuid4()
    photo = _record(
        id=photo_id,
        original_name="shore.jpg",
        relative_path=f"users/{photo_id}/photos/originals/{photo_id}.jpg",
        mime_type="image/jpeg",
        section=FileSection.PHOTOS,
        created_at=T0 + timedelta(minutes=2),
        last_accessed_at=T0,
    )
    fake = FakeIndexLinks([document, poisoned, photo], fail_ids={poisoned.id})

    result = await backfill(
        fake,
        limit=10,
        backfill_enabled=True,
        index_batch_size=20,
        max_image_bytes=MAX_IMAGE_BYTES,
    )

    assert fake.candidate_calls == [(10, None)]
    assert [link.file_record_id for link in fake.stored] == [document.id, photo.id]
    assert result.examined == 3
    assert result.enqueued == 2
    assert result.skipped == 1
    assert result.next_cursor == BackfillCursor(
        created_at=photo.created_at,
        record_id=photo.id,
    )
    errors = [
        record
        for record in captured_logs.records
        if record["level"].name == "ERROR"
    ]
    assert len(errors) == 1
    assert errors[0]["exception"] is not None
    assert "poison.pdf" not in errors[0]["message"]
    assert str(poisoned.id) in errors[0]["message"]


@pytest.mark.asyncio
async def test_omitted_limit_is_ten_batches_and_still_one_page(
    settings_must_not_be_read: None,
) -> None:
    records = [
        _record(
            original_name=f"file-{index}.pdf",
            relative_path=f"users/owner/files/file-{index}.pdf",
            created_at=T0 + timedelta(minutes=index),
            last_accessed_at=T0,
        )
        for index in range(5)
    ]
    fake = FakeIndexLinks(records)

    result = await backfill(
        fake,
        backfill_enabled=True,
        index_batch_size=2,
        max_image_bytes=MAX_IMAGE_BYTES,
    )

    assert fake.candidate_calls == [(20, None)]
    assert result.examined == 5
    assert result.enqueued == 5
    assert result.remaining == 0
    assert result.next_cursor == BackfillCursor(
        created_at=records[-1].created_at,
        record_id=records[-1].id,
    )
