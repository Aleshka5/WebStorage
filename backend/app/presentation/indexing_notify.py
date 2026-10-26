"""Open a short session and notify RAG after the request commit.

The request transaction is already committed. This session only stores the
``SUBMITTED`` or confirmed ``REMOVED`` result. It does not claim rows, so
it does not hold ``FOR UPDATE`` across the RAG call.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from loguru import logger

from app.application.indexing.immediate import notify_index_links
from app.infrastructure.database.repositories.file_index_link_repo import (
    FileIndexLinkRepository,
)
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.disk_router import STORAGE_VOLUME_ID
from app.infrastructure.rag.client import RagIndexClient
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter
from config import get_settings


async def deliver_immediate_index_notifications(file_record_ids: Sequence[UUID]) -> None:
    """Notify RAG for ids captured before commit. Never raises.

    An outage, a slow RAG, or a retryable error leaves the committed queue
    row for ``index_sync``. Upload and delete keep their normal status codes.
    """
    ordered_ids = list(dict.fromkeys(file_record_ids))
    if not ordered_ids or not get_settings().rag.enabled:
        return

    rag_settings = get_settings().rag
    rag: RagIndexClient | None = None
    try:
        rag = RagIndexClient(
            base_url=rag_settings.base_url,
            timeout_seconds=rag_settings.timeout_seconds,
        )
        async with async_session_factory() as session:
            try:
                await notify_index_links(
                    FileIndexLinkRepository(session),
                    rag,
                    build_disk_root_adapter(STORAGE_VOLUME_ID),
                    ordered_ids,
                )
                await session.commit()
                logger.info(
                    "Immediate index notify committed file_record_count={}",
                    len(ordered_ids),
                )
            except Exception as exc:
                await session.rollback()
                logger.warning(
                    "Immediate index notify failed; scheduler will retry "
                    "file_record_count={} error_type={}",
                    len(ordered_ids),
                    type(exc).__name__,
                )
    except Exception as exc:
        logger.warning(
            "Immediate index notify failed; scheduler will retry "
            "file_record_count={} error_type={}",
            len(ordered_ids),
            type(exc).__name__,
        )
    finally:
        if rag is not None:
            try:
                await rag.aclose()
            except Exception as exc:
                logger.warning(
                    "Immediate index notify client close failed; scheduler will retry "
                    "file_record_count={} error_type={}",
                    len(ordered_ids),
                    type(exc).__name__,
                )
