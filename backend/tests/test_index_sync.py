"""Sync job: fake link repository and fake RAG index."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from loguru import logger

from app.application.indexing.sync_job import run_index_sync
from app.application.ports.index_links import UNSET, IndexLink, UnsetType
from app.application.ports.rag_index import (
    ERROR_CONTENT_CHANGED,
    ERROR_DEPENDENCY_UNAVAILABLE,
    ERROR_DUPLICATE_FILE,
    ERROR_FILE_BUSY,
    ERROR_TIMEOUT,
    RagDeleteResult,
    RagFileCard,
    RagIndexHttpError,
    RagIndexUnavailableError,
    RagManifest,
    RagObjectKey,
)
from app.domain.entities.file_record import FileSection
from app.domain.exceptions import FileNotFoundError as StorageObjectMissing
from app.domain.indexing import (
    JPEG_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    IndexLinkState,
    IndexSkipCode,
    preview_object_key,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
HASH = "ab" * 32
PAYLOAD = b"pdf-bytes"
_BACKOFF_AFTER_ONE_ATTEMPT = timedelta(seconds=60)
_BACKOFF_WHILE_PROCESSING = timedelta(seconds=30)


class FakeLinks:
    def __init__(
        self,
        links: list[IndexLink],
        names: dict[UUID, str] | None = None,
    ) -> None:
        self.links = {link.id: link for link in links}
        self._order = list(links)
        self.claim_limit: int | None = None
        self.names = dict(names or {})

    async def original_names(self, file_record_ids: list[UUID]) -> dict[UUID, str]:
        return {
            file_id: self.names[file_id]
            for file_id in file_record_ids
            if file_id in self.names
        }

    async def claim_due(self, limit: int, now: datetime) -> list[IndexLink]:
        self.claim_limit = limit
        return self._order[:limit]

    async def apply_transition(
        self,
        link_id: UUID,
        *,
        state: IndexLinkState | UnsetType = UNSET,
        attempts: int | UnsetType = UNSET,
        next_attempt_at: datetime | None | UnsetType = UNSET,
        rag_file_id: UUID | None | UnsetType = UNSET,
        last_error_code: str | None | UnsetType = UNSET,
        s3_key: str | UnsetType = UNSET,
        content_sha256: str | None | UnsetType = UNSET,
        content_type: str | UnsetType = UNSET,
        now: datetime | None = None,
    ) -> IndexLink | None:
        current = self.links.get(link_id)
        if current is None:
            return None
        changes: dict[str, object] = {}
        incoming = {
            "state": state,
            "attempts": attempts,
            "next_attempt_at": next_attempt_at,
            "rag_file_id": rag_file_id,
            "last_error_code": last_error_code,
            "s3_key": s3_key,
            "content_sha256": content_sha256,
            "content_type": content_type,
        }
        for key, value in incoming.items():
            if isinstance(value, UnsetType):
                continue
            changes[key] = value
        payload_keys = {
            "state",
            "rag_file_id",
            "last_error_code",
            "s3_key",
            "content_sha256",
            "content_type",
        }
        if any(key in changes for key in payload_keys):
            changes["updated_at"] = now or current.updated_at
        updated = IndexLink(**{**_as_dict(current), **changes})
        self.links[link_id] = updated
        return updated


class FakeRag:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.operation_errors: dict[str, Exception] = {}
        self.documents: list[RagManifest] = []
        self.images: list[RagManifest] = []
        self.rekeys: list[tuple[UUID, RagObjectKey]] = []
        self.deletes: list[UUID] = []
        self.user_ids: list[UUID] = []
        self.error: Exception | None = None
        self.fail_keys: set[str] = set()
        self.file_id = uuid4()
        self.poll_card: RagFileCard | None = None

    async def register_document(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        self.calls.append("register_document")
        self.user_ids.append(user_id)
        if manifest.s3_key in self.fail_keys:
            raise RuntimeError("boom")
        self._raise("register_document")
        self.documents.append(manifest)
        return _card(self.file_id, "uploaded")

    async def register_image(
        self,
        manifest: RagManifest,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        self.calls.append("register_image")
        self.user_ids.append(user_id)
        self._raise("register_image")
        self.images.append(manifest)
        return _card(self.file_id, "uploaded")

    async def get_file(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        self.calls.append("get_file")
        self.user_ids.append(user_id)
        self._raise("get_file")
        if self.poll_card is None:
            raise AssertionError("get_file was not stubbed")
        return self.poll_card

    async def rekey(
        self,
        rag_file_id: UUID,
        target: RagObjectKey,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagFileCard:
        self.calls.append("rekey")
        self.user_ids.append(user_id)
        self.rekeys.append((rag_file_id, target))
        self._raise("rekey")
        return _card(rag_file_id, "ready")

    async def delete_vectors(
        self,
        rag_file_id: UUID,
        *,
        user_id: UUID,
        request_id: UUID | None = None,
    ) -> RagDeleteResult:
        self.calls.append("delete_vectors")
        self.user_ids.append(user_id)
        self.deletes.append(rag_file_id)
        self._raise("delete_vectors")
        return RagDeleteResult(file_id=rag_file_id, status="deleting")

    async def search_images(
        self,
        q: str,
        *,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        request_id: UUID | None = None,
    ) -> object:
        raise AssertionError("search_images is not part of the sync tick")

    async def health(self, *, request_id: UUID | None = None) -> bool:
        raise AssertionError("health is not part of the sync tick")

    async def aclose(self) -> None:
        return None

    def _raise(self, operation: str) -> None:
        error = self.operation_errors.get(operation)
        if error is not None:
            raise error
        if self.error is not None:
            raise self.error


class FakeObjects:
    def __init__(
        self,
        bodies: dict[str, bytes],
        *,
        missing_type: type[Exception] = FileNotFoundError,
    ) -> None:
        self.bodies = dict(bodies)
        self.reads: list[str] = []
        self.sizes: list[str] = []
        self.exists_calls: list[str] = []
        self.missing_type = missing_type

    async def exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return path in self.bodies

    def _missing(self, path: str) -> Exception:
        return self.missing_type(f"File {path!r} not found")

    async def get_size(self, path: str) -> int:
        self.sizes.append(path)
        try:
            return len(self.bodies[path])
        except KeyError as exc:
            raise self._missing(path) from exc

    async def read(self, path: str):
        self.reads.append(path)
        try:
            payload = self.bodies[path]
        except KeyError as exc:
            raise self._missing(path) from exc
        yield payload


def _link(**overrides: object) -> IndexLink:
    user_id = uuid4()
    fields: dict[str, object] = {
        "id": uuid4(),
        "file_record_id": uuid4(),
        "user_id": user_id,
        "section": FileSection.FILES,
        "s3_key": f"users/{user_id}/files/report.pdf",
        "content_sha256": HASH,
        "content_type": PDF_CONTENT_TYPE,
        "rag_file_id": None,
        "state": IndexLinkState.PENDING,
        "attempts": 0,
        "next_attempt_at": None,
        "last_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return IndexLink(**fields)  # type: ignore[arg-type]


def _as_dict(link: IndexLink) -> dict[str, object]:
    return {
        "id": link.id,
        "file_record_id": link.file_record_id,
        "user_id": link.user_id,
        "section": link.section,
        "s3_key": link.s3_key,
        "content_sha256": link.content_sha256,
        "content_type": link.content_type,
        "rag_file_id": link.rag_file_id,
        "state": link.state,
        "attempts": link.attempts,
        "next_attempt_at": link.next_attempt_at,
        "last_error_code": link.last_error_code,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def _card(file_id: UUID, status: str, error: str | None = None) -> RagFileCard:
    return RagFileCard(
        file_id=file_id,
        status=status,
        error=error,
        content_type=None,
        file_name=None,
        file_size=None,
        page_count=None,
    )


async def _sync(
    links: list[IndexLink],
    *,
    rag: FakeRag | None = None,
    bodies: dict[str, bytes] | None = None,
    names: dict[UUID, str] | None = None,
    missing_type: type[Exception] = FileNotFoundError,
    max_attempts: int = 8,
    index_max_wait_seconds: int = 3600,
):
    repo = FakeLinks(links, names)
    rag_client = rag or FakeRag()
    stored = bodies if bodies is not None else {link.s3_key: PAYLOAD for link in links}
    objects = FakeObjects(stored, missing_type=missing_type)
    result = await run_index_sync(
        repo,
        rag_client,
        objects,
        batch_size=20,
        max_attempts=max_attempts,
        index_max_wait_seconds=index_max_wait_seconds,
        now=NOW,
    )
    return result, repo, rag_client, objects


@pytest.mark.asyncio
async def test_pending_document_is_registered() -> None:
    link = _link()
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        result, repo, rag, objects = await _sync([link])
    finally:
        logger.remove(sink)

    stored = repo.links[link.id]
    manifest = rag.documents[0]
    assert result.transitioned == 1
    assert rag.calls == ["register_document"]
    assert rag.user_ids == [link.user_id]
    assert manifest.s3_key == link.s3_key
    assert manifest.file_name == "report.pdf"
    assert manifest.content_sha256 == HASH
    assert manifest.file_size == len(PAYLOAD)
    assert manifest.content_type == PDF_CONTENT_TYPE
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.rag_file_id == rag.file_id
    assert stored.attempts == 0
    assert stored.last_error_code is None
    assert objects.bodies == {link.s3_key: PAYLOAD}
    logged = "\n".join(messages)
    assert "report.pdf" not in logged
    assert PAYLOAD.decode() not in logged


@pytest.mark.asyncio
async def test_pending_image_is_registered() -> None:
    user_id = uuid4()
    link = _link(
        user_id=user_id,
        section=FileSection.PHOTOS,
        s3_key=f"users/{user_id}/photos/originals/photo.jpg",
        content_type=JPEG_CONTENT_TYPE,
    )

    _result, repo, rag, _objects = await _sync([link], bodies={link.s3_key: b"jpeg"})

    assert rag.calls == ["register_image"]
    assert rag.documents == []
    assert rag.images[0].file_name == "photo.jpg"
    assert rag.images[0].file_size == len(b"jpeg")
    assert rag.images[0].content_type == JPEG_CONTENT_TYPE
    assert rag.images[0].content_sha256 == HASH
    assert rag.user_ids == [user_id]
    assert repo.links[link.id].state is IndexLinkState.SUBMITTED
    assert repo.links[link.id].rag_file_id == rag.file_id


@pytest.mark.asyncio
async def test_pending_preview_registers_image_bytes_from_the_preview_key() -> None:
    user_id = uuid4()
    file_id = uuid4()
    key = preview_object_key(user_id, file_id)
    body = b"preview-jpeg"
    link = _link(
        user_id=user_id,
        file_record_id=file_id,
        section=FileSection.PHOTOS,
        s3_key=key,
        content_sha256=None,
        content_type=JPEG_CONTENT_TYPE,
    )

    _result, repo, rag, objects = await _sync([link], bodies={key: body})

    manifest = rag.images[0]
    assert rag.calls == ["register_image"]
    assert objects.reads == [key]
    assert manifest.s3_key == key
    assert manifest.content_sha256 == hashlib.sha256(body).hexdigest()
    assert manifest.file_size == len(body)
    assert manifest.content_type == JPEG_CONTENT_TYPE
    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.content_sha256 == manifest.content_sha256
    assert stored.rag_file_id == rag.file_id


@pytest.mark.asyncio
async def test_rename_rekeys_the_same_rag_file() -> None:
    rag_file_id = uuid4()
    link = _link(rag_file_id=rag_file_id)

    _result, repo, rag, _objects = await _sync([link])

    assert rag.calls == ["rekey"]
    assert rag.deletes == []
    assert rag.documents == []
    assert rag.images == []
    rekeyed_id, target = rag.rekeys[0]
    assert rekeyed_id == rag_file_id
    assert target.s3_key == link.s3_key
    assert target.content_sha256 == HASH
    assert target.file_size == len(PAYLOAD)
    assert rag.user_ids == [link.user_id]
    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.INDEXED
    assert stored.rag_file_id == rag_file_id


@pytest.mark.asyncio
async def test_content_replace_deletes_vectors_then_registers() -> None:
    old_id = uuid4()
    new_hash = "cd" * 32
    link = _link(
        state=IndexLinkState.REINDEX_PENDING,
        rag_file_id=old_id,
        content_sha256=new_hash,
    )

    _result, repo, rag, _objects = await _sync([link])

    assert rag.calls == ["delete_vectors", "register_document"]
    assert rag.deletes == [old_id]
    assert rag.documents[0].content_sha256 == new_hash
    assert rag.documents[0].s3_key == link.s3_key
    assert rag.documents[0].file_size == len(PAYLOAD)
    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.rag_file_id == rag.file_id
    assert stored.rag_file_id != old_id


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [IndexLinkState.REMOVED, IndexLinkState.ARCHIVED])
@pytest.mark.parametrize(
    "delete_error",
    [
        None,
        RagIndexHttpError("missing", status_code=404, error_code="not_found"),
        RagIndexHttpError("busy", status_code=409, error_code=ERROR_FILE_BUSY),
    ],
)
async def test_delete_vectors_on_remove_keeps_the_object(
    terminal: IndexLinkState,
    delete_error: RagIndexHttpError | None,
) -> None:
    rag_file_id = uuid4()
    link = _link(state=terminal, rag_file_id=rag_file_id)
    rag = FakeRag()
    rag.error = delete_error

    _result, repo, rag, objects = await _sync([link], rag=rag)

    assert rag.calls == ["delete_vectors"]
    assert rag.deletes == [rag_file_id]
    assert rag.documents == []
    assert rag.images == []
    assert rag.rekeys == []
    stored = repo.links[link.id]
    assert stored.state is terminal
    assert stored.rag_file_id == rag_file_id
    assert stored.next_attempt_at is not None
    assert stored.next_attempt_at > NOW
    assert objects.bodies[link.s3_key] == PAYLOAD
    assert objects.reads == []


@pytest.mark.asyncio
async def test_removed_without_rag_file_does_not_call_rag() -> None:
    link = _link(state=IndexLinkState.REMOVED, rag_file_id=None)

    _result, repo, rag, objects = await _sync([link])

    assert rag.calls == []
    assert objects.reads == []
    assert objects.sizes == []
    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.REMOVED
    assert stored.next_attempt_at is not None
    assert stored.next_attempt_at > NOW


@pytest.mark.asyncio
async def test_duplicate_file_uses_details_file_id() -> None:
    adopted = uuid4()
    link = _link()
    rag = FakeRag()
    rag.error = RagIndexHttpError(
        "duplicate",
        status_code=409,
        error_code=ERROR_DUPLICATE_FILE,
        details={"file_id": str(adopted)},
    )

    result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.transitioned == 1
    assert result.failed == 0
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.rag_file_id == adopted


@pytest.mark.asyncio
async def test_unavailable_schedules_retry_and_does_not_raise() -> None:
    link = _link()
    rag = FakeRag()
    rag.error = RagIndexUnavailableError(
        "rag down",
        error_code=ERROR_DEPENDENCY_UNAVAILABLE,
    )

    result, repo, rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.retried == 1
    assert result.failed == 0
    assert stored.state is IndexLinkState.PENDING
    assert stored.attempts == 1
    assert stored.next_attempt_at == NOW + _BACKOFF_AFTER_ONE_ATTEMPT
    assert stored.last_error_code == ERROR_DEPENDENCY_UNAVAILABLE
    assert rag.calls == ["register_document"]


@pytest.mark.asyncio
async def test_max_attempts_sets_failed() -> None:
    link = _link(attempts=7)
    rag = FakeRag()
    rag.error = RagIndexUnavailableError("timed out", error_code=ERROR_TIMEOUT)

    result, repo, rag, _objects = await _sync([link], rag=rag, max_attempts=8)

    stored = repo.links[link.id]
    assert result.failed == 1
    assert result.retried == 0
    assert stored.state is IndexLinkState.FAILED
    assert stored.attempts == 8
    assert stored.last_error_code == ERROR_TIMEOUT
    assert stored.next_attempt_at is None
    assert rag.calls == ["register_document"]


@pytest.mark.asyncio
async def test_skipped_row_does_not_call_rag() -> None:
    link = _link(
        state=IndexLinkState.SKIPPED,
        last_error_code="unsupported_type",
    )

    result, repo, rag, objects = await _sync([link])

    assert result.skipped == 1
    assert result.transitioned == 0
    assert rag.calls == []
    assert objects.reads == []
    assert objects.sizes == []
    assert objects.exists_calls == []
    assert repo.links[link.id].state is IndexLinkState.SKIPPED
    assert repo.links[link.id].last_error_code == "unsupported_type"


@pytest.mark.asyncio
async def test_submitted_ready_becomes_indexed() -> None:
    rag_file_id = uuid4()
    link = _link(
        state=IndexLinkState.SUBMITTED,
        rag_file_id=rag_file_id,
        updated_at=NOW - timedelta(minutes=5),
    )
    rag = FakeRag()
    rag.poll_card = _card(rag_file_id, "ready")

    _result, repo, rag, objects = await _sync([link], rag=rag)

    assert rag.calls == ["get_file"]
    assert objects.reads == []
    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.INDEXED
    assert stored.rag_file_id == rag_file_id


@pytest.mark.asyncio
async def test_submitted_still_processing_waits_without_moving_updated_at() -> None:
    rag_file_id = uuid4()
    link = _link(
        state=IndexLinkState.SUBMITTED,
        rag_file_id=rag_file_id,
        updated_at=NOW,
    )
    rag = FakeRag()
    rag.poll_card = _card(rag_file_id, "processing")

    result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.retried == 1
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.updated_at == NOW
    assert stored.attempts == 0
    assert stored.next_attempt_at == NOW + _BACKOFF_WHILE_PROCESSING


@pytest.mark.asyncio
async def test_submitted_past_max_wait_fails_timeout() -> None:
    rag_file_id = uuid4()
    link = _link(
        state=IndexLinkState.SUBMITTED,
        rag_file_id=rag_file_id,
        updated_at=NOW - timedelta(seconds=3600),
    )
    rag = FakeRag()
    rag.poll_card = _card(rag_file_id, "uploaded")

    _result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert stored.state is IndexLinkState.FAILED
    assert stored.last_error_code == ERROR_TIMEOUT


@pytest.mark.asyncio
async def test_unexpected_error_on_one_row_continues_the_batch() -> None:
    first = _link(s3_key="users/owner/files/first.pdf")
    second = _link(s3_key="users/owner/files/second.pdf")
    rag = FakeRag()
    rag.fail_keys.add(first.s3_key)

    result, repo, rag, _objects = await _sync([first, second], rag=rag)

    assert rag.calls == ["register_document", "register_document"]
    assert result.failed == 1
    assert result.transitioned == 1
    assert repo.links[first.id].state is IndexLinkState.PENDING
    assert repo.links[second.id].state is IndexLinkState.SUBMITTED
    assert repo.links[second.id].rag_file_id == rag.file_id


@pytest.mark.asyncio
async def test_domain_missing_object_on_rekey_fails_object_not_found() -> None:
    user_id = uuid4()
    key = f"users/{user_id}/files/report.pdf"
    link = _link(user_id=user_id, s3_key=key, rag_file_id=uuid4())
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        result, repo, rag, _objects = await _sync(
            [link],
            bodies={},
            missing_type=StorageObjectMissing,
        )
    finally:
        logger.remove(sink)

    stored = repo.links[link.id]
    assert result.failed == 1
    assert result.retried == 0
    assert rag.calls == []
    assert stored.state is IndexLinkState.FAILED
    assert stored.last_error_code == "object_not_found"
    assert stored.next_attempt_at is None
    logged = "\n".join(messages)
    assert key not in logged
    assert "report.pdf" not in logged


@pytest.mark.asyncio
async def test_photo_manifest_uses_original_name() -> None:
    user_id = uuid4()
    file_id = uuid4()
    link = _link(
        user_id=user_id,
        file_record_id=file_id,
        section=FileSection.PHOTOS,
        s3_key=f"users/{user_id}/photos/originals/{file_id}.jpg",
        content_type=JPEG_CONTENT_TYPE,
    )

    _result, _repo, rag, _objects = await _sync(
        [link],
        bodies={link.s3_key: b"jpeg"},
        names={file_id: "Holiday.jpg"},
    )

    assert rag.images[0].file_name == "Holiday.jpg"
    assert f"{file_id}.jpg" not in rag.images[0].file_name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(400, "bad_request"), (404, "not_found"), (415, "unsupported_media_type")],
)
async def test_register_client_error_fails_immediately(
    status_code: int,
    error_code: str,
) -> None:
    link = _link()
    rag = FakeRag()
    rag.error = RagIndexHttpError(
        "rejected",
        status_code=status_code,
        error_code=error_code,
    )

    result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.failed == 1
    assert result.retried == 0
    assert stored.state is IndexLinkState.FAILED
    assert stored.last_error_code == error_code
    assert stored.next_attempt_at is None
    assert stored.rag_file_id is None


@pytest.mark.asyncio
async def test_duplicate_without_file_id_is_duplicate_conflict() -> None:
    link = _link()
    rag = FakeRag()
    rag.error = RagIndexHttpError(
        "duplicate",
        status_code=409,
        error_code=ERROR_DUPLICATE_FILE,
        details={},
    )

    result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.failed == 1
    assert stored.state is IndexLinkState.FAILED
    assert stored.last_error_code == "duplicate_conflict"
    assert stored.rag_file_id is None
    assert stored.next_attempt_at is None


@pytest.mark.asyncio
async def test_rekey_content_changed_deletes_and_registers() -> None:
    old_id = uuid4()
    link = _link(rag_file_id=old_id)
    rag = FakeRag()
    rag.operation_errors["rekey"] = RagIndexHttpError(
        "changed",
        status_code=409,
        error_code=ERROR_CONTENT_CHANGED,
    )

    result, repo, rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.transitioned == 1
    assert rag.calls == ["rekey", "delete_vectors", "register_document"]
    assert rag.deletes == [old_id]
    assert stored.state is IndexLinkState.SUBMITTED
    assert stored.rag_file_id == rag.file_id
    assert stored.rag_file_id != old_id
    assert stored.last_error_code is None


@pytest.mark.asyncio
async def test_preview_missing_backs_off_then_skips() -> None:
    user_id = uuid4()
    file_id = uuid4()
    key = preview_object_key(user_id, file_id)
    waiting = _link(
        user_id=user_id,
        file_record_id=file_id,
        section=FileSection.PHOTOS,
        s3_key=key,
        content_sha256=None,
        content_type=JPEG_CONTENT_TYPE,
        attempts=0,
    )

    retry, retry_repo, retry_rag, retry_objects = await _sync([waiting], bodies={})

    retried = retry_repo.links[waiting.id]
    assert retry.retried == 1
    assert retry_rag.calls == []
    assert retry_objects.reads == []
    assert retried.state is IndexLinkState.PENDING
    assert retried.attempts == 1
    assert retried.next_attempt_at == NOW + _BACKOFF_AFTER_ONE_ATTEMPT
    assert retried.last_error_code == IndexSkipCode.PREVIEW_MISSING.value

    exhausted = _link(
        user_id=user_id,
        file_record_id=file_id,
        section=FileSection.PHOTOS,
        s3_key=key,
        content_sha256=None,
        content_type=JPEG_CONTENT_TYPE,
        attempts=7,
    )
    skipped, skipped_repo, skipped_rag, _objects = await _sync(
        [exhausted],
        bodies={},
        max_attempts=8,
    )

    stored = skipped_repo.links[exhausted.id]
    assert skipped.skipped == 1
    assert skipped_rag.calls == []
    assert stored.state is IndexLinkState.SKIPPED
    assert stored.attempts == 8
    assert stored.next_attempt_at is None
    assert stored.last_error_code == IndexSkipCode.PREVIEW_MISSING.value


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [IndexLinkState.REMOVED, IndexLinkState.ARCHIVED])
async def test_non_terminal_delete_error_keeps_state_until_attempts_exhausted(
    terminal: IndexLinkState,
) -> None:
    rag_file_id = uuid4()
    link = _link(state=terminal, rag_file_id=rag_file_id, attempts=1)
    rag = FakeRag()
    rag.error = RagIndexHttpError("upstream", status_code=500, error_code="upstream")

    result, repo, _rag, _objects = await _sync([link], rag=rag)

    stored = repo.links[link.id]
    assert result.retried == 1
    assert result.failed == 0
    assert stored.state is terminal
    assert stored.rag_file_id == rag_file_id
    assert stored.attempts == 2
    assert stored.next_attempt_at == NOW + timedelta(seconds=120)
    assert stored.last_error_code == "upstream"

    exhausted = _link(state=terminal, rag_file_id=rag_file_id, attempts=7)
    failed_rag = FakeRag()
    failed_rag.error = RagIndexHttpError("upstream", status_code=500, error_code="upstream")
    failed, failed_repo, _failed_rag, _failed_objects = await _sync(
        [exhausted],
        rag=failed_rag,
        max_attempts=8,
    )

    done = failed_repo.links[exhausted.id]
    assert failed.failed == 1
    assert done.state is IndexLinkState.FAILED
    assert done.attempts == 8
    assert done.next_attempt_at is None
    assert done.last_error_code == "upstream"
