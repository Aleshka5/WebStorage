"""DI for indexing routes.

The router is not mounted from ``main`` yet. Tests override these providers.
"""

from functools import lru_cache

from fastapi import Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.index_links import IndexLinkRepository
from app.application.ports.rag_index import RagIndex
from app.infrastructure.database.repositories.file_index_link_repo import (
    FileIndexLinkRepository,
)
from app.infrastructure.database.session import get_async_session
from app.infrastructure.rag.client import RagIndexClient
from config import get_settings


def get_index_link_repository(
    session: AsyncSession = Depends(get_async_session),
) -> IndexLinkRepository:
    return FileIndexLinkRepository(session)


@lru_cache
def get_rag_index() -> RagIndex:
    rag = get_settings().rag
    logger.info(
        "Indexing RAG client ready enabled={} base_url={}",
        rag.enabled,
        rag.base_url,
    )
    return RagIndexClient(rag.base_url, timeout_seconds=rag.timeout_seconds)
