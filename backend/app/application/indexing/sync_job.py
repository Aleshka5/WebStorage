"""Drain due ``file_index_links`` rows into the RAG index.

One tick claims a single batch and talks to RAG. The link repository only
flushes. This module does not commit and does not register APScheduler.

Commit ownership matches the archive and cleanup jobs: the caller opens the
session, calls :func:`run_index_sync`, then commits. Roll the session back
if this coroutine raises. ``RagIndexUnavailableError`` is recorded on the
row and does not raise, so an RAG outage does not fail the process.

``REMOVED`` and ``ARCHIVED`` stay in the due set while ``next_attempt_at``
is null. After RAG confirms the vectors are gone, ``next_attempt_at`` is
set far in the future so the row is kept for admin counts and is not
claimed again. This job never deletes the MinIO object.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from loguru import logger

from app.application.ports.index_links import (
    UNSET,
    IndexLink,
    IndexLinkRepository,
    UnsetType,
)
from app.application.ports.rag_index import (
    ERROR_CONTENT_CHANGED,
    ERROR_DEPENDENCY_UNAVAILABLE,
    ERROR_DUPLICATE_FILE,
    ERROR_FILE_BUSY,
    ERROR_TIMEOUT,
    RagFileCard,
    RagIndex,
    RagIndexHttpError,
    RagIndexUnavailableError,
    RagManifest,
    RagObjectKey,
)
from app.domain.exceptions import FileNotFoundError as StorageObjectMissing
from app.domain.indexing import (
    DOCX_CONTENT_TYPE,
    GIF_CONTENT_TYPE,
    JPEG_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    PNG_CONTENT_TYPE,
    WEBP_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    IndexLinkState,
    IndexSkipCode,
)
from config import get_settings

# MinIO raises the domain error. Tests and other readers may raise the builtin.
_MISSING_OBJECT = (StorageObjectMissing, FileNotFoundError)

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_DOCUMENT_CONTENT_TYPES = frozenset(
    {PDF_CONTENT_TYPE, DOCX_CONTENT_TYPE, XLSX_CONTENT_TYPE}
)
_IMAGE_CONTENT_TYPES = frozenset(
    {JPEG_CONTENT_TYPE, PNG_CONTENT_TYPE, WEBP_CONTENT_TYPE, GIF_CONTENT_TYPE}
)
_STATUS_READY = "ready"
_STATUS_ERROR = "error"
_GONE_STATUSES = frozenset({"deleting", "deleted"})
_OBJECT_NOT_FOUND = "object_not_found"
_DUPLICATE_CONFLICT = "duplicate_conflict"
_PREVIEW_KEY_MARKER = "/photos/previews/"
_PREVIEW_SUFFIX = "_thumb.jpg"
# claim_due treats null next_attempt_at as due now. A confirmed removal stays
# REMOVED or ARCHIVED, so the only way to leave the queue is a future stamp.
_CONFIRMED_REMOVAL_AT = datetime(9999, 1, 1, tzinfo=UTC)


class ObjectStorageReader(Protocol):
    """Read a storage object by the key already stored on an index link.

    A disk-root ``StorageAdapter`` (empty root prefix) satisfies this.
    ``read``, ``exists``, and ``get_size`` then take keys such as
    ``users/{user_id}/files/...`` and ``users/{user_id}/photos/previews/...``.
    """

    def read(self, path: str) -> AsyncIterator[bytes]:
        """Yield the object body. Raise ``FileNotFoundError`` when it is absent."""

    async def exists(self, path: str) -> bool:
        """Return whether the object is present."""

    async def get_size(self, path: str) -> int:
        """Return the object size in bytes."""


class _Outcome(StrEnum):
    TRANSITIONED = "transitioned"
    RETRIED = "retried"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class IndexSyncResult:
    """Counts for one tick.

    ``retried`` includes an RAG outage backoff and a ``SUBMITTED`` row that
    is still ``uploaded`` or ``processing``. ``failed`` counts rows moved
    to ``FAILED`` and rows whose unexpected error was logged and skipped.
    """

    claimed: int
    transitioned: int
    retried: int
    failed: int
    skipped: int


class IndexSyncJob:
    """One batch of due index links.

    Inject the link repository, the RAG port, and an object reader.
    Tuning arguments override ``get_settings().rag``. The RAG client's
    timeout stays inside the client.
    """

    def __init__(
        self,
        links: IndexLinkRepository,
        rag: RagIndex,
        objects: ObjectStorageReader,
        *,
        batch_size: int | None = None,
        max_attempts: int | None = None,
        index_max_wait_seconds: int | None = None,
    ) -> None:
        self._links = links
        self._rag = rag
        self._objects = objects
        self._batch_size = _tuning(
            batch_size,
            lambda: get_settings().rag.index_batch_size,
        )
        self._max_attempts = _tuning(
            max_attempts,
            lambda: get_settings().rag.max_attempts,
        )
        self._max_wait_seconds = _tuning(
            index_max_wait_seconds,
            lambda: get_settings().rag.index_max_wait_seconds,
        )

    async def run(self, now: datetime | None = None) -> IndexSyncResult:
        """Claim one batch and apply RAG transitions. Does not commit."""
        if self._batch_size < 1:
            raise ValueError("index batch size must be at least 1")
        if self._max_attempts < 1:
            raise ValueError("max attempts must be at least 1")

        moment = _as_utc(now or datetime.now(UTC))
        logger.info("Index sync tick started batch_size={}", self._batch_size)
        claimed = await self._links.claim_due(self._batch_size, moment)
        if not claimed:
            logger.warning("Index sync tick claimed no due links")
            result = IndexSyncResult(
                claimed=0,
                transitioned=0,
                retried=0,
                failed=0,
                skipped=0,
            )
            _log_finished(result)
            return result

        counts = {outcome: 0 for outcome in _Outcome}
        for link in claimed:
            outcome = await self._process(link, moment)
            counts[outcome] += 1

        result = IndexSyncResult(
            claimed=len(claimed),
            transitioned=counts[_Outcome.TRANSITIONED],
            retried=counts[_Outcome.RETRIED],
            failed=counts[_Outcome.FAILED],
            skipped=counts[_Outcome.SKIPPED],
        )
        _log_finished(result)
        return result

    async def _process(self, link: IndexLink, now: datetime) -> _Outcome:
        try:
            try:
                return await self._dispatch(link, now)
            except RagIndexUnavailableError as exc:
                code = exc.error_code or ERROR_DEPENDENCY_UNAVAILABLE
                return await self._schedule_retry(link, now, code)
            except RagIndexHttpError as exc:
                return await self._fail(link, now, _http_error_code(exc))
        except Exception:
            logger.exception("Failed to process index link {}", link.id)
            return _Outcome.FAILED

    async def _dispatch(self, link: IndexLink, now: datetime) -> _Outcome:
        if link.state is IndexLinkState.SKIPPED:
            logger.warning(
                "Index link {} is skipped and was not sent user_id={} "
                "file_record_id={} error_code={}",
                link.id,
                link.user_id,
                link.file_record_id,
                link.last_error_code,
            )
            return _Outcome.SKIPPED
        if link.state is IndexLinkState.SUBMITTED:
            return await self._poll(link, now)
        if link.state is IndexLinkState.REMOVED:
            return await self._remove(link, now, IndexLinkState.REMOVED)
        if link.state is IndexLinkState.ARCHIVED:
            return await self._remove(link, now, IndexLinkState.ARCHIVED)
        if link.state is IndexLinkState.REINDEX_PENDING:
            return await self._reindex(link, now)
        if link.state is IndexLinkState.PENDING and link.rag_file_id is not None:
            return await self._rekey(link, now)
        if link.state is IndexLinkState.PENDING:
            return await self._register(link, now)
        logger.warning(
            "Index link {} in state {} was not handled",
            link.id,
            link.state,
        )
        return _Outcome.SKIPPED

    async def _register(self, link: IndexLink, now: datetime) -> _Outcome:
        if _is_document(link):
            register = self._rag.register_document
        elif _is_image(link):
            register = self._rag.register_image
        else:
            return await self._skip(link, now, IndexSkipCode.UNSUPPORTED_TYPE.value)

        prepared = await self._manifest(link, now)
        if isinstance(prepared, _Outcome):
            return prepared

        try:
            card = await register(
                prepared,
                user_id=link.user_id,
                request_id=uuid4(),
            )
        except RagIndexHttpError as exc:
            return await self._on_register_http(link, now, exc, prepared)
        return await self._accept_registration(link, now, card.file_id, prepared)

    async def _reindex(self, link: IndexLink, now: datetime) -> _Outcome:
        if link.rag_file_id is not None:
            outcome = await self._delete_vectors(link, now)
            if outcome is not None:
                return outcome
            cleared = await self._write(
                link,
                now,
                rag_file_id=None,
                announce=False,
            )
            if cleared is None:
                return _Outcome.FAILED
            link = cleared
        return await self._register(link, now)

    async def _rekey(self, link: IndexLink, now: datetime) -> _Outcome:
        if link.rag_file_id is None:
            return await self._register(link, now)
        located = await self._locate(link, now)
        if isinstance(located, _Outcome):
            return located
        digest, size = located
        target = RagObjectKey(
            s3_key=link.s3_key,
            file_size=size,
            content_sha256=digest,
        )
        try:
            card = await self._rag.rekey(
                link.rag_file_id,
                target,
                user_id=link.user_id,
                request_id=uuid4(),
            )
        except RagIndexHttpError as exc:
            return await self._on_rekey_http(link, now, exc)
        kept_id = card.file_id if card.file_id is not None else link.rag_file_id
        return await self._succeed(
            link,
            now,
            state=IndexLinkState.INDEXED,
            rag_file_id=kept_id,
            content_sha256=_hash_update(link, digest),
        )

    async def _poll(self, link: IndexLink, now: datetime) -> _Outcome:
        if link.rag_file_id is None:
            logger.warning(
                "Index link {} is SUBMITTED without a RAG file id user_id={} file_record_id={}",
                link.id,
                link.user_id,
                link.file_record_id,
            )
            return await self._register(link, now)
        try:
            card = await self._rag.get_file(
                link.rag_file_id,
                user_id=link.user_id,
                request_id=uuid4(),
            )
        except RagIndexHttpError as exc:
            if exc.status_code == _HTTP_NOT_FOUND:
                return await self._settle(link, now, IndexLinkState.REMOVED)
            return await self._fail(link, now, _http_error_code(exc))
        return await self._on_polled_card(link, now, card)

    async def _on_polled_card(
        self,
        link: IndexLink,
        now: datetime,
        card: RagFileCard,
    ) -> _Outcome:
        status = (card.status or "").strip().lower()
        if status == _STATUS_READY:
            return await self._succeed(
                link,
                now,
                state=IndexLinkState.INDEXED,
                rag_file_id=card.file_id,
            )
        if status == _STATUS_ERROR:
            return await self._fail(link, now, card.error or _STATUS_ERROR)
        if status in _GONE_STATUSES:
            return await self._settle(link, now, IndexLinkState.REMOVED)
        if _elapsed_seconds(now, link.updated_at) >= self._max_wait_seconds:
            return await self._fail(link, now, ERROR_TIMEOUT)
        next_at = now + _backoff(link.attempts)
        await self._write(link, now, next_attempt_at=next_at, announce=False)
        logger.warning(
            "Index link {} still indexing user_id={} file_record_id={} "
            "rag_file_id={} next_attempt_at={}",
            link.id,
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            next_at.isoformat(),
        )
        return _Outcome.RETRIED

    async def _remove(
        self,
        link: IndexLink,
        now: datetime,
        terminal: IndexLinkState,
    ) -> _Outcome:
        if link.rag_file_id is None:
            return await self._settle(link, now, terminal)
        outcome = await self._delete_vectors(link, now)
        if outcome is not None:
            return outcome
        return await self._settle(link, now, terminal)

    async def _delete_vectors(self, link: IndexLink, now: datetime) -> _Outcome | None:
        """Return ``None`` when the vectors are gone.

        A non-terminal HTTP error on ``REMOVED`` or ``ARCHIVED`` keeps that
        state, increments attempts, and sets ``next_attempt_at``. The row
        becomes ``FAILED`` only after attempts are exhausted. Other states
        fail immediately. ``404`` and ``409 file_busy`` count as gone.
        """
        if link.rag_file_id is None:
            return None
        try:
            await self._rag.delete_vectors(
                link.rag_file_id,
                user_id=link.user_id,
                request_id=uuid4(),
            )
        except RagIndexHttpError as exc:
            if _vectors_already_gone(exc):
                return None
            if link.state in (IndexLinkState.REMOVED, IndexLinkState.ARCHIVED):
                return await self._schedule_retry(link, now, _http_error_code(exc))
            await self._fail(link, now, _http_error_code(exc))
            return _Outcome.FAILED
        return None

    async def _manifest(self, link: IndexLink, now: datetime) -> RagManifest | _Outcome:
        located = await self._locate(link, now)
        if isinstance(located, _Outcome):
            return located
        digest, size = located
        original_name = await self._display_name(link)
        return RagManifest(
            s3_key=link.s3_key,
            file_name=_file_name(link.s3_key, original_name),
            content_sha256=digest,
            file_size=size,
            content_type=link.content_type,
        )

    async def _display_name(self, link: IndexLink) -> str | None:
        if link.file_record_id is None:
            return None
        names = await self._links.original_names((link.file_record_id,))
        return names.get(link.file_record_id)

    async def _locate(
        self,
        link: IndexLink,
        now: datetime,
    ) -> tuple[str, int] | _Outcome:
        if link.content_sha256 is None:
            return await self._read_object_hash(link, now)
        try:
            size = await self._objects.get_size(link.s3_key)
        except _MISSING_OBJECT:
            return await self._fail(link, now, _OBJECT_NOT_FOUND)
        return link.content_sha256, size

    async def _read_object_hash(
        self,
        link: IndexLink,
        now: datetime,
    ) -> tuple[str, int] | _Outcome:
        try:
            present = await self._objects.exists(link.s3_key)
        except _MISSING_OBJECT:
            present = False
        if not present:
            return await self._miss_object(link, now)
        try:
            payload = await _read_all(self._objects, link.s3_key)
        except _MISSING_OBJECT:
            return await self._miss_object(link, now)
        return hashlib.sha256(payload).hexdigest(), len(payload)

    async def _miss_object(self, link: IndexLink, now: datetime) -> _Outcome:
        if not _is_preview_key(link.s3_key):
            return await self._fail(link, now, _OBJECT_NOT_FOUND)
        attempts = link.attempts + 1
        if attempts >= self._max_attempts:
            logger.warning(
                "Index link {} preview missing user_id={} file_record_id={} "
                "state={} error_code={}",
                link.id,
                link.user_id,
                link.file_record_id,
                IndexLinkState.SKIPPED,
                IndexSkipCode.PREVIEW_MISSING.value,
            )
            await self._write(
                link,
                now,
                state=IndexLinkState.SKIPPED,
                attempts=attempts,
                next_attempt_at=None,
                last_error_code=IndexSkipCode.PREVIEW_MISSING.value,
                announce=False,
            )
            return _Outcome.SKIPPED
        next_at = now + _backoff(attempts)
        logger.warning(
            "Index link {} preview missing and will retry user_id={} "
            "file_record_id={} attempts={} next_attempt_at={} error_code={}",
            link.id,
            link.user_id,
            link.file_record_id,
            attempts,
            next_at.isoformat(),
            IndexSkipCode.PREVIEW_MISSING.value,
        )
        await self._write(
            link,
            now,
            attempts=attempts,
            next_attempt_at=next_at,
            last_error_code=IndexSkipCode.PREVIEW_MISSING.value,
            announce=False,
        )
        return _Outcome.RETRIED

    async def _on_register_http(
        self,
        link: IndexLink,
        now: datetime,
        exc: RagIndexHttpError,
        manifest: RagManifest,
    ) -> _Outcome:
        if exc.status_code == _HTTP_CONFLICT and exc.error_code == ERROR_DUPLICATE_FILE:
            return await self._adopt_duplicate(link, now, exc, manifest.content_sha256)
        return await self._fail(link, now, _http_error_code(exc))

    async def _adopt_duplicate(
        self,
        link: IndexLink,
        now: datetime,
        exc: RagIndexHttpError,
        content_sha256: str,
    ) -> _Outcome:
        adopted = _detail_file_id(exc.details)
        if adopted is None:
            return await self._fail(link, now, _DUPLICATE_CONFLICT)
        if link.rag_file_id is not None and link.rag_file_id != adopted:
            return await self._fail(link, now, _DUPLICATE_CONFLICT)
        logger.warning(
            "Index link {} adopted duplicate RAG file user_id={} "
            "file_record_id={} rag_file_id={}",
            link.id,
            link.user_id,
            link.file_record_id,
            adopted,
        )
        return await self._succeed(
            link,
            now,
            state=IndexLinkState.SUBMITTED,
            rag_file_id=adopted,
            content_sha256=_hash_update(link, content_sha256),
        )

    async def _on_rekey_http(
        self,
        link: IndexLink,
        now: datetime,
        exc: RagIndexHttpError,
    ) -> _Outcome:
        if exc.status_code == _HTTP_CONFLICT and exc.error_code == ERROR_CONTENT_CHANGED:
            logger.warning(
                "Index link {} rekey saw content change user_id={} "
                "file_record_id={} rag_file_id={} error_code={}",
                link.id,
                link.user_id,
                link.file_record_id,
                link.rag_file_id,
                ERROR_CONTENT_CHANGED,
            )
            updated = await self._write(
                link,
                now,
                state=IndexLinkState.REINDEX_PENDING,
                next_attempt_at=None,
                last_error_code=ERROR_CONTENT_CHANGED,
            )
            if updated is None:
                return _Outcome.FAILED
            return await self._reindex(updated, now)
        if exc.status_code == _HTTP_NOT_FOUND:
            return await self._fail(link, now, exc.error_code or _OBJECT_NOT_FOUND)
        return await self._fail(link, now, _http_error_code(exc))

    async def _accept_registration(
        self,
        link: IndexLink,
        now: datetime,
        rag_file_id: UUID,
        manifest: RagManifest,
    ) -> _Outcome:
        return await self._succeed(
            link,
            now,
            state=IndexLinkState.SUBMITTED,
            rag_file_id=rag_file_id,
            content_sha256=_hash_update(link, manifest.content_sha256),
        )

    async def _succeed(
        self,
        link: IndexLink,
        now: datetime,
        *,
        state: IndexLinkState,
        rag_file_id: UUID | None | UnsetType = UNSET,
        content_sha256: str | None | UnsetType = UNSET,
    ) -> _Outcome:
        await self._write(
            link,
            now,
            state=state,
            attempts=0,
            next_attempt_at=None,
            rag_file_id=rag_file_id,
            last_error_code=None,
            content_sha256=content_sha256,
        )
        return _Outcome.TRANSITIONED

    async def _settle(
        self,
        link: IndexLink,
        now: datetime,
        terminal: IndexLinkState,
    ) -> _Outcome:
        await self._write(
            link,
            now,
            state=terminal,
            attempts=0,
            next_attempt_at=_CONFIRMED_REMOVAL_AT,
            last_error_code=None,
        )
        return _Outcome.TRANSITIONED

    async def _skip(self, link: IndexLink, now: datetime, code: str) -> _Outcome:
        logger.warning(
            "Index link {} skipped user_id={} file_record_id={} state={} error_code={}",
            link.id,
            link.user_id,
            link.file_record_id,
            IndexLinkState.SKIPPED,
            code,
        )
        await self._write(
            link,
            now,
            state=IndexLinkState.SKIPPED,
            next_attempt_at=None,
            last_error_code=code,
            announce=False,
        )
        return _Outcome.SKIPPED

    async def _fail(self, link: IndexLink, now: datetime, code: str) -> _Outcome:
        logger.error(
            "Index link {} failed user_id={} file_record_id={} rag_file_id={} "
            "state={} error_code={}",
            link.id,
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            IndexLinkState.FAILED,
            code,
        )
        await self._write(
            link,
            now,
            state=IndexLinkState.FAILED,
            next_attempt_at=None,
            last_error_code=code,
            announce=False,
        )
        return _Outcome.FAILED

    async def _schedule_retry(
        self,
        link: IndexLink,
        now: datetime,
        code: str,
    ) -> _Outcome:
        attempts = link.attempts + 1
        if attempts >= self._max_attempts:
            logger.error(
                "Index link {} exhausted attempts user_id={} file_record_id={} "
                "rag_file_id={} state={} error_code={}",
                link.id,
                link.user_id,
                link.file_record_id,
                link.rag_file_id,
                IndexLinkState.FAILED,
                code,
            )
            await self._write(
                link,
                now,
                state=IndexLinkState.FAILED,
                attempts=attempts,
                next_attempt_at=None,
                last_error_code=code,
                announce=False,
            )
            return _Outcome.FAILED
        next_at = now + _backoff(attempts)
        # SUBMITTED wait is measured from updated_at. Leave last_error_code
        # unchanged on that state so a transport retry does not reset the clock.
        error_code: str | None | UnsetType = (
            UNSET if link.state is IndexLinkState.SUBMITTED else code
        )
        logger.warning(
            "Index link {} will retry user_id={} file_record_id={} rag_file_id={} "
            "attempts={} next_attempt_at={} error_code={}",
            link.id,
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            attempts,
            next_at.isoformat(),
            code,
        )
        await self._write(
            link,
            now,
            attempts=attempts,
            next_attempt_at=next_at,
            last_error_code=error_code,
            announce=False,
        )
        return _Outcome.RETRIED

    async def _write(
        self,
        link: IndexLink,
        now: datetime,
        *,
        state: IndexLinkState | UnsetType = UNSET,
        attempts: int | UnsetType = UNSET,
        next_attempt_at: datetime | None | UnsetType = UNSET,
        rag_file_id: UUID | None | UnsetType = UNSET,
        last_error_code: str | None | UnsetType = UNSET,
        content_sha256: str | None | UnsetType = UNSET,
        announce: bool = True,
    ) -> IndexLink | None:
        updated = await self._links.apply_transition(
            link.id,
            state=state,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            rag_file_id=rag_file_id,
            last_error_code=last_error_code,
            content_sha256=content_sha256,
            now=now,
        )
        if updated is None:
            logger.warning("Index link {} was not found for transition", link.id)
            return None
        if announce:
            _log_transition(link, updated)
        return updated


async def run_index_sync(
    links: IndexLinkRepository,
    rag: RagIndex,
    objects: ObjectStorageReader,
    *,
    batch_size: int | None = None,
    max_attempts: int | None = None,
    index_max_wait_seconds: int | None = None,
    now: datetime | None = None,
) -> IndexSyncResult:
    """Drain one due batch. ``main.py`` calls this, then commits the session.

    The repository does not commit. Passing ``batch_size``, ``max_attempts``,
    or ``index_max_wait_seconds`` overrides ``get_settings().rag`` for this
    tick. Omit them to read ``index_batch_size``, ``max_attempts``, and
    ``index_max_wait_seconds``. Per-call HTTP timeout belongs to the RAG client.
    """
    job = IndexSyncJob(
        links,
        rag,
        objects,
        batch_size=batch_size,
        max_attempts=max_attempts,
        index_max_wait_seconds=index_max_wait_seconds,
    )
    return await job.run(now=now)


def _tuning(value: int | None, fallback: Callable[[], int]) -> int:
    if value is not None:
        return value
    return fallback()


def _log_finished(result: IndexSyncResult) -> None:
    logger.info(
        "Index sync tick finished claimed={} transitioned={} retried={} failed={} skipped={}",
        result.claimed,
        result.transitioned,
        result.retried,
        result.failed,
        result.skipped,
    )


def _log_transition(before: IndexLink, after: IndexLink) -> None:
    logger.info(
        "Index link {} transitioned user_id={} file_record_id={} rag_file_id={} "
        "state={} error_code={}",
        before.id,
        before.user_id,
        before.file_record_id,
        after.rag_file_id,
        after.state,
        after.last_error_code,
    )


def _hash_update(link: IndexLink, digest: str) -> str | UnsetType:
    if link.content_sha256 == digest:
        return UNSET
    return digest


def _is_document(link: IndexLink) -> bool:
    return link.content_type in _DOCUMENT_CONTENT_TYPES


def _is_image(link: IndexLink) -> bool:
    return link.content_type in _IMAGE_CONTENT_TYPES


def _is_preview_key(s3_key: str) -> bool:
    return _PREVIEW_KEY_MARKER in s3_key and s3_key.endswith(_PREVIEW_SUFFIX)


def _file_name(s3_key: str, original_name: str | None = None) -> str:
    """Prefer the file record's ``original_name`` over the object-key basename.

    Photo keys are ``{id}.jpg`` or ``{id}_thumb.jpg``. The manifest name is
    the name the user stored.
    """
    if original_name is not None:
        cleaned = original_name.strip()
        if cleaned and cleaned not in {".", ".."}:
            return cleaned
    name = PurePosixPath(s3_key).name
    if not name or name in {".", ".."}:
        return "file"
    return name


def _backoff(attempts: int) -> timedelta:
    exponent = max(attempts, 0)
    seconds = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**exponent))
    return timedelta(seconds=seconds)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _elapsed_seconds(now: datetime, updated_at: datetime) -> float:
    return (_as_utc(now) - _as_utc(updated_at)).total_seconds()


def _vectors_already_gone(exc: RagIndexHttpError) -> bool:
    if exc.status_code == _HTTP_NOT_FOUND:
        return True
    return exc.status_code == _HTTP_CONFLICT and exc.error_code == ERROR_FILE_BUSY


def _http_error_code(exc: RagIndexHttpError) -> str:
    if exc.error_code:
        return exc.error_code
    return f"http_{exc.status_code}"


def _detail_file_id(details: object) -> UUID | None:
    if not isinstance(details, dict):
        return None
    raw = details.get("file_id")
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


async def _read_all(objects: ObjectStorageReader, key: str) -> bytes:
    chunks: list[bytes] = []
    async for chunk in objects.read(key):
        chunks.append(chunk)
    return b"".join(chunks)
