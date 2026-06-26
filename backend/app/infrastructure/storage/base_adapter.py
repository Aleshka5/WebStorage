from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.domain.exceptions import PathTraversalError

READ_CHUNK_SIZE = 64 * 1024
HIDDEN_DIR_NAMES = frozenset({".tmp"})


@dataclass(frozen=True)
class FileNode:
    name: str
    is_dir: bool
    size: int
    modified_at: datetime


class StorageAdapter(ABC):
    @property
    @abstractmethod
    def base_path(self) -> Path:
        """Root directory this adapter operates within."""

    @property
    @abstractmethod
    def disk_id(self) -> str:
        """Identifier of the storage disk (e.g. disk1)."""

    @property
    @abstractmethod
    def disk_relative_prefix(self) -> str:
        """Path from disk root to the adapter base (e.g. users/{id}/files)."""

    @staticmethod
    def _safe_path(base: Path, user_input: str) -> Path:
        normalized_input = user_input.strip().replace("\\", "/").lstrip("/")
        base_resolved = base.resolve()
        candidate = (base_resolved / normalized_input).resolve()

        try:
            candidate.relative_to(base_resolved)
        except ValueError as exc:
            raise PathTraversalError(
                f"Path {user_input!r} escapes allowed directory {base_resolved}"
            ) from exc

        return candidate

    @abstractmethod
    async def list(self, path: str) -> list[FileNode]:
        pass

    @abstractmethod
    async def read(self, path: str) -> AsyncIterator[bytes]:
        pass

    @abstractmethod
    async def write(
        self,
        path: str,
        data: AsyncIterator[bytes],
        size: int,
    ) -> str:
        """Write stream to path; return SHA-256 checksum hex digest."""
        pass

    @abstractmethod
    async def delete(self, path: str) -> None:
        pass

    @abstractmethod
    async def mkdir(self, path: str) -> None:
        pass

    @abstractmethod
    async def rename(self, old_path: str, new_path: str) -> None:
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        pass

    def to_disk_relative_path(self, section_path: str) -> str:
        """Convert a path relative to base_path into a disk-root-relative path."""
        normalized = section_path.strip().replace("\\", "/").lstrip("/")
        if not normalized:
            return self.disk_relative_prefix
        return f"{self.disk_relative_prefix}/{normalized}"
