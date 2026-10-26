"""Repository tests for file_index_links.

Postgres is not required. SQLAlchemy's asyncio SQLite dialect imports
``aiosqlite``, which this project does not depend on, so the fixture below
adapts the stdlib ``sqlite3`` driver onto that import name.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Thread
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from loguru import logger
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.application.ports.index_links import BackfillCursor, IndexErrorCount
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.indexing import (
    PDF_CONTENT_TYPE,
    DUE_INDEX_STATES,
    IndexLinkState,
    IndexSkipCode,
    PrivateIndexRefused,
    RegistrationDecision,
    RegistrationKind,
    decide_registration_target,
)
from app.infrastructure.database.base import Base
from app.infrastructure.database.models import FileIndexLink
from app.infrastructure.database.models import FileRecord as FileRecordModel
from app.infrastructure.database.models import FileSection as FileSectionModel
from app.infrastructure.database.models import FileStatus as FileStatusModel
from app.infrastructure.database.models import User
from app.infrastructure.database.repositories.file_index_link_repo import (
    FileIndexLinkRepository,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
CHECKSUM = "a" * 64
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@compiles(PG_UUID, "sqlite")
def _compile_pg_uuid_sqlite(type_: PG_UUID, compiler: Any, **kw: Any) -> str:
    return "CHAR(36)"


def _install_sqlite_async_driver() -> None:
    if "aiosqlite" in sys.modules:
        return

    def _succeed(future: asyncio.Future[Any], result: Any) -> None:
        if not future.done():
            future.set_result(result)

    def _fail(future: asyncio.Future[Any], exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    class Cursor:
        def __init__(self, connection: Connection, raw: sqlite3.Cursor) -> None:
            self._connection = connection
            self._raw = raw
            self.description: Any = None
            self.rowcount = -1
            self.lastrowid: int | None = None
            self.arraysize = 1

        async def execute(self, operation: str, parameters: Any = None) -> Cursor:
            def run() -> tuple[Any, int, int | None]:
                if parameters is None:
                    self._raw.execute(operation)
                else:
                    self._raw.execute(operation, parameters)
                return self._raw.description, self._raw.rowcount, self._raw.lastrowid

            description, rowcount, lastrowid = await self._connection._execute(run)
            self.description = description
            self.rowcount = rowcount
            self.lastrowid = lastrowid
            return self

        async def executemany(self, operation: str, seq_of_parameters: Any) -> Cursor:
            def run() -> tuple[int, int | None]:
                self._raw.executemany(operation, seq_of_parameters)
                return self._raw.rowcount, self._raw.lastrowid

            rowcount, lastrowid = await self._connection._execute(run)
            self.description = None
            self.rowcount = rowcount
            self.lastrowid = lastrowid
            return self

        async def fetchone(self) -> Any:
            return await self._connection._execute(self._raw.fetchone)

        async def fetchall(self) -> list[Any]:
            return await self._connection._execute(self._raw.fetchall)

        async def fetchmany(self, size: int | None = None) -> list[Any]:
            if size is None:
                size = self.arraysize
            return await self._connection._execute(self._raw.fetchmany, size)

        async def close(self) -> None:
            await self._connection._execute(self._raw.close)

    class Connection:
        def __init__(self, database: str, *args: Any, **kwargs: Any) -> None:
            self._database = database
            self._args = args
            self._kwargs = kwargs
            self._conn: sqlite3.Connection | None = None
            self._loop: asyncio.AbstractEventLoop | None = None
            self._started: asyncio.Future[None] | None = None
            self._tx: Queue[tuple[asyncio.Future[Any] | None, Any]] = Queue()
            self._thread = Thread(target=self._run, name="file-index-link-sqlite")
            self._closed = False

        def __await__(self) -> Any:
            if self._loop is None:
                self._loop = asyncio.get_running_loop()
                self._started = self._loop.create_future()
                self._thread.start()
            assert self._started is not None
            return self._wait().__await__()

        async def _wait(self) -> Connection:
            assert self._started is not None
            await self._started
            return self

        def _run(self) -> None:
            assert self._loop is not None
            assert self._started is not None
            try:
                self._conn = sqlite3.connect(self._database, *self._args, **self._kwargs)
            except BaseException as exc:
                self._loop.call_soon_threadsafe(_fail, self._started, exc)
                return
            self._loop.call_soon_threadsafe(_succeed, self._started, None)
            while True:
                future, func = self._tx.get()
                if future is None:
                    if self._conn is not None:
                        self._conn.close()
                        self._conn = None
                    break
                try:
                    result = func()
                except BaseException as exc:
                    future.get_loop().call_soon_threadsafe(_fail, future, exc)
                else:
                    future.get_loop().call_soon_threadsafe(_succeed, future, result)

        async def _execute(self, fn: Any, *args: Any) -> Any:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Any] = loop.create_future()

            def task() -> Any:
                return fn(*args)

            self._tx.put_nowait((future, task))
            return await future

        async def cursor(self) -> Cursor:
            raw = await self._execute(lambda: self._conn.cursor() if self._conn else None)
            if raw is None:
                raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
            return Cursor(self, raw)

        async def execute(self, operation: str, parameters: Any = None) -> Cursor:
            cursor = await self.cursor()
            await cursor.execute(operation, parameters)
            return cursor

        async def commit(self) -> None:
            await self._execute(lambda: self._conn.commit() if self._conn else None)

        async def rollback(self) -> None:
            await self._execute(lambda: self._conn.rollback() if self._conn else None)

        async def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            if self._conn is not None:
                await self._execute(self._conn.close)
                self._conn = None
            self.stop()

        async def create_function(self, *args: Any, **kwargs: Any) -> None:
            await self._execute(
                lambda: self._conn.create_function(*args, **kwargs) if self._conn else None
            )

        def stop(self) -> None:
            self._tx.put_nowait((None, None))

        @property
        def isolation_level(self) -> str | None:
            if self._conn is None:
                return None
            return self._conn.isolation_level

    def connect(database: str, *args: Any, **kwargs: Any) -> Connection:
        return Connection(database, *args, **kwargs)

    module = ModuleType("aiosqlite")
    module.Connection = Connection  # type: ignore[attr-defined]
    module.Cursor = Cursor  # type: ignore[attr-defined]
    module.connect = connect  # type: ignore[attr-defined]
    module.Error = sqlite3.Error  # type: ignore[attr-defined]
    module.DatabaseError = sqlite3.DatabaseError  # type: ignore[attr-defined]
    module.IntegrityError = sqlite3.IntegrityError  # type: ignore[attr-defined]
    module.NotSupportedError = sqlite3.NotSupportedError  # type: ignore[attr-defined]
    module.OperationalError = sqlite3.OperationalError  # type: ignore[attr-defined]
    module.ProgrammingError = sqlite3.ProgrammingError  # type: ignore[attr-defined]
    module.sqlite_version = sqlite3.sqlite_version  # type: ignore[attr-defined]
    module.sqlite_version_info = sqlite3.sqlite_version_info  # type: ignore[attr-defined]
    module.paramstyle = "qmark"  # type: ignore[attr-defined]
    sys.modules["aiosqlite"] = module


_install_sqlite_async_driver()


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
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


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
        "created_at": T0,
        "last_accessed_at": T0,
        "is_archived": False,
        "archive_path": None,
    }
    fields.update(overrides)
    return FileRecord(**fields)  # type: ignore[arg-type]


def _decision_for(record: FileRecord) -> RegistrationDecision:
    return decide_registration_target(record, max_image_bytes=MAX_IMAGE_BYTES)


async def _save_file(session: AsyncSession, record: FileRecord) -> None:
    if await session.get(User, record.user_id) is None:
        session.add(
            User(
                id=record.user_id,
                email=f"{record.user_id}@example.test",
                is_active=True,
                created_at=record.created_at,
            )
        )
    session.add(
        FileRecordModel(
            id=record.id,
            user_id=record.user_id,
            disk_id=record.disk_id,
            relative_path=record.relative_path,
            original_name=record.original_name,
            size_bytes=record.size_bytes,
            mime_type=record.mime_type,
            is_encrypted=record.is_encrypted,
            section=FileSectionModel(record.section.value),
            status=FileStatusModel(record.status.value),
            checksum_sha256=record.checksum_sha256,
            created_at=record.created_at,
            last_accessed_at=record.last_accessed_at,
            is_archived=record.is_archived,
            archive_path=record.archive_path,
        )
    )
    await session.flush()


def _document_decision(record: FileRecord, s3_key: str | None = None) -> RegistrationDecision:
    return RegistrationDecision(
        kind=RegistrationKind.DOCUMENT,
        s3_key=s3_key or record.relative_path,
        content_type=PDF_CONTENT_TYPE,
        content_sha256=CHECKSUM,
        skip_code=None,
    )


@pytest.mark.asyncio
async def test_upsert_pending_document_image_and_preview(
    session: AsyncSession,
) -> None:
    repo = FileIndexLinkRepository(session)
    user_id = uuid4()
    document = _record(
        user_id=user_id,
        created_at=T0,
        last_accessed_at=T0,
    )
    image_id = uuid4()
    image = _record(
        id=image_id,
        user_id=user_id,
        original_name="shot.jpg",
        relative_path=f"users/{user_id}/photos/originals/{image_id}.jpg",
        mime_type="image/jpeg",
        section=FileSection.PHOTOS,
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    preview_id = uuid4()
    preview = _record(
        id=preview_id,
        user_id=user_id,
        original_name="raw.heic",
        relative_path=f"users/{user_id}/photos/originals/{preview_id}.heic",
        mime_type="image/heic",
        section=FileSection.PHOTOS,
        created_at=T0 + timedelta(minutes=2),
        last_accessed_at=T0,
    )
    for record in (document, image, preview):
        await _save_file(session, record)

    document_link = await repo.upsert_pending(document, _decision_for(document), now=T0)
    image_link = await repo.upsert_pending(
        image,
        _decision_for(image),
        now=T0 + timedelta(minutes=1),
    )
    preview_decision = _decision_for(preview)
    preview_link = await repo.upsert_pending(
        preview,
        preview_decision,
        now=T0 + timedelta(minutes=2),
    )

    assert document_link is not None
    assert document_link.state is IndexLinkState.PENDING
    assert document_link.section is FileSection.FILES
    assert document_link.s3_key == document.relative_path
    assert document_link.content_sha256 == CHECKSUM
    assert document_link.content_type == PDF_CONTENT_TYPE
    assert document_link.attempts == 0
    assert document_link.next_attempt_at is None
    assert document_link.last_error_code is None
    assert document_link.rag_file_id is None
    assert document_link.updated_at == T0

    assert image_link is not None
    assert image_link.state is IndexLinkState.PENDING
    assert image_link.section is FileSection.PHOTOS

    assert preview_decision.kind is RegistrationKind.PREVIEW
    assert preview_link is not None
    assert preview_link.state is IndexLinkState.PENDING
    assert preview_link.content_sha256 is None
    assert preview_link.s3_key == preview_decision.s3_key

    found = await repo.get_by_file_record_id(document.id)
    assert found is not None
    assert found.id == document_link.id
    pending = await repo.list_by_user_state(user_id, IndexLinkState.PENDING)
    assert [link.file_record_id for link in pending] == [
        document.id,
        image.id,
        preview.id,
    ]


@pytest.mark.asyncio
async def test_upsert_skipped_for_unsupported_and_archived(
    session: AsyncSession,
) -> None:
    repo = FileIndexLinkRepository(session)
    user_id = uuid4()
    unsupported = _record(
        user_id=user_id,
        original_name="notes.txt",
        relative_path=f"users/{user_id}/files/notes.txt",
        mime_type="text/plain",
    )
    archived = _record(
        user_id=user_id,
        original_name="old.pdf",
        relative_path=f"users/{user_id}/files/old.pdf",
        is_archived=True,
    )
    await _save_file(session, unsupported)
    await _save_file(session, archived)

    unsupported_link = await repo.upsert_pending(
        unsupported,
        _decision_for(unsupported),
        now=T0,
    )
    archived_link = await repo.upsert_pending(archived, _decision_for(archived), now=T0)

    assert unsupported_link is not None
    assert unsupported_link.state is IndexLinkState.SKIPPED
    assert unsupported_link.last_error_code == IndexSkipCode.UNSUPPORTED_TYPE.value
    assert archived_link is not None
    assert archived_link.state is IndexLinkState.SKIPPED
    assert archived_link.last_error_code == IndexSkipCode.ARCHIVED.value
    claimed = await repo.claim_due(10, T0)
    assert claimed == []


@pytest.mark.asyncio
async def test_refuse_private_and_excluded_write_no_row(
    session: AsyncSession,
    captured_logs: _LogCapture,
) -> None:
    repo = FileIndexLinkRepository(session)
    private = _record(
        section=FileSection.PRIVATE,
        is_encrypted=True,
        original_name="secret.pdf",
        relative_path="users/hidden/secret.pdf",
    )
    encrypted = _record(
        section=FileSection.FILES,
        is_encrypted=True,
        original_name="sealed.pdf",
    )
    shared = _record(
        section=FileSection.SHARED,
        original_name="shared.pdf",
        relative_path="shared/docs/shared.pdf",
    )
    for record in (private, encrypted, shared):
        await _save_file(session, record)

    with pytest.raises(PrivateIndexRefused, match="never indexed"):
        await repo.upsert_pending(private, _document_decision(private), now=T0)
    with pytest.raises(PrivateIndexRefused, match="never indexed"):
        await repo.upsert_pending(encrypted, _document_decision(encrypted), now=T0)
    excluded = await repo.upsert_pending(shared, _document_decision(shared), now=T0)

    assert excluded is None
    assert await repo.get_by_file_record_id(private.id) is None
    assert await repo.get_by_file_record_id(encrypted.id) is None
    assert await repo.get_by_file_record_id(shared.id) is None
    for record in (private, encrypted, shared):
        counts = await repo.counts_for_user(record.user_id)
        assert counts.total == 0
    assert captured_logs.messages("ERROR") == []
    assert all(record["exception"] is None for record in captured_logs.records)


@pytest.mark.asyncio
async def test_duplicate_file_record_id_returns_existing_row(
    session: AsyncSession,
    captured_logs: _LogCapture,
) -> None:
    repo = FileIndexLinkRepository(session)
    record = _record()
    await _save_file(session, record)
    created = await repo.upsert_pending(record, _decision_for(record), now=T0)
    assert created is not None

    other_key = f"users/{record.user_id}/files/renamed.pdf"
    again = await repo.upsert_pending(
        record,
        _document_decision(record, s3_key=other_key),
        now=T0 + timedelta(days=1),
    )

    assert again is not None
    assert again.id == created.id
    assert again.s3_key == created.s3_key
    assert again.state is IndexLinkState.PENDING
    assert again.updated_at == created.updated_at
    warnings = captured_logs.messages("WARNING")
    assert any(str(record.id) in message and "already exists" in message for message in warnings)
    assert captured_logs.messages("ERROR") == []
    assert all(record_item["exception"] is None for record_item in captured_logs.records)
    counts = await repo.counts_for_user(record.user_id)
    assert counts.total == 1
    assert counts.pending == 1


@pytest.mark.asyncio
async def test_duplicate_insert_race_returns_existing_row(
    session: AsyncSession,
    captured_logs: _LogCapture,
) -> None:
    repo = FileIndexLinkRepository(session)
    record = _record()
    await _save_file(session, record)
    created = await repo.upsert_pending(record, _decision_for(record), now=T0)
    assert created is not None

    real_lookup = repo._model_by_file_record_id
    calls = {"count": 0}

    async def hide_existing_once(file_record_id: UUID) -> FileIndexLink | None:
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return await real_lookup(file_record_id)

    repo._model_by_file_record_id = hide_existing_once  # type: ignore[method-assign]
    raced = await repo.upsert_pending(record, _decision_for(record), now=T0 + timedelta(days=1))

    assert raced is not None
    assert raced.id == created.id
    assert calls["count"] >= 2
    assert any("already exists" in message for message in captured_logs.messages("WARNING"))
    assert captured_logs.messages("ERROR") == []
    assert (await repo.counts_for_user(record.user_id)).total == 1


@pytest.mark.asyncio
async def test_claim_due_states_skips_future_next_attempt_at(
    session: AsyncSession,
    captured_logs: _LogCapture,
) -> None:
    repo = FileIndexLinkRepository(session)
    claim_now = T0 + timedelta(days=10)
    empty = await repo.claim_due(10, claim_now)
    assert empty == []
    assert any("No due index links" in message for message in captured_logs.messages("WARNING"))

    user_id = uuid4()
    specs: list[tuple[str, datetime, IndexLinkState, datetime | None]] = [
        ("pending", T0, IndexLinkState.PENDING, None),
        ("submitted", T0 + timedelta(hours=1), IndexLinkState.SUBMITTED, T0),
        ("removed", T0 + timedelta(hours=2), IndexLinkState.REMOVED, None),
        ("archived", T0 + timedelta(hours=3), IndexLinkState.ARCHIVED, claim_now),
        ("reindex", T0 + timedelta(hours=4), IndexLinkState.REINDEX_PENDING, None),
        ("future", T0 + timedelta(hours=5), IndexLinkState.PENDING, claim_now + timedelta(days=1)),
        ("indexed", T0 + timedelta(hours=6), IndexLinkState.INDEXED, None),
        ("failed", T0 + timedelta(hours=7), IndexLinkState.FAILED, T0),
        ("skipped", T0 + timedelta(hours=8), IndexLinkState.SKIPPED, None),
    ]
    links_by_name: dict[str, UUID] = {}
    for name, created_at, _state, _when in specs:
        record = _record(
            user_id=user_id,
            original_name=f"{name}.pdf",
            relative_path=f"users/{user_id}/files/{name}.pdf",
            created_at=created_at,
            last_accessed_at=created_at,
        )
        await _save_file(session, record)
        link = await repo.upsert_pending(record, _decision_for(record), now=created_at)
        assert link is not None
        links_by_name[name] = link.id
        await repo.apply_transition(
            link.id,
            state=_state,
            next_attempt_at=_when,
            rag_file_id=None if name in {"pending", "future", "skipped"} else uuid4(),
            now=created_at,
        )

    due = await repo.claim_due(10, claim_now)
    assert [item.id for item in due] == [
        links_by_name["pending"],
        links_by_name["submitted"],
        links_by_name["removed"],
        links_by_name["archived"],
        links_by_name["reindex"],
    ]
    assert {item.state for item in due} == set(DUE_INDEX_STATES)
    assert all(
        item.next_attempt_at is None or item.next_attempt_at <= claim_now for item in due
    )

    oldest_two = await repo.claim_due(2, claim_now)
    assert [item.id for item in oldest_two] == [
        links_by_name["pending"],
        links_by_name["submitted"],
    ]


@pytest.mark.asyncio
async def test_update_sets_updated_at(session: AsyncSession) -> None:
    repo = FileIndexLinkRepository(session)
    record = _record()
    await _save_file(session, record)
    created = await repo.upsert_pending(record, _decision_for(record), now=T0)
    assert created is not None
    assert created.updated_at == T0

    rag_file_id = uuid4()
    later = T0 + timedelta(days=1)
    submitted = await repo.apply_transition(
        created.id,
        state=IndexLinkState.SUBMITTED,
        rag_file_id=rag_file_id,
        attempts=1,
        next_attempt_at=later,
        last_error_code=None,
        now=later,
    )
    assert submitted is not None
    assert submitted.updated_at == later
    assert submitted.updated_at > created.updated_at
    assert submitted.state is IndexLinkState.SUBMITTED
    assert submitted.rag_file_id == rag_file_id
    assert submitted.attempts == 1
    found_by_rag = await repo.get_by_rag_file_id(rag_file_id)
    assert found_by_rag is not None
    assert found_by_rag.id == created.id

    poll_at = later + timedelta(minutes=5)
    polled = await repo.apply_transition(
        created.id,
        next_attempt_at=poll_at,
        attempts=1,
        now=poll_at,
    )
    assert polled is not None
    assert polled.state is IndexLinkState.SUBMITTED
    assert polled.next_attempt_at == poll_at
    assert polled.updated_at == later

    rekeyed_at = later + timedelta(days=1)
    new_key = f"users/{record.user_id}/files/moved.pdf"
    rekeyed = await repo.mark_rekey(record.id, new_key, now=rekeyed_at)
    assert rekeyed is not None
    assert rekeyed.state is IndexLinkState.PENDING
    assert rekeyed.s3_key == new_key
    assert rekeyed.rag_file_id == rag_file_id
    assert rekeyed.next_attempt_at == rekeyed_at
    assert rekeyed.updated_at == rekeyed_at

    changed_at = rekeyed_at + timedelta(hours=1)
    changed_hash = "b" * 64
    changed = await repo.mark_content_changed(
        record.id,
        RegistrationDecision(
            kind=RegistrationKind.DOCUMENT,
            s3_key=new_key,
            content_type=PDF_CONTENT_TYPE,
            content_sha256=changed_hash,
            skip_code=None,
        ),
        now=changed_at,
    )
    assert changed is not None
    assert changed.state is IndexLinkState.REINDEX_PENDING
    assert changed.attempts == 0
    assert changed.content_sha256 == changed_hash
    assert changed.rag_file_id == rag_file_id
    assert changed.updated_at == changed_at


@pytest.mark.asyncio
async def test_backfill_status_and_photo_lookup(session: AsyncSession) -> None:
    repo = FileIndexLinkRepository(session)
    user_id = uuid4()
    other_user = uuid4()
    oldest = _record(
        user_id=user_id,
        original_name="a.pdf",
        relative_path=f"users/{user_id}/files/a.pdf",
        created_at=T0,
        last_accessed_at=T0,
    )
    newer = _record(
        user_id=user_id,
        original_name="b.pdf",
        relative_path=f"users/{user_id}/files/b.pdf",
        created_at=T0 + timedelta(hours=1),
        last_accessed_at=T0,
    )
    linked = _record(
        user_id=user_id,
        original_name="c.pdf",
        relative_path=f"users/{user_id}/files/c.pdf",
        created_at=T0 + timedelta(hours=2),
        last_accessed_at=T0,
    )
    deleted = _record(
        user_id=user_id,
        original_name="gone.pdf",
        relative_path=f"users/{user_id}/files/gone.pdf",
        status=FileStatus.DELETED,
        created_at=T0 - timedelta(days=1),
        last_accessed_at=T0,
    )
    shared = _record(
        user_id=user_id,
        section=FileSection.SHARED,
        original_name="shared.pdf",
        relative_path="shared/docs/shared.pdf",
        created_at=T0,
        last_accessed_at=T0,
    )
    photo = _record(
        user_id=user_id,
        section=FileSection.PHOTOS,
        original_name="pic.jpg",
        relative_path=f"users/{user_id}/photos/originals/pic.jpg",
        mime_type="image/jpeg",
        created_at=T0 + timedelta(hours=3),
        last_accessed_at=T0,
    )
    other_photo = _record(
        user_id=other_user,
        section=FileSection.PHOTOS,
        original_name="other.jpg",
        relative_path=f"users/{other_user}/photos/originals/other.jpg",
        mime_type="image/jpeg",
        created_at=T0,
        last_accessed_at=T0,
    )
    for record in (oldest, newer, linked, deleted, shared, photo, other_photo):
        await _save_file(session, record)

    linked_row = await repo.upsert_pending(linked, _decision_for(linked), now=linked.created_at)
    assert linked_row is not None
    photo_row = await repo.upsert_pending(photo, _decision_for(photo), now=photo.created_at)
    other_row = await repo.upsert_pending(
        other_photo,
        _decision_for(other_photo),
        now=other_photo.created_at,
    )
    assert photo_row is not None
    assert other_row is not None
    photo_rag = uuid4()
    other_rag = uuid4()
    file_rag = uuid4()
    await repo.apply_transition(
        photo_row.id,
        state=IndexLinkState.INDEXED,
        rag_file_id=photo_rag,
        now=T0 + timedelta(days=1),
    )
    await repo.apply_transition(
        other_row.id,
        state=IndexLinkState.INDEXED,
        rag_file_id=other_rag,
        now=T0 + timedelta(days=1),
    )
    await repo.apply_transition(
        linked_row.id,
        state=IndexLinkState.FAILED,
        attempts=8,
        last_error_code="timeout",
        rag_file_id=file_rag,
        now=T0 + timedelta(days=2),
    )

    remaining_before = await repo.count_remaining()
    candidates = await repo.backfill_candidates(10)
    assert [record.id for record in candidates] == [oldest.id, newer.id]
    assert remaining_before == 2
    cursor = BackfillCursor(created_at=candidates[0].created_at, record_id=candidates[0].id)
    assert [record.id for record in await repo.backfill_candidates(10, cursor)] == [newer.id]
    assert await repo.count_remaining(cursor) == 1

    await repo.upsert_pending(oldest, _decision_for(oldest), now=oldest.created_at)
    failed = await repo.apply_transition(
        (await repo.get_by_file_record_id(oldest.id)).id,  # type: ignore[union-attr]
        state=IndexLinkState.FAILED,
        attempts=3,
        next_attempt_at=T0 + timedelta(days=3),
        last_error_code="duplicate_conflict",
        now=T0 + timedelta(days=3),
    )
    assert failed is not None
    snapshot = await repo.admin_snapshot()
    assert snapshot.by_state[IndexLinkState.FAILED.value] == 2
    assert snapshot.by_state[IndexLinkState.INDEXED.value] == 2
    assert snapshot.oldest_pending_at is None
    assert snapshot.last_errors == (
        IndexErrorCount(code="duplicate_conflict", count=1),
        IndexErrorCount(code="timeout", count=1),
    )

    retry_at = T0 + timedelta(days=4)
    requeued = await repo.retry_failed(1, now=retry_at)
    assert requeued == 1
    retried = await repo.get_by_file_record_id(oldest.id)
    assert retried is not None
    assert retried.state is IndexLinkState.PENDING
    assert retried.attempts == 0
    assert retried.next_attempt_at is None
    assert retried.last_error_code is None
    assert retried.updated_at == retry_at
    still_failed = await repo.get_by_file_record_id(linked.id)
    assert still_failed is not None
    assert still_failed.state is IndexLinkState.FAILED

    photos = await repo.indexed_photos_by_rag_file_id(
        user_id,
        [photo_rag, other_rag, file_rag],
    )
    assert list(photos) == [photo_rag]
    assert photos[photo_rag].file_record_id == photo.id

    removed = await repo.mark_removed([oldest.id, uuid4()], now=retry_at + timedelta(hours=1))
    assert [link.file_record_id for link in removed] == [oldest.id]
    assert removed[0].state is IndexLinkState.REMOVED
    archived = await repo.mark_archived(newer.id, now=retry_at)
    assert archived is None
    missing = await repo.mark_archived(linked.id, now=retry_at + timedelta(hours=2))
    assert missing is not None
    assert missing.state is IndexLinkState.ARCHIVED


@pytest.mark.asyncio
async def test_retry_failed_with_rag_file_id_moves_to_submitted(
    session: AsyncSession,
) -> None:
    repo = FileIndexLinkRepository(session)
    registered = _record(original_name="registered.pdf")
    fresh = _record(
        original_name="fresh.pdf",
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    await _save_file(session, registered)
    await _save_file(session, fresh)
    registered_link = await repo.upsert_pending(registered, _decision_for(registered), now=T0)
    fresh_link = await repo.upsert_pending(fresh, _decision_for(fresh), now=T0 + timedelta(minutes=1))
    assert registered_link is not None
    assert fresh_link is not None
    rag_file_id = uuid4()
    failed_at = T0 + timedelta(hours=1)
    await repo.apply_transition(
        registered_link.id,
        state=IndexLinkState.FAILED,
        rag_file_id=rag_file_id,
        attempts=4,
        next_attempt_at=failed_at,
        last_error_code="timeout",
        now=failed_at,
    )
    await repo.apply_transition(
        fresh_link.id,
        state=IndexLinkState.FAILED,
        attempts=2,
        next_attempt_at=failed_at,
        last_error_code="bad_request",
        now=failed_at,
    )

    retry_at = failed_at + timedelta(hours=1)
    requeued = await repo.retry_failed(10, now=retry_at)

    assert requeued == 2
    polled = await repo.get_by_file_record_id(registered.id)
    assert polled is not None
    assert polled.state is IndexLinkState.SUBMITTED
    assert polled.rag_file_id == rag_file_id
    assert polled.attempts == 0
    assert polled.next_attempt_at is None
    assert polled.last_error_code is None
    pending = await repo.get_by_file_record_id(fresh.id)
    assert pending is not None
    assert pending.state is IndexLinkState.PENDING
    assert pending.rag_file_id is None
    assert pending.attempts == 0
    assert pending.next_attempt_at is None
    assert pending.last_error_code is None


@pytest.mark.asyncio
async def test_backfill_includes_archived_files_and_photos(
    session: AsyncSession,
    captured_logs: _LogCapture,
) -> None:
    repo = FileIndexLinkRepository(session)
    user_id = uuid4()
    archived_file = _record(
        user_id=user_id,
        original_name="old-report.pdf",
        relative_path=f"users/{user_id}/files/old-report.pdf",
        status=FileStatus.ARCHIVED,
        is_archived=True,
        created_at=T0,
        last_accessed_at=T0,
    )
    photo_id = uuid4()
    archived_photo = _record(
        id=photo_id,
        user_id=user_id,
        section=FileSection.PHOTOS,
        original_name="old-shore.jpg",
        relative_path=f"users/{user_id}/photos/originals/{photo_id}.jpg",
        mime_type="image/jpeg",
        status=FileStatus.ARCHIVED,
        is_archived=True,
        created_at=T0 + timedelta(minutes=1),
        last_accessed_at=T0,
    )
    committed = _record(
        user_id=user_id,
        original_name="live.pdf",
        relative_path=f"users/{user_id}/files/live.pdf",
        created_at=T0 + timedelta(minutes=2),
        last_accessed_at=T0,
    )
    deleted = _record(
        user_id=user_id,
        original_name="gone.pdf",
        relative_path=f"users/{user_id}/files/gone.pdf",
        status=FileStatus.DELETED,
        created_at=T0 - timedelta(days=1),
        last_accessed_at=T0,
    )
    for record in (archived_file, archived_photo, committed, deleted):
        await _save_file(session, record)

    candidates = await repo.backfill_candidates(10)
    assert [record.id for record in candidates] == [
        archived_file.id,
        archived_photo.id,
        committed.id,
    ]

    names = await repo.original_names([archived_photo.id, uuid4()])
    assert names == {archived_photo.id: "old-shore.jpg"}
    logged = " ".join(captured_logs.messages("INFO") + captured_logs.messages("WARNING"))
    assert "old-shore.jpg" not in logged
    assert "old-report.pdf" not in logged

    for record in (archived_file, archived_photo):
        link = await repo.upsert_pending(record, _decision_for(record), now=record.created_at)
        assert link is not None
        assert link.state is IndexLinkState.SKIPPED
        assert link.last_error_code == IndexSkipCode.ARCHIVED.value


