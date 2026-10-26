"""Indexing HTTP routes.

Include ``router`` with no extra prefix. Each route already carries its full
path (``/api/photos/search``, ``/api/indexing/status``, ``/api/admin/indexing``).
``main`` mounts this router later; this module does not import ``main``.
"""

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.indexing.backfill import BackfillResult, BackfillStatus, backfill
from app.application.indexing.search import (
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
    SEARCH_LIMIT_MIN,
    PhotoSearchPage,
    search_indexed_photos,
)
from app.application.indexing.status import (
    AdminIndexStatus,
    admin_index_status,
    retry_failed_index,
    user_index_status,
)
from app.application.ports.index_links import IndexLinkRepository, UserIndexCounts
from app.application.ports.rag_index import RagIndex
from app.domain.entities.user import User
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.indexing import get_index_link_repository, get_rag_index
from app.presentation.middleware.check_role import check_role
from app.presentation.schemas.indexing import (
    AdminIndexResponse,
    BackfillCursorResponse,
    BackfillRequest,
    BackfillResponse,
    IndexErrorCountResponse,
    IndexStatusResponse,
    PhotoSearchItemResponse,
    PhotoSearchResponse,
    RetryRequest,
    RetryResponse,
)
from config import get_settings

router = APIRouter(prefix="", tags=["indexing"])

_REQUEST_ID_HEADER = "X-Request-Id"


@asynccontextmanager
async def _unit_of_work(session: AsyncSession):
    try:
        yield
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def _request_id(request: Request) -> UUID | None:
    raw = request.headers.get(_REQUEST_ID_HEADER)
    if raw is None or raw.strip() == "":
        return None
    try:
        return UUID(raw.strip())
    except ValueError:
        logger.warning("Ignoring malformed X-Request-Id")
        return None


def _search_response(page: PhotoSearchPage) -> PhotoSearchResponse:
    return PhotoSearchResponse(
        items=[
            PhotoSearchItemResponse(
                id=item.id,
                file_name=item.file_name,
                preview_url=item.preview_url,
                original_url=item.original_url,
                score=item.score,
            )
            for item in page.items
        ],
        query=page.query,
        total=page.total,
    )


def _status_response(counts: UserIndexCounts) -> IndexStatusResponse:
    return IndexStatusResponse(
        indexed=counts.indexed,
        pending=counts.pending,
        failed=counts.failed,
        skipped=counts.skipped,
        total=counts.total,
    )


def _admin_response(status: AdminIndexStatus) -> AdminIndexResponse:
    return AdminIndexResponse(
        by_state=status.by_state,
        oldest_pending_seconds=status.oldest_pending_seconds,
        last_errors=[
            IndexErrorCountResponse(code=item.code, count=item.count) for item in status.last_errors
        ],
        rag_reachable=status.rag_reachable,
    )


def _backfill_response(result: BackfillResult) -> BackfillResponse:
    cursor = result.next_cursor
    next_cursor = None
    if cursor is not None:
        next_cursor = BackfillCursorResponse(
            created_at=cursor.created_at,
            record_id=cursor.record_id,
        )
    return BackfillResponse(
        examined=result.examined,
        enqueued=result.enqueued,
        skipped=result.skipped,
        next_cursor=next_cursor,
        remaining=result.remaining,
        status=result.status.value,
    )


@router.get("/api/photos/search", response_model=PhotoSearchResponse)
async def search_photos(
    request: Request,
    q: str = Query(),
    limit: int = Query(default=SEARCH_LIMIT_DEFAULT, ge=SEARCH_LIMIT_MIN, le=SEARCH_LIMIT_MAX),
    current_user: User = Depends(get_current_user),
    links: IndexLinkRepository = Depends(get_index_link_repository),
    rag: RagIndex = Depends(get_rag_index),
    session: AsyncSession = Depends(get_async_session),
) -> PhotoSearchResponse:
    async with _unit_of_work(session):
        page = await search_indexed_photos(
            links,
            rag,
            user_id=current_user.id,
            query=q,
            limit=limit,
            request_id=_request_id(request),
        )
    return _search_response(page)


@router.get("/api/indexing/status", response_model=IndexStatusResponse)
async def indexing_status(
    current_user: User = Depends(get_current_user),
    links: IndexLinkRepository = Depends(get_index_link_repository),
    session: AsyncSession = Depends(get_async_session),
) -> IndexStatusResponse:
    async with _unit_of_work(session):
        counts = await user_index_status(links, current_user.id)
    return _status_response(counts)


@router.get("/api/admin/indexing", response_model=AdminIndexResponse)
async def admin_indexing(
    request: Request,
    current_user: User = Depends(check_role(Role.ADMIN)),
    links: IndexLinkRepository = Depends(get_index_link_repository),
    rag: RagIndex = Depends(get_rag_index),
    session: AsyncSession = Depends(get_async_session),
) -> AdminIndexResponse:
    async with _unit_of_work(session):
        status = await admin_index_status(
            links,
            rag,
            user_id=current_user.id,
            request_id=_request_id(request),
        )
    return _admin_response(status)


@router.post("/api/admin/indexing/backfill", response_model=BackfillResponse)
async def admin_indexing_backfill(
    current_user: User = Depends(check_role(Role.ADMIN)),
    body: BackfillRequest | None = None,
    links: IndexLinkRepository = Depends(get_index_link_repository),
    session: AsyncSession = Depends(get_async_session),
) -> BackfillResponse:
    limit = None if body is None else body.limit
    logger.info("Admin index backfill started user_id={} limit={}", current_user.id, limit)
    if not get_settings().rag.enabled:
        logger.warning(
            "Admin index backfill skipped because RAG is disabled user_id={}",
            current_user.id,
        )
        result = BackfillResult(
            examined=0,
            enqueued=0,
            skipped=0,
            next_cursor=None,
            remaining=0,
            status=BackfillStatus.SKIPPED,
        )
    else:
        async with _unit_of_work(session):
            result = await backfill(links, limit=limit, backfill_enabled=True)
    logger.info(
        "Admin index backfill finished user_id={} examined={} enqueued={} "
        "skipped={} remaining={} status={}",
        current_user.id,
        result.examined,
        result.enqueued,
        result.skipped,
        result.remaining,
        result.status.value,
    )
    return _backfill_response(result)


@router.post("/api/admin/indexing/retry", response_model=RetryResponse)
async def admin_indexing_retry(
    current_user: User = Depends(check_role(Role.ADMIN)),
    body: RetryRequest | None = None,
    links: IndexLinkRepository = Depends(get_index_link_repository),
    session: AsyncSession = Depends(get_async_session),
) -> RetryResponse:
    state = None if body is None else body.state
    async with _unit_of_work(session):
        requeued = await retry_failed_index(links, user_id=current_user.id, state=state)
    return RetryResponse(requeued=requeued)
