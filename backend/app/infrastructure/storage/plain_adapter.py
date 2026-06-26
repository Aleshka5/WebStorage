import asyncio
import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
from loguru import logger

from app.domain.exceptions import FileNotFoundError
from app.infrastructure.storage.base_adapter import (
    HIDDEN_DIR_NAMES,
    READ_CHUNK_SIZE,
    FileNode,
    StorageAdapter,
)


class PlainStorageAdapter(StorageAdapter):
    def __init__(self, base_path: Path, disk_id: str | None = None) -> None:
        self._base_path = base_path.resolve()
        self._disk_id = disk_id or self._resolve_disk_id()
        self._disk_root = self._resolve_disk_root()
        self._disk_relative_prefix = self._base_path.relative_to(self._disk_root).as_posix()
        logger.info(
            "PlainStorageAdapter initialized: disk_id={}, base_path={}",
            self._disk_id,
            self._base_path,
        )

    @property
    def base_path(self) -> Path:
        return self._base_path

    @property
    def disk_id(self) -> str:
        return self._disk_id

    @property
    def disk_relative_prefix(self) -> str:
        return self._disk_relative_prefix

    def _resolve_disk_id(self) -> str:
        from config import get_settings

        storage_root = Path(get_settings().storage.root).resolve()
        relative = self._base_path.relative_to(storage_root)
        if not relative.parts:
            raise ValueError(f"Base path {self._base_path} is not under storage root {storage_root}")
        return relative.parts[0]

    def _resolve_disk_root(self) -> Path:
        for parent in (self._base_path, *self._base_path.parents):
            if parent.name == self._disk_id:
                return parent
        from config import get_settings

        storage_root = Path(get_settings().storage.root).resolve()
        return storage_root / self._disk_id

    async def list(self, path: str) -> list[FileNode]:
        target = self._safe_path(self._base_path, path)
        if not target.exists():
            raise FileNotFoundError(f"Directory {path!r} not found")
        if not target.is_dir():
            raise FileNotFoundError(f"Path {path!r} is not a directory")

        entries = await asyncio.to_thread(os.scandir, target)
        nodes: list[FileNode] = []
        for entry in entries:
            if entry.name in HIDDEN_DIR_NAMES or entry.name.startswith("."):
                continue
            stat = entry.stat()
            nodes.append(
                FileNode(
                    name=entry.name,
                    is_dir=entry.is_dir(),
                    size=0 if entry.is_dir() else stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                )
            )
        nodes.sort(key=lambda node: (not node.is_dir, node.name.lower()))
        logger.info("Listed {} entries in {}", len(nodes), path)
        return nodes

    async def read(self, path: str) -> AsyncIterator[bytes]:
        target = self._safe_path(self._base_path, path)
        if not target.is_file():
            raise FileNotFoundError(f"File {path!r} not found")

        async with aiofiles.open(target, mode="rb") as file_handle:
            while True:
                chunk = await file_handle.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    async def write(
        self,
        path: str,
        data: AsyncIterator[bytes],
        size: int,
    ) -> str:
        target = self._safe_path(self._base_path, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        bytes_written = 0

        async with aiofiles.open(target, mode="wb") as file_handle:
            async for chunk in data:
                hasher.update(chunk)
                bytes_written += len(chunk)
                await file_handle.write(chunk)

        if bytes_written != size:
            logger.warning(
                "Written byte count {} differs from declared size {} for path {}",
                bytes_written,
                size,
                path,
            )

        checksum = hasher.hexdigest()
        logger.info("Wrote {} bytes to {} (checksum={})", bytes_written, path, checksum)
        return checksum

    async def delete(self, path: str) -> None:
        target = self._safe_path(self._base_path, path)
        if not target.exists():
            raise FileNotFoundError(f"Path {path!r} not found")

        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        else:
            await asyncio.to_thread(target.unlink)

        logger.info("Deleted path {}", path)

    async def mkdir(self, path: str) -> None:
        target = self._safe_path(self._base_path, path)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        logger.info("Created directory {}", path)

    async def rename(self, old_path: str, new_path: str) -> None:
        source = self._safe_path(self._base_path, old_path)
        destination = self._safe_path(self._base_path, new_path)

        if not source.exists():
            raise FileNotFoundError(f"Path {old_path!r} not found")

        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, source, destination)
        logger.info("Renamed {} to {}", old_path, new_path)

    async def exists(self, path: str) -> bool:
        target = self._safe_path(self._base_path, path)
        return target.exists()
