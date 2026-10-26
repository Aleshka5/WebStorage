"""One page of backfill into the index queue.

Startup and the admin route both call :func:`backfill`. The function reads
``get_settings().rag`` unless the caller passes ``backfill_enabled``,
``index_batch_size``, and ``max_image_bytes``. It never calls RAG and never
drains ``claim_due``.
"""

from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

from app.application.ports.index_links import BackfillCursor, IndexLinkRepository
from app.domain.entities.file_record import FileRecord
from app.domain.indexing import IndexLinkState, PrivateIndexRefused, decide_registration_target
from config import get_settings

_LIMIT_BATCH_FACTOR = 10


class BackfillStatus(StrEnum):
    """Whether this call wrote a page or left the queue untouched."""

    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of a single backfill pass.

    ``status`` is ``skipped`` when backfill is disabled. Counts are then zero,
    ``next_cursor`` is ``None``, and the repository is not called.
    """

    examined: int
    enqueued: int
    skipped: int
    next_cursor: BackfillCursor | None
    remaining: int
    status: BackfillStatus


async def backfill(
    links: IndexLinkRepository,
    *,
    limit: int | None = None,
    after: BackfillCursor | None = None,
    backfill_enabled: bool | None = None,
    index_batch_size: int | None = None,
    max_image_bytes: int | None = None,
) -> BackfillResult:
    """Enqueue at most one page of ``PENDING`` or ``SKIPPED`` link rows.

    Startup uses the RAG settings, including the on/off switch::

        await backfill(links)

    When ``backfill_enabled`` is false the result status is ``skipped`` and
    nothing is written.

    The admin route asks for one page even if startup backfill is off::

        await backfill(links, limit=limit, backfill_enabled=True)

    ``limit`` defaults to ``index_batch_size * 10``. Pass
    ``result.next_cursor`` as ``after`` to read the next page. This function
    does not loop until ``remaining`` is 0.
    """
    enabled, batch_size, image_cap = _resolve_options(
        backfill_enabled=backfill_enabled,
        index_batch_size=index_batch_size,
        max_image_bytes=max_image_bytes,
    )
    if not enabled:
        logger.warning("Index backfill is disabled; skipping this pass")
        return _skipped_result()

    page_limit = _page_limit(limit, batch_size)
    logger.info("Starting index backfill pass limit={}", page_limit)
    candidates = await links.backfill_candidates(page_limit, after)
    examined, enqueued, skipped = await _enqueue_page(links, candidates, image_cap)
    next_cursor = _cursor_after(candidates)
    remaining_at = next_cursor if next_cursor is not None else after
    remaining = await links.count_remaining(remaining_at)
    logger.info(
        "Index backfill pass finished examined={} enqueued={} skipped={} remaining={}",
        examined,
        enqueued,
        skipped,
        remaining,
    )
    return BackfillResult(
        examined=examined,
        enqueued=enqueued,
        skipped=skipped,
        next_cursor=next_cursor,
        remaining=remaining,
        status=BackfillStatus.COMPLETED,
    )


def _resolve_options(
    *,
    backfill_enabled: bool | None,
    index_batch_size: int | None,
    max_image_bytes: int | None,
) -> tuple[bool, int, int]:
    if (
        backfill_enabled is not None
        and index_batch_size is not None
        and max_image_bytes is not None
    ):
        return backfill_enabled, index_batch_size, max_image_bytes

    rag = get_settings().rag
    return (
        rag.backfill_enabled if backfill_enabled is None else backfill_enabled,
        rag.index_batch_size if index_batch_size is None else index_batch_size,
        rag.max_image_bytes if max_image_bytes is None else max_image_bytes,
    )


def _page_limit(limit: int | None, index_batch_size: int) -> int:
    if index_batch_size < 1:
        raise ValueError("index_batch_size must be at least 1")
    resolved = index_batch_size * _LIMIT_BATCH_FACTOR if limit is None else limit
    if resolved < 1:
        raise ValueError("backfill limit must be at least 1")
    return resolved


def _skipped_result() -> BackfillResult:
    return BackfillResult(
        examined=0,
        enqueued=0,
        skipped=0,
        next_cursor=None,
        remaining=0,
        status=BackfillStatus.SKIPPED,
    )


def _cursor_after(candidates: list[FileRecord]) -> BackfillCursor | None:
    if not candidates:
        return None
    last = candidates[-1]
    return BackfillCursor(created_at=last.created_at, record_id=last.id)


async def _enqueue_page(
    links: IndexLinkRepository,
    candidates: list[FileRecord],
    max_image_bytes: int,
) -> tuple[int, int, int]:
    examined = 0
    enqueued = 0
    skipped = 0
    for record in candidates:
        examined += 1
        if await _enqueue_record(links, record, max_image_bytes):
            enqueued += 1
        else:
            skipped += 1
    return examined, enqueued, skipped


async def _enqueue_record(
    links: IndexLinkRepository,
    record: FileRecord,
    max_image_bytes: int,
) -> bool:
    """Return whether the repository accepted a ``PENDING`` row."""
    try:
        decision = decide_registration_target(record, max_image_bytes=max_image_bytes)
        link = await links.upsert_pending(record, decision)
    except PrivateIndexRefused:
        logger.warning("Index backfill refused private file record {}", record.id)
        return False
    except Exception:
        logger.exception("Failed to backfill file record {}", record.id)
        return False
    return link is not None and link.state is IndexLinkState.PENDING
