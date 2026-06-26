import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
from loguru import logger

from app.domain.entities.file_record import FileRecord
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE
from app.infrastructure.storage.encrypted_adapter import EncryptedStorageAdapter


async def stream_decompressed_archived(
    record: FileRecord,
    archive_manager: ArchiveManager,
    disk_router: DiskRouter,
    *,
    encrypted_adapter: EncryptedStorageAdapter | None = None,
) -> AsyncIterator[bytes]:
    if not record.archive_path:
        raise FileNotFoundError(f"Archive path is missing for file {record.id}")

    disk = disk_router.get_disk_by_id(record.disk_id)
    archive_path = disk.mount_path / record.archive_path
    temp_dir = disk.mount_path / ".archive_tmp" / str(record.id)
    temp_file = temp_dir / "decompressed"

    logger.info("Decompressing archived file {} from {}", record.id, archive_path)
    try:
        await archive_manager.decompress_async(archive_path, temp_file)

        if record.is_encrypted:
            if encrypted_adapter is None:
                raise ValueError(
                    f"Encrypted archived file {record.id} requires EncryptedStorageAdapter",
                )
            async for chunk in encrypted_adapter.read_encrypted_blob(temp_file):
                yield chunk
        else:
            async with aiofiles.open(temp_file, mode="rb") as file_handle:
                while True:
                    chunk = await file_handle.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
    finally:
        if temp_file.exists():
            await asyncio.to_thread(temp_file.unlink)
        if temp_dir.exists():
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
        logger.info("Cleaned up temporary decompressed file for {}", record.id)


def resolve_archived_file_path(record: FileRecord, disk_router: DiskRouter) -> Path:
    if not record.archive_path:
        raise FileNotFoundError(f"Archive path is missing for file {record.id}")
    disk = disk_router.get_disk_by_id(record.disk_id)
    return disk.mount_path / record.archive_path
