"""Request-path updates of the ``file_index_links`` queue.

Enqueue does not call RAG and does not commit. The caller owns ``commit``.
After a successful commit, create and delete of Files and Photos call
``notify_index_links``. Rename, move, overwrite, and archive stay on the
``index_sync`` scheduler. A queue failure propagates so that session rolls
back.
"""

from collections.abc import Sequence
from uuid import UUID

from loguru import logger

from app.application.indexing.immediate import note_immediate_index_ids
from app.application.ports.index_links import IndexLink, IndexLinkRepository
from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.indexing import (
    IndexSkipCode,
    PrivateIndexRefused,
    decide_registration_target,
)
from config import get_settings

_INDEXED_SECTIONS = frozenset({FileSection.FILES, FileSection.PHOTOS})


def index_enqueue_active(links: IndexLinkRepository | None) -> bool:
    """Whether this call should write a link row.

    A missing collaborator stays silent so unwired sections keep today's
    behaviour. Indexing switched off logs a warning and writes nothing.
    """
    return _queue(links) is not None


async def enqueue_upload(
    links: IndexLinkRepository | None,
    record: FileRecord,
) -> None:
    """Queue one newly committed Files or Photos record."""
    active = _queue(links, file_record_id=record.id)
    if active is None or _refused_without_row(record):
        return
    try:
        decision = decide_registration_target(
            record,
            max_image_bytes=get_settings().rag.max_image_bytes,
        )
        if decision.skip_code is IndexSkipCode.EXCLUDED_SECTION:
            _warn_excluded(record)
            return
        link = await active.upsert_pending(record, decision)
    except PrivateIndexRefused:
        _warn_private(record)
        return
    _log_queued(link)
    if link is not None and link.file_record_id is not None:
        note_immediate_index_ids((link.file_record_id,))


async def enqueue_content_changed(
    links: IndexLinkRepository | None,
    record: FileRecord,
) -> None:
    """Record a content overwrite. The repository chooses ``REINDEX_PENDING``."""
    active = _queue(links, file_record_id=record.id)
    if active is None or _refused_without_row(record):
        return
    try:
        decision = decide_registration_target(
            record,
            max_image_bytes=get_settings().rag.max_image_bytes,
        )
        if decision.skip_code is IndexSkipCode.EXCLUDED_SECTION:
            _warn_excluded(record)
            return
        link = await active.mark_content_changed(record.id, decision)
    except PrivateIndexRefused:
        _warn_private(record)
        return
    _log_queued(link)


async def enqueue_rekey(
    links: IndexLinkRepository | None,
    record: FileRecord,
) -> None:
    """Record a rename or move. ``record.relative_path`` is the new object key."""
    active = _queue(links, file_record_id=record.id)
    if active is None:
        return
    await _rekey_linked(active, record)


async def enqueue_rekey_many(
    links: IndexLinkRepository | None,
    records: Sequence[FileRecord],
) -> None:
    """Re-key every descendant after a directory rename or move."""
    active = _queue(links)
    if active is None:
        return
    for record in records:
        await _rekey_linked(active, record)


async def enqueue_removed(
    links: IndexLinkRepository | None,
    file_record_ids: Sequence[UUID],
) -> None:
    """Record deletes. Missing rows are left untouched by the repository."""
    ids = list(file_record_ids)
    if not ids:
        return
    file_record_id = ids[0] if len(ids) == 1 else None
    active = _queue(links, file_record_id=file_record_id)
    if active is None:
        return
    try:
        removed = await active.mark_removed(ids)
    except PrivateIndexRefused:
        logger.warning(
            "Refused private index removal for {} file records",
            len(ids),
        )
        return
    for link in removed:
        _log_queued(link)
    note_immediate_index_ids(
        [link.file_record_id for link in removed if link.file_record_id is not None]
    )


async def enqueue_archived(
    links: IndexLinkRepository | None,
    record: FileRecord,
) -> None:
    """Record a successful archive. Does not register the ``.zst`` key."""
    active = _queue(links, file_record_id=record.id)
    if active is None or _refused_without_row(record):
        return
    try:
        link = await active.mark_archived(record.id)
    except PrivateIndexRefused:
        _warn_private(record)
        return
    _log_queued(link)


def _queue(
    links: IndexLinkRepository | None,
    *,
    file_record_id: UUID | None = None,
) -> IndexLinkRepository | None:
    if links is None:
        return None
    if get_settings().rag.enabled:
        return links
    if file_record_id is None:
        logger.warning("RAG indexing is disabled; index link was not written")
    else:
        logger.warning(
            "RAG indexing is disabled; index link was not written for file_record_id {}",
            file_record_id,
        )
    return None


def _refused_without_row(record: FileRecord) -> bool:
    if record.section is FileSection.PRIVATE or record.is_encrypted:
        _warn_private(record)
        return True
    if record.section not in _INDEXED_SECTIONS:
        _warn_excluded(record)
        return True
    return False


async def _rekey_linked(links: IndexLinkRepository, record: FileRecord) -> None:
    if _refused_without_row(record):
        return
    try:
        link = await links.mark_rekey(record.id, record.relative_path)
    except PrivateIndexRefused:
        _warn_private(record)
        return
    _log_queued(link)


def _warn_private(record: FileRecord) -> None:
    logger.warning(
        "Refused private index link for file_record_id {} user_id {}",
        record.id,
        record.user_id,
    )


def _warn_excluded(record: FileRecord) -> None:
    logger.warning(
        "Index link not queued for excluded section {} file_record_id {}",
        record.section.value,
        record.id,
    )


def _log_queued(link: IndexLink | None) -> None:
    if link is None:
        return
    logger.info(
        "Queued index link user_id={} file_record_id={} rag_file_id={} state={}",
        link.user_id,
        link.file_record_id,
        link.rag_file_id,
        link.state.value,
    )
