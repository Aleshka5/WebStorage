from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from app.domain.exceptions import PathTraversalError

READ_CHUNK_SIZE = 64 * 1024
HIDDEN_DIR_NAMES = frozenset({".tmp"})


@dataclass(frozen=True)
class FileNode:
    name: str
    is_dir: bool
    size: int
    modified_at: datetime
    uploaded_by: str | None = None


class StorageAdapter(ABC):
    @property
    @abstractmethod
    def root_prefix(self) -> str:
        """Logical root key prefix (e.g. users/{id}/files), backend-agnostic."""

    @property
    @abstractmethod
    def disk_id(self) -> str:
        """Identifier of the storage disk (e.g. disk1)."""

    @property
    @abstractmethod
    def disk_relative_prefix(self) -> str:
        """Path from disk root to the adapter base (e.g. users/{id}/files)."""

    @staticmethod
    def _safe_logical_key(user_input: str) -> str:
        """Normalize a logical path and forbid ``..`` / escape segments."""
        normalized_input = user_input.strip().replace("\\", "/").lstrip("/")
        if not normalized_input:
            return ""

        parts: list[str] = []
        for part in normalized_input.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise PathTraversalError(
                    f"Path {user_input!r} escapes allowed directory"
                )
            parts.append(part)
        return "/".join(parts)

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

    @abstractmethod
    async def get_size(self, path: str) -> int:
        """Return size in bytes of a file at ``path``."""

    @abstractmethod
    async def list_stale_tmp_entry_paths(self, cutoff_ts: float) -> list[str]:
        """Return adapter-relative paths of stale entries under any ``.tmp/`` directory."""

    def to_disk_relative_path(self, section_path: str) -> str:
        """Convert a path relative to root_prefix into a disk-root-relative path."""
        normalized = self._safe_logical_key(section_path)
        if not normalized:
            return self.disk_relative_prefix
        return f"{self.disk_relative_prefix}/{normalized}"

    async def encrypt_path(self, path: str) -> str:
        """Encrypt a section path for storage.

        Default: identity (plain storage). Override in EncryptedStorageAdapter.
        """
        return path
