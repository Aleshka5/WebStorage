"""Caller queue counts, operator snapshot, and one retry batch."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from loguru import logger

from app.application.ports.index_links import (
    IndexErrorCount,
    IndexLinkRepository,
    UserIndexCounts,
)
from app.application.ports.rag_index import RagIndex, RagIndexUnavailableError
from app.domain.indexing import IndexLinkState
from config import get_settings


@dataclass(frozen=True)
class AdminIndexStatus:
    """Operator view of the queue plus a live RAG health probe."""

    by_state: dict[str, int]
    oldest_pending_seconds: int | None
    last_errors: tuple[IndexErrorCount, ...]
    rag_reachable: bool


async def user_index_status(links: IndexLinkRepository, user_id: UUID) -> UserIndexCounts:
    """Return queue counts for ``user_id`` only."""
    counts = await links.counts_for_user(user_id)
    logger.info(
        "Index status fetched user_id={} indexed={} pending={} failed={} skipped={} total={}",
        user_id,
        counts.indexed,
        counts.pending,
        counts.failed,
        counts.skipped,
        counts.total,
    )
    return counts


async def admin_index_status(
    links: IndexLinkRepository,
    rag: RagIndex,
    *,
    user_id: UUID,
    request_id: UUID | None = None,
    now: datetime | None = None,
) -> AdminIndexStatus:
    """Return queue facts and whether RAG answered ``GET /health``.

    When RAG is disabled the probe is skipped and ``rag_reachable`` is false.
    """
    snapshot = await links.admin_snapshot()
    reachable = await _rag_reachable(rag, request_id=request_id)
    oldest_pending_seconds = _oldest_pending_seconds(snapshot.oldest_pending_at, now=now)
    logger.info(
        "Admin index snapshot fetched user_id={} rag_reachable={} oldest_pending_seconds={}",
        user_id,
        reachable,
        oldest_pending_seconds,
    )
    return AdminIndexStatus(
        by_state=dict(snapshot.by_state),
        oldest_pending_seconds=oldest_pending_seconds,
        last_errors=snapshot.last_errors,
        rag_reachable=reachable,
    )


async def retry_failed_index(
    links: IndexLinkRepository,
    *,
    user_id: UUID,
    state: str | None,
) -> int:
    """Move one batch of ``FAILED`` rows back to ``PENDING``.

    ``state`` may be omitted. Any other value is rejected.
    """
    if state is not None and state != IndexLinkState.FAILED.value:
        logger.error("Index retry rejected state={} user_id={}", state, user_id)
        raise ValueError("Index retry accepts only FAILED")

    batch_size = get_settings().rag.index_batch_size
    if batch_size < 1:
        logger.error("Index retry batch size is invalid batch_size={}", batch_size)
        raise ValueError("index_batch_size must be at least 1")

    requeued = await links.retry_failed(batch_size)
    logger.info(
        "Index retry finished user_id={} requeued={} batch_size={}",
        user_id,
        requeued,
        batch_size,
    )
    return requeued


async def _rag_reachable(rag: RagIndex, *, request_id: UUID | None) -> bool:
    if not get_settings().rag.enabled:
        logger.warning("RAG is disabled; admin index probe skipped")
        return False
    try:
        reachable = await rag.health(request_id=request_id)
    except RagIndexUnavailableError as exc:
        logger.warning(
            "RAG health probe failed status_code={} error_code={}",
            exc.status_code,
            exc.error_code,
        )
        return False
    if not reachable:
        logger.warning("RAG health probe reported not ready")
    return reachable


def _oldest_pending_seconds(
    oldest_pending_at: datetime | None,
    *,
    now: datetime | None,
) -> int | None:
    if oldest_pending_at is None:
        return None
    current = _as_utc(now if now is not None else datetime.now(UTC))
    pending_at = _as_utc(oldest_pending_at)
    elapsed = int((current - pending_at).total_seconds())
    if elapsed < 0:
        return 0
    return elapsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
