"""HTTP tests for photo search, index status, and admin queue routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.indexing.backfill import BackfillResult, BackfillStatus
from app.application.ports.index_links import (
    AdminIndexSnapshot,
    BackfillCursor,
    IndexErrorCount,
    IndexLink,
    UserIndexCounts,
)
from app.application.ports.rag_index import (
    RagImageHit,
    RagImageSearchResult,
    RagIndexUnavailableError,
)
from app.domain.entities.file_record import FileSection
from app.domain.entities.user import User
from app.domain.indexing import SEARCH_QUERY_MAX_LENGTH, IndexLinkState
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.indexing import get_index_link_repository, get_rag_index
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.indexing_router import router
from config import get_settings

USER_A_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ADMIN_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
PHOTO_A1 = UUID("a1111111-1111-1111-1111-111111111111")
PHOTO_A2 = UUID("a2222222-2222-2222-2222-222222222222")
PHOTO_A_PENDING = UUID("a3333333-3333-3333-3333-333333333333")
FILE_A = UUID("a4444444-4444-4444-4444-444444444444")
PHOTO_B = UUID("b1111111-1111-1111-1111-111111111111")
RAG_A1 = UUID("11111111-1111-1111-1111-111111111111")
RAG_A2 = UUID("22222222-2222-2222-2222-222222222222")
RAG_A_PENDING = UUID("33333333-3333-3333-3333-333333333333")
RAG_A_FILE = UUID("44444444-4444-4444-4444-444444444444")
RAG_B = UUID("55555555-5555-5555-5555-555555555555")
RAG_UNKNOWN = UUID("66666666-6666-6666-6666-666666666666")
REQUEST_ID = UUID("77777777-7777-7777-7777-777777777777")
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


class Actor:
    def __init__(self, user: User) -> None:
        self.user = user

    def __call__(self) -> User:
        return self.user


class FakeIndexLinks:
    def __init__(self) -> None:
        self.by_rag: dict[UUID, IndexLink] = {}
        self.photo_calls: list[tuple[UUID, list[UUID]]] = []
        self.count_calls: list[UUID] = []
        self.counts: dict[UUID, UserIndexCounts] = {}
        self.snapshot = AdminIndexSnapshot(
            by_state={},
            oldest_pending_at=None,
            last_errors=(),
        )
        self.retry_calls: list[int] = []
        self.requeued = 0
        self.names: dict[UUID, str] = {}

    async def original_names(self, file_record_ids) -> dict[UUID, str]:
        return {
            file_id: self.names[file_id]
            for file_id in file_record_ids
            if file_id in self.names
        }

    async def indexed_photos_by_rag_file_id(
        self,
        user_id: UUID,
        rag_file_ids,
    ) -> dict[UUID, IndexLink]:
        ids = list(rag_file_ids)
        self.photo_calls.append((user_id, ids))
        return {
            rag_id: self.by_rag[rag_id]
            for rag_id in ids
            if rag_id in self.by_rag and self.by_rag[rag_id].user_id == user_id
        }

    async def counts_for_user(self, user_id: UUID) -> UserIndexCounts:
        self.count_calls.append(user_id)
        return self.counts[user_id]

    async def admin_snapshot(self) -> AdminIndexSnapshot:
        return self.snapshot

    async def retry_failed(self, limit: int, *, now: datetime | None = None) -> int:
        self.retry_calls.append(limit)
        return self.requeued


class FakeRagIndex:
    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.health_calls: list[UUID | None] = []
        self.items: tuple[RagImageHit, ...] = ()
        self.search_error: Exception | None = None
        self.health_result = True
        self.health_error: Exception | None = None

    async def search_images(
        self,
        q: str,
        *,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        request_id: UUID | None = None,
    ) -> RagImageSearchResult:
        self.search_calls.append(
            {
                "q": q,
                "user_id": user_id,
                "limit": limit,
                "offset": offset,
                "request_id": request_id,
            }
        )
        if self.search_error is not None:
            raise self.search_error
        return RagImageSearchResult(
            items=self.items,
            limit=limit,
            offset=offset,
            next_offset=None,
        )

    async def health(self, *, request_id: UUID | None = None) -> bool:
        self.health_calls.append(request_id)
        if self.health_error is not None:
            raise self.health_error
        return self.health_result


def _user(user_id: UUID, role: Role = Role.FAMILY) -> User:
    return User(
        id=user_id,
        email=f"{user_id.hex[:8]}@example.test",
        role=role,
        is_active=True,
        created_at=NOW,
    )


def _link(
    *,
    user_id: UUID,
    file_record_id: UUID,
    rag_file_id: UUID,
    section: FileSection = FileSection.PHOTOS,
    state: IndexLinkState = IndexLinkState.INDEXED,
) -> IndexLink:
    return IndexLink(
        id=uuid4(),
        file_record_id=file_record_id,
        user_id=user_id,
        section=section,
        s3_key=f"users/{user_id}/photos/originals/{file_record_id}.jpg",
        content_sha256="ab" * 32,
        content_type="image/jpeg",
        rag_file_id=rag_file_id,
        state=state,
        attempts=1,
        next_attempt_at=None,
        last_error_code=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _enable_rag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setenv("RAG_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    get_rag_index.cache_clear()
    yield
    get_settings.cache_clear()
    get_rag_index.cache_clear()


def _build_app(actor: Actor, links: FakeIndexLinks, rag: FakeRagIndex) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def _session():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_index_link_repository] = lambda: links
    app.dependency_overrides[get_rag_index] = lambda: rag
    app.dependency_overrides[get_async_session] = _session
    return app


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def _library() -> tuple[FakeIndexLinks, FakeRagIndex]:
    links = FakeIndexLinks()
    links.by_rag[RAG_A1] = _link(user_id=USER_A_ID, file_record_id=PHOTO_A1, rag_file_id=RAG_A1)
    links.by_rag[RAG_A2] = _link(user_id=USER_A_ID, file_record_id=PHOTO_A2, rag_file_id=RAG_A2)
    links.by_rag[RAG_A_PENDING] = _link(
        user_id=USER_A_ID,
        file_record_id=PHOTO_A_PENDING,
        rag_file_id=RAG_A_PENDING,
        state=IndexLinkState.PENDING,
    )
    links.by_rag[RAG_A_FILE] = _link(
        user_id=USER_A_ID,
        file_record_id=FILE_A,
        rag_file_id=RAG_A_FILE,
        section=FileSection.FILES,
    )
    links.by_rag[RAG_B] = _link(user_id=USER_B_ID, file_record_id=PHOTO_B, rag_file_id=RAG_B)
    links.names[PHOTO_A1] = "pier-view.jpg"
    links.names[PHOTO_A2] = "first-light.heic"
    links.names[PHOTO_B] = "north-cove.jpg"
    rag = FakeRagIndex()
    rag.items = (
        RagImageHit(file_id=RAG_UNKNOWN, file_name="stranger.jpg", score=1.0),
        RagImageHit(file_id=RAG_A_FILE, file_name="notes.pdf", score=0.75),
        RagImageHit(file_id=RAG_A_PENDING, file_name="pending.jpg", score=0.9),
        RagImageHit(file_id=RAG_B, file_name="beach.jpg", score=0.125),
        RagImageHit(file_id=RAG_A1, file_name="sunset.jpg", score=0.25),
        RagImageHit(file_id=RAG_A2, file_name="dawn.jpg", score=0.5),
    )
    return links, rag


def test_photo_search_is_isolated_between_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, True)
    links, rag = _library()
    actor = Actor(_user(USER_A_ID))
    client = TestClient(_build_app(actor, links, rag))

    response_a = client.get(
        "/api/photos/search",
        params={"q": "  sunset over the sea  "},
        headers={"X-Request-Id": str(REQUEST_ID)},
    )

    assert response_a.status_code == 200
    body_a = response_a.json()
    assert body_a["query"] == "sunset over the sea"
    assert body_a["total"] == 2
    assert [item["id"] for item in body_a["items"]] == [str(PHOTO_A2), str(PHOTO_A1)]
    assert [item["score"] for item in body_a["items"]] == [0.5, 0.25]
    assert body_a["items"][0]["file_name"] == "first-light.heic"
    assert body_a["items"][1]["file_name"] == "pier-view.jpg"
    assert "dawn.jpg" not in response_a.text
    assert "sunset.jpg" not in response_a.text
    assert body_a["items"][0]["preview_url"] == f"/api/photos/{PHOTO_A2}/preview"
    assert body_a["items"][0]["original_url"] == f"/api/photos/{PHOTO_A2}/original"
    assert "s3_key" not in response_a.text
    assert "content_path" not in response_a.text
    assert f"users/{USER_A_ID}" not in response_a.text
    assert rag.search_calls[0]["user_id"] == USER_A_ID
    assert rag.search_calls[0]["q"] == "sunset over the sea"
    assert rag.search_calls[0]["limit"] == 20
    assert rag.search_calls[0]["offset"] == 0
    assert rag.search_calls[0]["request_id"] == REQUEST_ID
    assert links.photo_calls[0][0] == USER_A_ID

    actor.user = _user(USER_B_ID)
    response_b = client.get("/api/photos/search", params={"q": "sunset over the sea"})

    assert response_b.status_code == 200
    body_b = response_b.json()
    assert body_b["total"] == 1
    assert [item["id"] for item in body_b["items"]] == [str(PHOTO_B)]
    assert body_b["items"][0]["file_name"] == "north-cove.jpg"
    assert "beach.jpg" not in response_b.text
    assert str(PHOTO_A1) not in response_b.text
    assert str(PHOTO_A2) not in response_b.text
    assert rag.search_calls[1]["user_id"] == USER_B_ID
    assert links.photo_calls[1][0] == USER_B_ID


@pytest.mark.parametrize("query", ["", "   ", "a" * (SEARCH_QUERY_MAX_LENGTH + 1)])
def test_photo_search_rejects_invalid_query(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    _enable_rag(monkeypatch, True)
    links, rag = _library()
    client = TestClient(_build_app(Actor(_user(USER_A_ID)), links, rag))

    response = client.get("/api/photos/search", params={"q": query})

    assert response.status_code == 400
    assert _error_code(response) == ErrorCode.SEARCH_QUERY_INVALID
    assert response.json()["detail"]["message"]
    assert rag.search_calls == []
    assert links.photo_calls == []


def test_photo_search_when_rag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, False)
    links, rag = _library()
    client = TestClient(_build_app(Actor(_user(USER_A_ID)), links, rag))

    response = client.get("/api/photos/search", params={"q": "sunset"})

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.INDEXING_UNAVAILABLE
    assert "Traceback" not in response.text
    assert rag.search_calls == []


def test_photo_search_when_rag_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, True)
    links, rag = _library()
    rag.search_error = RagIndexUnavailableError(
        "RAG timed out",
        status_code=504,
        error_code="timeout",
    )
    client = TestClient(_build_app(Actor(_user(USER_A_ID)), links, rag))

    response = client.get("/api/photos/search", params={"q": "sunset", "limit": 5})

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.INDEXING_UNAVAILABLE
    assert "Traceback" not in response.text
    assert len(rag.search_calls) == 1
    assert rag.search_calls[0]["limit"] == 5
    assert links.photo_calls == []


def test_indexing_status_is_caller_scoped() -> None:
    links = FakeIndexLinks()
    links.counts[USER_A_ID] = UserIndexCounts(indexed=2, pending=1, failed=0, skipped=3, total=10)
    links.counts[USER_B_ID] = UserIndexCounts(indexed=1, pending=0, failed=4, skipped=0, total=5)
    actor = Actor(_user(USER_A_ID))
    client = TestClient(_build_app(actor, links, FakeRagIndex()))

    response_a = client.get("/api/indexing/status")
    actor.user = _user(USER_B_ID)
    response_b = client.get("/api/indexing/status")

    assert response_a.status_code == 200
    assert response_a.json() == {
        "indexed": 2,
        "pending": 1,
        "failed": 0,
        "skipped": 3,
        "total": 10,
    }
    assert response_b.json() == {
        "indexed": 1,
        "pending": 0,
        "failed": 4,
        "skipped": 0,
        "total": 5,
    }
    assert links.count_calls == [USER_A_ID, USER_B_ID]


def test_admin_indexing_snapshot_and_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    links = FakeIndexLinks()
    links.snapshot = AdminIndexSnapshot(
        by_state={"INDEXED": 4, "ARCHIVED": 2, "REMOVED": 3, "FAILED": 1},
        oldest_pending_at=datetime.now(UTC) - timedelta(seconds=120),
        last_errors=(IndexErrorCount(code="timeout", count=2),),
    )
    rag = FakeRagIndex()
    client = TestClient(_build_app(Actor(_user(ADMIN_ID, Role.ADMIN)), links, rag))
    _enable_rag(monkeypatch, False)

    disabled = client.get("/api/admin/indexing", headers={"X-Request-Id": str(REQUEST_ID)})

    assert disabled.status_code == 200
    body = disabled.json()
    assert body["by_state"] == {"INDEXED": 4, "ARCHIVED": 2, "REMOVED": 3, "FAILED": 1}
    assert body["last_errors"] == [{"code": "timeout", "count": 2}]
    assert body["rag_reachable"] is False
    assert body["oldest_pending_seconds"] >= 120
    assert rag.health_calls == []

    _enable_rag(monkeypatch, True)
    rag.health_result = True
    reachable = client.get("/api/admin/indexing", headers={"X-Request-Id": str(REQUEST_ID)})

    assert reachable.status_code == 200
    assert reachable.json()["rag_reachable"] is True
    assert rag.health_calls == [REQUEST_ID]

    rag.health_error = RagIndexUnavailableError(
        "RAG is down",
        status_code=503,
        error_code="dependency_unavailable",
    )
    outage = client.get("/api/admin/indexing")

    assert outage.status_code == 200
    assert outage.json()["rag_reachable"] is False
    assert "Traceback" not in outage.text
    assert len(rag.health_calls) == 2


def test_admin_backfill_calls_existing_function_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, True)
    calls: list[dict] = []
    calls_this_request = {"count": 0}
    cursor = BackfillCursor(created_at=NOW, record_id=PHOTO_A1)

    async def spy(
        links,
        *,
        limit=None,
        after=None,
        backfill_enabled=None,
        index_batch_size=None,
        max_image_bytes=None,
    ):
        calls_this_request["count"] += 1
        if calls_this_request["count"] > 1:
            raise RuntimeError("backfill was called more than once for one request")
        calls.append(
            {
                "links": links,
                "limit": limit,
                "after": after,
                "backfill_enabled": backfill_enabled,
                "index_batch_size": index_batch_size,
                "max_image_bytes": max_image_bytes,
            }
        )
        return BackfillResult(
            examined=3,
            enqueued=2,
            skipped=1,
            next_cursor=cursor,
            remaining=9,
            status=BackfillStatus.COMPLETED,
        )

    monkeypatch.setattr("app.presentation.routers.indexing_router.backfill", spy)
    links = FakeIndexLinks()
    client = TestClient(_build_app(Actor(_user(ADMIN_ID, Role.ADMIN)), links, FakeRagIndex()))

    calls_this_request["count"] = 0
    limited = client.post("/api/admin/indexing/backfill", json={"limit": 5})

    assert limited.status_code == 200
    assert len(calls) == 1
    assert limited.json()["examined"] == 3
    assert limited.json()["enqueued"] == 2
    assert limited.json()["skipped"] == 1
    assert limited.json()["remaining"] == 9
    assert limited.json()["status"] == "completed"
    assert limited.json()["next_cursor"]["record_id"] == str(PHOTO_A1)
    assert calls[0]["links"] is links
    assert calls[0]["limit"] == 5
    assert calls[0]["after"] is None
    assert calls[0]["backfill_enabled"] is True
    assert calls[0]["index_batch_size"] is None
    assert calls[0]["max_image_bytes"] is None

    calls_this_request["count"] = 0
    omitted = client.post("/api/admin/indexing/backfill")

    assert omitted.status_code == 200
    assert len(calls) == 2
    assert calls[1]["limit"] is None
    assert calls[1]["backfill_enabled"] is True
    assert omitted.json()["remaining"] == 9


def test_admin_backfill_skips_when_rag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, False)

    async def spy(*args, **kwargs):
        del args, kwargs
        raise AssertionError("backfill must not run when RAG is disabled")

    monkeypatch.setattr("app.presentation.routers.indexing_router.backfill", spy)
    client = TestClient(_build_app(Actor(_user(ADMIN_ID, Role.ADMIN)), FakeIndexLinks(), FakeRagIndex()))

    response = client.post("/api/admin/indexing/backfill", json={"limit": 5})

    assert response.status_code == 200
    assert response.json() == {
        "examined": 0,
        "enqueued": 0,
        "skipped": 0,
        "next_cursor": None,
        "remaining": 0,
        "status": "skipped",
    }


def test_admin_retry_requeues_one_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_rag(monkeypatch, True)
    links = FakeIndexLinks()
    links.requeued = 4
    client = TestClient(_build_app(Actor(_user(ADMIN_ID, Role.ADMIN)), links, FakeRagIndex()))
    batch_size = get_settings().rag.index_batch_size

    explicit = client.post("/api/admin/indexing/retry", json={"state": "FAILED"})
    omitted = client.post("/api/admin/indexing/retry", json={})
    rejected = client.post("/api/admin/indexing/retry", json={"state": "PENDING"})

    assert explicit.status_code == 200
    assert explicit.json() == {"requeued": 4}
    assert omitted.status_code == 200
    assert omitted.json() == {"requeued": 4}
    assert links.retry_calls == [batch_size, batch_size]
    assert rejected.status_code == 422
    assert links.retry_calls == [batch_size, batch_size]


def test_admin_indexing_routes_require_admin() -> None:
    links = FakeIndexLinks()
    links.counts[USER_A_ID] = UserIndexCounts(indexed=0, pending=0, failed=0, skipped=0, total=0)
    client = TestClient(_build_app(Actor(_user(USER_A_ID, Role.FAMILY)), links, FakeRagIndex()))

    status = client.get("/api/indexing/status")
    admin_get = client.get("/api/admin/indexing")
    backfill = client.post("/api/admin/indexing/backfill", json={})
    retry = client.post("/api/admin/indexing/retry", json={})

    assert status.status_code == 200
    assert admin_get.status_code == 403
    assert backfill.status_code == 403
    assert retry.status_code == 403
    assert _error_code(admin_get) == ErrorCode.ACCESS_DENIED
    assert links.retry_calls == []
