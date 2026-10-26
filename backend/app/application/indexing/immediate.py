"""Register or delete RAG vectors for links that were just committed.

Enqueue still only writes the queue row. The request calls
:func:`notify_index_links` after its own commit, in a separate session.
``index_sync`` remains the backup when this attempt fails or races the
scheduler. Polling ``SUBMITTED`` to ``INDEXED`` stays on the scheduler.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID

from loguru import logger

from app.application.indexing.sync_job import IndexSyncJob, ObjectStorageReader, _Outcome
from app.application.ports.index_links import IndexLink, IndexLinkRepository
from app.application.ports.rag_index import RagIndex
from app.domain.entities.file_record import FileSection
from app.domain.indexing import IndexLinkState
from config import get_settings

_INDEXED_SECTIONS = frozenset({FileSection.FILES, FileSection.PHOTOS})
_queued_ids: ContextVar[list[UUID] | None] = ContextVar(
    "immediate_index_file_ids",
    default=None,
)


@contextmanager
def capture_immediate_index_ids() -> Iterator[list[UUID]]:
    """Collect file ids queued by upload and delete inside this block.

    Rename, overwrite, and archive do not record ids. The list stays
    populated after the block so the caller can notify after ``commit``.
    """
    ids: list[UUID] = []
    token = _queued_ids.set(ids)
    try:
        yield ids
    finally:
        _queued_ids.reset(token)


def note_immediate_index_ids(file_record_ids: Sequence[UUID]) -> None:
    """Remember ids when a request has opened :func:`capture_immediate_index_ids`."""
    bucket = _queued_ids.get()
    if bucket is None:
        return
    for file_record_id in file_record_ids:
        if file_record_id not in bucket:
            bucket.append(file_record_id)


async def notify_index_links(
    links: IndexLinkRepository,
    rag: RagIndex,
    objects: ObjectStorageReader,
    file_record_ids: Sequence[UUID],
    *,
    now: datetime | None = None,
) -> None:
    """Register or delete the given links. Does not commit and does not claim.

    ``PENDING`` without ``rag_file_id`` is a registration (HTTP 202 moves the
    row to ``SUBMITTED``). ``REMOVED`` deletes vectors when ``rag_file_id``
    is set, and settles locally when it is not. ``SKIPPED`` is not sent.
    Other states stay on the scheduler. Failures are logged; the caller
    still returns the upload or delete response.
    """
    ordered_ids = list(dict.fromkeys(file_record_ids))
    if not ordered_ids:
        return
    if not get_settings().rag.enabled:
        logger.warning(
            "RAG indexing is disabled; immediate index notify was not sent for {} file records",
            len(ordered_ids),
        )
        return

    job = IndexSyncJob(links, rag, objects)
    for file_record_id in ordered_ids:
        link = await links.get_by_file_record_id(file_record_id)
        if link is None:
            logger.warning(
                "Immediate index notify found no link; scheduler will retry "
                "file_record_id={} rag_file_id=None state=missing",
                file_record_id,
            )
            continue
        if not _should_notify(link):
            continue
        logger.info(
            "Immediate index notify user_id={} file_record_id={} rag_file_id={} state={}",
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            link.state.value,
        )
        try:
            outcome = await job.process_link(link, now)
        except Exception as exc:
            logger.warning(
                "Immediate index notify failed; scheduler will retry "
                "user_id={} file_record_id={} rag_file_id={} state={} error_type={}",
                link.user_id,
                link.file_record_id,
                link.rag_file_id,
                link.state.value,
                type(exc).__name__,
            )
            continue
        if outcome is _Outcome.RETRIED:
            logger.warning(
                "Immediate index notify failed; scheduler will retry "
                "user_id={} file_record_id={} rag_file_id={} state={}",
                link.user_id,
                link.file_record_id,
                link.rag_file_id,
                link.state.value,
            )


def _should_notify(link: IndexLink) -> bool:
    if link.section not in _INDEXED_SECTIONS:
        logger.warning(
            "Immediate index notify ignored excluded section {} user_id={} "
            "file_record_id={} rag_file_id={} state={}",
            link.section.value,
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            link.state.value,
        )
        return False
    if link.state is IndexLinkState.SKIPPED:
        logger.info(
            "Immediate index notify skipped unsupported type user_id={} "
            "file_record_id={} rag_file_id={} state={}",
            link.user_id,
            link.file_record_id,
            link.rag_file_id,
            link.state.value,
        )
        return False
    if link.state is IndexLinkState.REMOVED:
        return True
    if link.state is IndexLinkState.PENDING and link.rag_file_id is None:
        return True
    logger.info(
        "Immediate index notify left user_id={} file_record_id={} "
        "rag_file_id={} state={} for the scheduler",
        link.user_id,
        link.file_record_id,
        link.rag_file_id,
        link.state.value,
    )
    return False
