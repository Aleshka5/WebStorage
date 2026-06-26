from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PhotoItemResponse(BaseModel):
    id: UUID
    preview_url: str
    original_url: str
    created_at: datetime
    size: int = Field(ge=0)


class PhotoListResponse(BaseModel):
    items: list[PhotoItemResponse]
    total: int = Field(ge=0)
    has_next: bool
