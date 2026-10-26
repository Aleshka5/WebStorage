from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PhotoSearchItemResponse(BaseModel):
    id: UUID
    file_name: str
    preview_url: str
    original_url: str
    score: float


class PhotoSearchResponse(BaseModel):
    items: list[PhotoSearchItemResponse]
    query: str
    total: int = Field(ge=0)


class IndexStatusResponse(BaseModel):
    indexed: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    total: int = Field(ge=0)


class IndexErrorCountResponse(BaseModel):
    code: str
    count: int = Field(ge=0)


class AdminIndexResponse(BaseModel):
    by_state: dict[str, int]
    oldest_pending_seconds: int | None
    last_errors: list[IndexErrorCountResponse]
    rag_reachable: bool


class BackfillRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)


class BackfillCursorResponse(BaseModel):
    created_at: datetime
    record_id: UUID


class BackfillResponse(BaseModel):
    examined: int = Field(ge=0)
    enqueued: int = Field(ge=0)
    skipped: int = Field(ge=0)
    next_cursor: BackfillCursorResponse | None
    remaining: int = Field(ge=0)
    status: str


class RetryRequest(BaseModel):
    state: Literal["FAILED"] | None = None


class RetryResponse(BaseModel):
    requeued: int = Field(ge=0)
