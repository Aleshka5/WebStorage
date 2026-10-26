"""Request-path indexing only writes file_index_links. It does not call RAG."""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from loguru import logger
from PIL import Image

from app.application.archive_service import ARCHIVE_EXTENSION, ArchiveService
from app.application.file_service import FileService
from app.application.photo_service import PhotoService
from app.application.ports.index_links import IndexLink
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.indexing import (
    IndexLinkState,
    IndexSkipCode,
    RegistrationDecision,
    RegistrationKind,
)
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.rag.client import RagIndexClient
from app.infrastructure.thumbnail_service import ThumbnailService
from config import get_settings
from tests.test_encrypted_storage_adapter import InMemoryStorageAdapter

PDF_NAME = "report.pdf"
PDF_BYTES = b"%PDF-1.4 report"
PRIVATE_NAME = "do-not-log-vault-name.pdf"
EXCLUDED_NAME = "do-not-log-shared-name.pdf"
INDEX_DOWN = RuntimeError("index down")


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), (12, 34, 56))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _record(**overrides: object) -> FileRecord:
    now = datetime(2026, 10, 26, tzinfo=UTC)
    user_id = uuid4()
    fields: dict[str, object] = {
        "id": uuid4(),
        "user_id": user_id,
        "disk_id": "storage",
        "relative_path": f"users/{user_id}/files/docs/{PDF_NAME}",
        "original_name": PDF_NAME,
        "size_bytes": len(PDF_BYTES),
        "mime_type": "application/pdf",
        "is_encrypted": False,
        "section": FileSection.FILES,
        "status": FileStatus.COMMITTED,
        "checksum_sha256": "ab" * 32,
        "created_at": now,
        "last_accessed_at": now,
        "is_archived": False,
        "archive_path": None,
    }
    fields.update(overrides)
    return FileRecord(**fields)  # type: ignore[arg-type]


