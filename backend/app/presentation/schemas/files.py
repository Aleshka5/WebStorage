from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.entities.file_record import FileSection, FileStatus
from app.infrastructure.storage.base_adapter import FileNode


class FileNodeResponse(BaseModel):
    name: str
    is_dir: bool
    size: int = Field(ge=0)
    modified_at: datetime
    path: str
    uploaded_by: str | None = None

    @classmethod
    def from_node(cls, node: FileNode, path: str) -> "FileNodeResponse":
        return cls(
            name=node.name,
            is_dir=node.is_dir,
            size=node.size,
            modified_at=node.modified_at,
            path=path,
            uploaded_by=node.uploaded_by,
        )


class FileRecordResponse(BaseModel):
    id: UUID
    name: str
    size: int = Field(ge=0)
    section: FileSection
    status: FileStatus
    created_at: datetime


class MkdirRequest(BaseModel):
    path: str
    name: str = Field(min_length=1)


class RenameRequest(BaseModel):
    path: str
    new_name: str = Field(min_length=1)
