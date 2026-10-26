"""Repository port for the ``file_index_links`` queue.

Later slices import this module directly:

``from app.application.ports.index_links import IndexLinkRepository``
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.indexing import IndexLinkState, RegistrationDecision


class UnsetType:
    """Leave one ``apply_transition`` column unchanged."""


UNSET = UnsetType()


@dataclass(frozen=True)
class IndexLink:
    """One ``file_index_links`` row as the application layer sees it."""

    id: UUID
    file_record_id: UUID | None
    user_id: UUID
    section: FileSection
    s3_key: str
    content_sha256: str | None
    content_type: str
    rag_file_id: UUID | None
    state: IndexLinkState
    attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserIndexCounts:
    """Caller-facing queue counts. Named buckets are those states only.

    ``SUBMITTED``, ``REINDEX_PENDING``, ``REMOVED``, and ``ARCHIVED`` are
    included in ``total`` and not in ``pending``.
    """

    indexed: int
    pending: int
    failed: int
    skipped: int
    total: int


@dataclass(frozen=True)
class IndexErrorCount:
    """How many links currently store one ``last_error_code``."""

    code: str
    count: int


@dataclass(frozen=True)
class AdminIndexSnapshot:
    """Queue facts the admin status route can serve without calling RAG.

    ``oldest_pending_at`` is the ``created_at`` of the oldest ``PENDING`` row.
    The route turns that into ``oldest_pending_seconds``. ``rag_reachable``
    is not a database fact.
    """

    by_state: Mapping[str, int]
    oldest_pending_at: datetime | None
    last_errors: tuple[IndexErrorCount, ...]


@dataclass(frozen=True)
class BackfillCursor:
    """Keyset position ``(created_at, file_records.id)`` for backfill."""

    created_at: datetime
    record_id: UUID


class IndexLinkRepository(Protocol):
    """Persistence for enqueue, sync, backfill, and status.

    ``upsert_pending`` raises ``PrivateIndexRefused`` for a ``PRIVATE`` or
    encrypted record and writes nothing. An excluded section returns ``None``.
    A second insert for the same ``file_record_id`` returns the existing row.
    """

    async def upsert_pending(
        self,
        record: FileRecord,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        """Insert ``PENDING`` or ``SKIPPED`` for one file record."""

    async def get_by_file_record_id(self, file_record_id: UUID) -> IndexLink | None:
        """Return the link for one file record, if it exists."""

    async def get_by_rag_file_id(self, rag_file_id: UUID) -> IndexLink | None:
        """Return the link that stores this RAG file id."""

    async def list_by_user_state(
        self,
        user_id: UUID,
        state: IndexLinkState,
    ) -> list[IndexLink]:
        """Return one user's links in ``state``, oldest first."""

    async def mark_content_changed(
        self,
        file_record_id: UUID,
        decision: RegistrationDecision,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        """``REINDEX_PENDING`` when ``rag_file_id`` is set, else refresh ``PENDING``."""

    async def mark_rekey(
        self,
        file_record_id: UUID,
        s3_key: str,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        """Keep ``rag_file_id``, set ``PENDING`` and ``next_attempt_at``.

        A row that has no ``rag_file_id`` only updates ``s3_key``.
        """

    async def mark_removed(
        self,
        file_record_ids: Sequence[UUID],
        *,
        now: datetime | None = None,
    ) -> list[IndexLink]:
        """Move existing links to ``REMOVED``. Missing rows are skipped."""

    async def mark_archived(
        self,
        file_record_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IndexLink | None:
        """Move an existing link to ``ARCHIVED``. Missing row returns ``None``."""

    async def claim_due(self, limit: int, now: datetime) -> list[IndexLink]:
        """Oldest due rows in ``DUE_INDEX_STATES``, at most ``limit``.

        A row is due when ``next_attempt_at`` is null or ``<= now``.
        PostgreSQL locks the claimed rows (``FOR UPDATE SKIP LOCKED``) until
        the caller commits. ``limit < 1`` raises ``ValueError``.
        """

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
        """Write queue columns. ``UNSET`` keeps the stored value; ``None`` clears it.

        ``updated_at`` moves when state, ``s3_key``, ``content_sha256``,
        ``content_type``, ``rag_file_id``, or ``last_error_code`` changes.
        Updating only ``attempts`` and ``next_attempt_at`` leaves ``updated_at``
        so a ``SUBMITTED`` wait is measured from the transition into that state.
        """

    async def backfill_candidates(
        self,
        limit: int,
        after: BackfillCursor | None = None,
    ) -> list[FileRecord]:
        """Committed or archived Files and Photos records that have no link, oldest first."""

    async def count_remaining(self, after_enqueued: BackfillCursor | None = None) -> int:
        """How many backfill candidates remain at ``after_enqueued``."""

    async def counts_for_user(self, user_id: UUID) -> UserIndexCounts:
        """Status counts for one owner."""

    async def admin_snapshot(self) -> AdminIndexSnapshot:
        """Counts by state, oldest pending instant, and error-code totals."""

    async def retry_failed(self, limit: int, *, now: datetime | None = None) -> int:
        """Requeue up to ``limit`` ``FAILED`` rows.

        A row that already has ``rag_file_id`` becomes ``SUBMITTED`` so the
        next tick polls. A row that never registered becomes ``PENDING``.
        ``attempts`` resets to 0. ``next_attempt_at`` and ``last_error_code``
        are cleared.
        """

    async def original_names(self, file_record_ids: Sequence[UUID]) -> dict[UUID, str]:
        """``file_records.original_name`` for the given ids. Missing rows are omitted."""

    async def indexed_photos_by_rag_file_id(
        self,
        user_id: UUID,
        rag_file_ids: Sequence[UUID],
    ) -> dict[UUID, IndexLink]:
        """Indexed Photos links for this owner, keyed by ``rag_file_id``."""
