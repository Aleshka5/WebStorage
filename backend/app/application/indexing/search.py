"""Photo search over the caller's indexed Photos links.

The browser calls this service. This service calls RAG ``search_images`` and
keeps only indexed Photos that belong to the caller. Query text is not logged.
"""

from dataclasses import dataclass
from uuid import UUID

from loguru import logger

from app.application.ports.index_links import IndexLink, IndexLinkRepository
from app.application.ports.rag_index import RagImageHit, RagIndex, RagIndexUnavailableError
from app.domain.entities.file_record import FileSection
from app.domain.indexing import IndexLinkState, IndexingUnavailable, validate_search_query
from config import get_settings

SEARCH_LIMIT_MIN = 1
SEARCH_LIMIT_MAX = 50
SEARCH_LIMIT_DEFAULT = 20
SEARCH_OFFSET = 0

_PREVIEW_URL = "/api/photos/{photo_id}/preview"
_ORIGINAL_URL = "/api/photos/{photo_id}/original"
_UNAVAILABLE_MESSAGE = "Search is unavailable right now"


@dataclass(frozen=True)
class PhotoSearchHit:
    """One photo the caller is allowed to see."""

    id: UUID
    file_name: str
    preview_url: str
    original_url: str
    score: float


@dataclass(frozen=True)
class PhotoSearchPage:
    """Filtered image-search page. ``total`` counts items after the filter."""

    items: tuple[PhotoSearchHit, ...]
    query: str
    total: int


async def search_indexed_photos(
    links: IndexLinkRepository,
    rag: RagIndex,
    *,
    user_id: UUID,
    query: str,
    limit: int,
    request_id: UUID | None = None,
) -> PhotoSearchPage:
    """Search the caller's indexed photos.

    ``SearchQueryInvalid`` propagates when ``query`` is empty or too long.
    RAG disabled, and ``RagIndexUnavailableError``, become
    ``IndexingUnavailable`` with no traceback.
    """
    normalized = validate_search_query(query)
    page_limit = _page_limit(limit)
    if not get_settings().rag.enabled:
        logger.warning("Photo search skipped because RAG is disabled user_id={}", user_id)
        raise IndexingUnavailable(_UNAVAILABLE_MESSAGE)

    logger.info("Photo search started user_id={} limit={}", user_id, page_limit)
    try:
        result = await rag.search_images(
            normalized,
            user_id=user_id,
            limit=page_limit,
            offset=SEARCH_OFFSET,
            request_id=request_id,
        )
    except RagIndexUnavailableError as exc:
        logger.warning(
            "Photo search unavailable user_id={} status_code={} error_code={}",
            user_id,
            exc.status_code,
            exc.error_code,
        )
        raise IndexingUnavailable(_UNAVAILABLE_MESSAGE) from exc

    indexed = await links.indexed_photos_by_rag_file_id(
        user_id,
        [hit.file_id for hit in result.items],
    )
    names = await links.original_names(
        [
            link.file_record_id
            for link in indexed.values()
            if link.file_record_id is not None
        ]
    )
    items = _visible_hits(result.items, indexed, names, user_id)
    if not items:
        logger.warning(
            "Photo search returned no indexed photos user_id={} rag_hits={}",
            user_id,
            len(result.items),
        )
    else:
        logger.info(
            "Photo search finished user_id={} rag_hits={} returned={}",
            user_id,
            len(result.items),
            len(items),
        )
    return PhotoSearchPage(items=tuple(items), query=normalized, total=len(items))


def _page_limit(limit: int) -> int:
    if limit < SEARCH_LIMIT_MIN or limit > SEARCH_LIMIT_MAX:
        raise ValueError("Search limit must be between 1 and 50")
    return limit


def _visible_hits(
    hits: tuple[RagImageHit, ...],
    indexed: dict[UUID, IndexLink],
    names: dict[UUID, str],
    user_id: UUID,
) -> list[PhotoSearchHit]:
    visible: list[PhotoSearchHit] = []
    for hit in hits:
        item = _visible_hit(hit, indexed.get(hit.file_id), names, user_id)
        if item is not None:
            visible.append(item)
    visible.sort(key=lambda item: item.score, reverse=True)
    return visible


def _visible_hit(
    hit: RagImageHit,
    link: IndexLink | None,
    names: dict[UUID, str],
    user_id: UUID,
) -> PhotoSearchHit | None:
    if link is None or not _is_caller_indexed_photo(link, user_id, hit.file_id):
        return None
    photo_id = link.file_record_id
    if photo_id is None:
        return None
    file_name = names.get(photo_id)
    if not file_name:
        logger.warning(
            "Photo search skipped a hit without a display name user_id={} file_record_id={}",
            user_id,
            photo_id,
        )
        return None
    return PhotoSearchHit(
        id=photo_id,
        file_name=file_name,
        preview_url=_PREVIEW_URL.format(photo_id=photo_id),
        original_url=_ORIGINAL_URL.format(photo_id=photo_id),
        score=hit.score,
    )


def _is_caller_indexed_photo(link: IndexLink, user_id: UUID, rag_file_id: UUID) -> bool:
    return (
        link.user_id == user_id
        and link.rag_file_id == rag_file_id
        and link.section is FileSection.PHOTOS
        and link.state is IndexLinkState.INDEXED
    )
