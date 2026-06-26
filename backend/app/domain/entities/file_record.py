from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class FileSection(StrEnum):
    PHOTOS = "PHOTOS"
    FILES = "FILES"
    PRIVATE = "PRIVATE"
    SHARED = "SHARED"


class FileStatus(StrEnum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class FileRecord:
    id: UUID
    user_id: UUID
    disk_id: str
    relative_path: str
    original_name: str
    size_bytes: int
    mime_type: str
    is_encrypted: bool
    section: FileSection
    status: FileStatus
    checksum_sha256: str | None
    created_at: datetime
    last_accessed_at: datetime
    is_archived: bool
    archive_path: str | None