class FakeFileRepo:
    def __init__(self) -> None:
        self.records: list[FileRecord] = []

    def _replace(self, updated: FileRecord) -> FileRecord:
        self.records = [updated if item.id == updated.id else item for item in self.records]
        return updated

    async def create(self, **kwargs: object) -> FileRecord:
        now = datetime.now(UTC)
        record = FileRecord(
            id=uuid4(),
            user_id=kwargs["user_id"],  # type: ignore[arg-type]
            disk_id=str(kwargs["disk_id"]),
            relative_path=str(kwargs["relative_path"]),
            original_name=str(kwargs["original_name"]),
            size_bytes=int(kwargs["size_bytes"]),  # type: ignore[arg-type]
            mime_type=str(kwargs["mime_type"]),
            is_encrypted=bool(kwargs["is_encrypted"]),
            section=kwargs["section"],  # type: ignore[arg-type]
            status=kwargs.get("status", FileStatus.PENDING),  # type: ignore[arg-type]
            checksum_sha256=None,
            created_at=now,
            last_accessed_at=now,
            is_archived=False,
            archive_path=None,
        )
        self.records.append(record)
        return record

    async def get_by_id(self, file_id: UUID) -> FileRecord | None:
        return next((item for item in self.records if item.id == file_id), None)

    async def update_status(
        self,
        file_id: UUID,
        status: FileStatus,
        *,
        checksum_sha256: str | None = None,
        relative_path: str | None = None,
        original_name: str | None = None,
    ) -> FileRecord | None:
        record = await self.get_by_id(file_id)
        if record is None:
            return None
        return self._replace(
            replace(
                record,
                status=status,
                checksum_sha256=(
                    record.checksum_sha256 if checksum_sha256 is None else checksum_sha256
                ),
                relative_path=record.relative_path if relative_path is None else relative_path,
                original_name=record.original_name if original_name is None else original_name,
            )
        )

    async def update_size(
        self,
        file_id: UUID,
        size_bytes: int,
        *,
        checksum_sha256: str | None = None,
    ) -> FileRecord | None:
        record = await self.get_by_id(file_id)
        if record is None:
            return None
        return self._replace(
            replace(
                record,
                size_bytes=size_bytes,
                checksum_sha256=(
                    record.checksum_sha256 if checksum_sha256 is None else checksum_sha256
                ),
            )
        )

    async def delete(self, file_id: UUID) -> FileRecord | None:
        record = await self.get_by_id(file_id)
        if record is None:
            return None
        return self._replace(replace(record, status=FileStatus.DELETED))

    async def get_downloadable_by_relative_path(
        self,
        user_id: UUID,
        relative_path: str,
        section: FileSection,
    ) -> FileRecord | None:
        for record in self.records:
            if (
                record.user_id == user_id
                and record.relative_path == relative_path
                and record.section == section
                and record.status in (FileStatus.COMMITTED, FileStatus.ARCHIVED)
            ):
                return record
        return None

    async def heal_stale_relative_path(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None

    async def list_active_under_prefix(
        self,
        user_id: UUID,
        section: FileSection,
        prefix: str,
    ) -> list[FileRecord]:
        prefix_with_slash = f"{prefix.rstrip('/')}/"
        return [
            record
            for record in self.records
            if record.user_id == user_id
            and record.section == section
            and record.status in (FileStatus.COMMITTED, FileStatus.ARCHIVED)
            and record.relative_path.startswith(prefix_with_slash)
        ]

    async def update_relative_path_prefix(
        self,
        user_id: UUID,
        section: FileSection,
        old_prefix: str,
        new_prefix: str,
    ) -> int:
        prefix_with_slash = f"{old_prefix}/"
        moved = 0
        for record in list(self.records):
            if (
                record.user_id == user_id
                and record.section == section
                and record.status == FileStatus.COMMITTED
                and record.relative_path.startswith(prefix_with_slash)
            ):
                suffix = record.relative_path[len(old_prefix) :]
                self._replace(replace(record, relative_path=f"{new_prefix}{suffix}"))
                moved += 1
        return moved

    async def touch_last_accessed(self, file_id: UUID) -> None:
        del file_id


class FakeQuotaRepo:
    def __init__(self) -> None:
        self.total_bytes = 0
        self.limit_bytes = 100 * 1024 * 1024

    async def get_by_user_id(self, user_id: UUID) -> SimpleNamespace:
        return SimpleNamespace(
            user_id=user_id,
            total_bytes=self.total_bytes,
            limit_bytes=self.limit_bytes,
            private_bytes=0,
            private_limit_bytes=0,
        )

    async def increment(self, user_id: UUID, size_bytes: int, section: FileSection) -> SimpleNamespace:
        del section
        self.total_bytes += size_bytes
        return await self.get_by_user_id(user_id)

    async def decrement(self, user_id: UUID, size_bytes: int, section: FileSection) -> SimpleNamespace:
        del section
        self.total_bytes = max(0, self.total_bytes - size_bytes)
        return await self.get_by_user_id(user_id)


class FakeIndexLinks:
    """In-memory stand-in for IndexLinkRepository enqueue methods.

    Document, image, and preview decisions are stored as PENDING. Skip
    decisions are stored as SKIPPED. That is the repository's insert rule.
    """

    def __init__(self, *, fail: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.fail = fail

    async def upsert_pending(
        self,
        record: FileRecord,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink:
        del now
        self.calls.append(("upsert_pending", record, decision))
        self._maybe_fail("upsert_pending")
        state = (
            IndexLinkState.SKIPPED
            if decision.kind is RegistrationKind.SKIP
            else IndexLinkState.PENDING
        )
        return self._link(record, state, decision.s3_key, decision.content_type, decision.content_sha256)

    async def mark_content_changed(
        self,
        file_record_id: UUID,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink:
        del now
        self.calls.append(("mark_content_changed", file_record_id, decision))
        self._maybe_fail("mark_content_changed")
        record = self._record_for(file_record_id)
        return self._link(
            record,
            IndexLinkState.REINDEX_PENDING,
            decision.s3_key,
            decision.content_type,
            decision.content_sha256,
        )

    async def mark_rekey(
        self,
        file_record_id: UUID,
        s3_key: str,
        *,
        now: datetime | None = None,
    ) -> IndexLink:
        del now
        self.calls.append(("mark_rekey", file_record_id, s3_key))
        self._maybe_fail("mark_rekey")
        record = self._record_for(file_record_id)
        return self._link(record, IndexLinkState.PENDING, s3_key, record.mime_type, record.checksum_sha256)

    async def mark_removed(
        self,
        file_record_ids: list[UUID],
        *,
        now: datetime | None = None,
    ) -> list[IndexLink]:
        del now
        ids = list(file_record_ids)
        self.calls.append(("mark_removed", ids))
        self._maybe_fail("mark_removed")
        return [
            self._link(self._record_for(file_id), IndexLinkState.REMOVED, "removed", "application/pdf", None)
            for file_id in ids
        ]

    async def mark_archived(
        self,
        file_record_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IndexLink:
        del now
        self.calls.append(("mark_archived", file_record_id))
        self._maybe_fail("mark_archived")
        record = self._record_for(file_record_id)
        return self._link(record, IndexLinkState.ARCHIVED, record.relative_path, record.mime_type, None)

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail:
            raise INDEX_DOWN

    def _record_for(self, file_record_id: UUID) -> FileRecord:
        for call in self.calls:
            if call[0] == "upsert_pending" and call[1].id == file_record_id:
                return call[1]
        return _record(id=file_record_id)

    @staticmethod
    def _link(
        record: FileRecord,
        state: IndexLinkState,
        s3_key: str,
        content_type: str,
        content_sha256: str | None,
    ) -> IndexLink:
        now = datetime.now(UTC)
        return IndexLink(
            id=uuid4(),
            file_record_id=record.id,
            user_id=record.user_id,
            section=record.section,
            s3_key=s3_key,
            content_sha256=content_sha256,
            content_type=content_type,
            rag_file_id=None,
            state=state,
            attempts=0,
            next_attempt_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )


def _methods(links: FakeIndexLinks) -> list[object]:
    return [call[0] for call in links.calls]


@pytest.fixture
def log_lines() -> list[str]:
    lines: list[str] = []

    def _sink(message: object) -> None:
        record = message.record  # type: ignore[attr-defined]
        lines.append(str(record["message"]))
        exception = record["exception"]
        if exception is not None and exception.value is not None:
            lines.append(f"{type(exception.value).__name__}: {exception.value}")

    sink_id = logger.add(_sink, level="DEBUG")
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def rag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("RAG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def rag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("RAG_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def rag_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("storage operation must not call RAG")

    for name in (
        "register_document",
        "register_image",
        "get_file",
        "rekey",
        "delete_vectors",
        "search_images",
        "health",
    ):
        monkeypatch.setattr(RagIndexClient, name, _forbidden)

    class _ClosedClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("storage operation must not call RAG")

    monkeypatch.setattr(httpx, "AsyncClient", _ClosedClient)
    monkeypatch.setattr(httpx, "Client", _ClosedClient)


async def _files_service(
    links: FakeIndexLinks | None,
    *,
    section: FileSection = FileSection.FILES,
) -> tuple[FileService, UUID, InMemoryStorageAdapter]:
    user_id = uuid4()
    adapter = InMemoryStorageAdapter(
        disk_id="storage",
        root_prefix=f"users/{user_id}/files",
    )
    await adapter.mkdir(".tmp")
    await adapter.mkdir("docs")
    service = FileService(
        adapter,
        FakeQuotaRepo(),
        FakeFileRepo(),
        section=section,
        index_links=links,
    )
    return service, user_id, adapter


async def _upload(
    service: FileService,
    user_id: UUID,
    filename: str,
    payload: bytes,
    section: FileSection,
) -> FileRecord:
    return await service.upload_file(
        user_id,
        "docs",
        filename,
        _chunks(payload),
        len(payload),
        section,
    )


async def _drain_background_tasks() -> None:
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _logged(lines: list[str], text: str) -> bool:
    return any(text in line for line in lines)


async def test_upload_queues_pending_and_does_not_call_rag(
    rag_enabled: None,
    log_lines: list[str],
) -> None:
    links = FakeIndexLinks()
    service, user_id, adapter = await _files_service(links)

    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    assert uploaded.status is FileStatus.COMMITTED
    assert await adapter.exists(f"docs/{PDF_NAME}")
    assert _methods(links) == ["upsert_pending"]
    record, decision = links.calls[0][1], links.calls[0][2]
    assert record.id == uploaded.id
    assert decision.kind is RegistrationKind.DOCUMENT
    assert decision.s3_key == uploaded.relative_path
    assert decision.content_sha256 == uploaded.checksum_sha256
    assert _logged(log_lines, "state=PENDING")
    assert _logged(log_lines, f"file_record_id={uploaded.id}")


async def test_photo_upload_queues_pending(rag_enabled: None, log_lines: list[str]) -> None:
    links = FakeIndexLinks()
    user_id = uuid4()
    adapter = InMemoryStorageAdapter(disk_id="storage", root_prefix=f"users/{user_id}/photos")
    await adapter.mkdir("originals")
    await adapter.mkdir("previews")
    service = PhotoService(
        adapter,
        ThumbnailService(),
        FakeFileRepo(),
        FakeQuotaRepo(),
        MagicMock(),
        index_links=links,
    )
    payload = _jpeg_bytes()

    uploaded = await service.upload_photo(user_id, "sunset.jpg", _chunks(payload), len(payload))
    await _drain_background_tasks()

    assert _methods(links) == ["upsert_pending"]
    assert links.calls[0][2].kind is RegistrationKind.IMAGE
    assert links.calls[0][1].id == uploaded.id
    assert _logged(log_lines, "state=PENDING")


async def test_zip_upload_queues_each_committed_file(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)
    payload = b"%PDF-1.4 zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes/report.pdf", payload)
        archive.writestr("notes/readme.txt", b"plain")
    buffer.seek(0)

    stats = await service.upload_zip_folder(
        user_id,
        "",
        "bundle.zip",
        buffer,
        len(payload) + len(b"plain"),
        10_000_000,
    )

    assert stats["files"] == 2
    assert _methods(links) == ["upsert_pending", "upsert_pending"]
    kinds = {call[2].kind for call in links.calls}
    assert RegistrationKind.DOCUMENT in kinds
    assert RegistrationKind.SKIP in kinds


async def test_rag_disabled_writes_nothing(
    rag_disabled: None,
    log_lines: list[str],
) -> None:
    links = FakeIndexLinks()
    service, user_id, adapter = await _files_service(links)

    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    assert uploaded.status is FileStatus.COMMITTED
    assert await adapter.exists(f"docs/{PDF_NAME}")
    assert links.calls == []
    assert _logged(log_lines, "RAG indexing is disabled")
    assert not _logged(log_lines, "Queued index link")


async def test_private_and_excluded_sections_write_nothing(
    rag_enabled: None,
    log_lines: list[str],
) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)

    private = await _upload(service, user_id, PRIVATE_NAME, PDF_BYTES, FileSection.PRIVATE)
    excluded = await _upload(service, user_id, EXCLUDED_NAME, PDF_BYTES, FileSection.SHARED)

    assert private.section is FileSection.PRIVATE
    assert private.is_encrypted is True
    assert excluded.section is FileSection.SHARED
    assert links.calls == []
    joined = "\n".join(log_lines)
    assert PRIVATE_NAME not in joined
    assert EXCLUDED_NAME not in joined
    assert _logged(log_lines, f"file_record_id {private.id}")
    assert _logged(log_lines, "excluded section SHARED")
    assert _logged(log_lines, f"file_record_id {excluded.id}")


async def test_unsupported_type_is_still_handed_to_upsert(rag_enabled: None, log_lines: list[str]) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)

    await _upload(service, user_id, "notes.txt", b"hello", FileSection.FILES)

    assert _methods(links) == ["upsert_pending"]
    decision = links.calls[0][2]
    assert decision.kind is RegistrationKind.SKIP
    assert decision.skip_code is IndexSkipCode.UNSUPPORTED_TYPE
    assert _logged(log_lines, "state=SKIPPED")


async def test_content_change_calls_mark_content_changed(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)
    original = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)
    replacement = b"%PDF-1.4 replacement body that is longer"

    updated = await service.overwrite_file(
        user_id,
        "docs",
        PDF_NAME,
        _chunks(replacement),
        len(replacement),
        FileSection.FILES,
    )

    assert _methods(links) == ["upsert_pending", "mark_content_changed"]
    file_record_id, decision = links.calls[1][1], links.calls[1][2]
    assert file_record_id == original.id
    assert decision.content_sha256 == updated.checksum_sha256
    assert decision.content_sha256 != original.checksum_sha256
    assert decision.s3_key == updated.relative_path


async def test_rename_calls_mark_rekey(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)
    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    renamed = await service.rename(user_id, uploaded.id, "renamed.pdf")

    assert _methods(links) == ["upsert_pending", "mark_rekey"]
    assert links.calls[1][1] == uploaded.id
    assert links.calls[1][2] == renamed.relative_path
    assert str(links.calls[1][2]).endswith("/docs/renamed.pdf")


async def test_directory_rename_rekeys_descendants(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)
    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    assert await service.rename_by_path(user_id, "docs", "moved") is None

    rekeys = [call for call in links.calls if call[0] == "mark_rekey"]
    assert len(rekeys) == 1
    assert rekeys[0][1] == uploaded.id
    assert str(rekeys[0][2]).endswith("/moved/report.pdf")


async def test_delete_calls_mark_removed(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, adapter = await _files_service(links)
    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    await service.delete_file(user_id, uploaded.id)

    assert not await adapter.exists(f"docs/{PDF_NAME}")
    assert ("mark_removed", [uploaded.id]) in links.calls


async def test_directory_delete_calls_mark_removed(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    service, user_id, _adapter = await _files_service(links)
    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    released = await service.delete_directory_recursive(user_id, "docs")

    assert released == len(PDF_BYTES)
    assert ("mark_removed", [uploaded.id]) in links.calls


async def test_photo_delete_calls_mark_removed(rag_enabled: None) -> None:
    links = FakeIndexLinks()
    user_id = uuid4()
    adapter = InMemoryStorageAdapter(disk_id="storage", root_prefix=f"users/{user_id}/photos")
    await adapter.mkdir("originals")
    await adapter.mkdir("previews")
    service = PhotoService(
        adapter,
        ThumbnailService(),
        FakeFileRepo(),
        FakeQuotaRepo(),
        MagicMock(),
        index_links=links,
    )
    payload = _jpeg_bytes()
    uploaded = await service.upload_photo(user_id, "sunset.jpg", _chunks(payload), len(payload))

    await service.delete_photo(user_id, uploaded.id)
    await _drain_background_tasks()

    assert ("mark_removed", [uploaded.id]) in links.calls


async def test_archive_calls_mark_archived(rag_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid4()
    relative_path = f"users/{user_id}/files/docs/{PDF_NAME}"
    adapter = InMemoryStorageAdapter(disk_id="storage", root_prefix="")
    await adapter.mkdir(f"users/{user_id}/files/docs")
    await adapter.write(relative_path, _chunks(PDF_BYTES), len(PDF_BYTES))
    record = _record(
        id=uuid4(),
        user_id=user_id,
        relative_path=relative_path,
        size_bytes=len(PDF_BYTES),
    )
    archived = replace(
        record,
        status=FileStatus.ARCHIVED,
        is_archived=True,
        archive_path=f"{relative_path}{ARCHIVE_EXTENSION}",
    )
    file_repo = AsyncMock()
    file_repo.mark_archived.return_value = archived
    links = FakeIndexLinks()
    service = ArchiveService(
        file_repo=file_repo,
        archive_manager=ArchiveManager(),
        disk_router=MagicMock(),
        index_links=links,
    )
    monkeypatch.setattr(
        "app.application.archive_service.build_disk_root_adapter",
        lambda disk_id: adapter,
    )

    assert await service._archive_record(record) is True

    assert not await adapter.exists(relative_path)
    assert await adapter.exists(f"{relative_path}{ARCHIVE_EXTENSION}")
    assert links.calls == [("mark_archived", record.id)]
    assert not any(
        isinstance(part, str) and part.endswith(ARCHIVE_EXTENSION) for call in links.calls for part in call
    )


async def test_repository_error_does_not_fail_upload(
    rag_enabled: None,
) -> None:
    links = FakeIndexLinks(fail=frozenset({"upsert_pending"}))
    service, user_id, _adapter = await _files_service(links)

    with pytest.raises(RuntimeError, match="index down"):
        await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    assert _methods(links) == ["upsert_pending"]


async def test_repository_error_does_not_fail_delete(rag_enabled: None) -> None:
    links = FakeIndexLinks(fail=frozenset({"mark_removed"}))
    service, user_id, _adapter = await _files_service(links)
    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    with pytest.raises(RuntimeError, match="index down"):
        await service.delete_file(user_id, uploaded.id)

    assert ("mark_removed", [uploaded.id]) in links.calls


async def test_repository_error_does_not_fail_archive(
    rag_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    relative_path = f"users/{user_id}/files/docs/{PDF_NAME}"
    adapter = InMemoryStorageAdapter(disk_id="storage", root_prefix="")
    await adapter.mkdir(f"users/{user_id}/files/docs")
    await adapter.write(relative_path, _chunks(PDF_BYTES), len(PDF_BYTES))
    record = _record(user_id=user_id, relative_path=relative_path, size_bytes=len(PDF_BYTES))
    file_repo = AsyncMock()
    file_repo.mark_archived.return_value = replace(record, is_archived=True, status=FileStatus.ARCHIVED)
    links = FakeIndexLinks(fail=frozenset({"mark_archived"}))
    service = ArchiveService(
        file_repo=file_repo,
        archive_manager=ArchiveManager(),
        disk_router=MagicMock(),
        index_links=links,
    )
    monkeypatch.setattr(
        "app.application.archive_service.build_disk_root_adapter",
        lambda disk_id: adapter,
    )

    with pytest.raises(RuntimeError, match="index down"):
        await service._archive_record(record)

    assert links.calls == [("mark_archived", record.id)]


async def test_missing_collaborator_keeps_upload_unchanged(rag_enabled: None, log_lines: list[str]) -> None:
    service, user_id, adapter = await _files_service(None)

    uploaded = await _upload(service, user_id, PDF_NAME, PDF_BYTES, FileSection.FILES)

    assert uploaded.status is FileStatus.COMMITTED
    assert await adapter.exists(f"docs/{PDF_NAME}")
    assert not _logged(log_lines, "Queued index link")
    assert not _logged(log_lines, "RAG indexing is disabled")
