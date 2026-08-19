import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
from loguru import logger

from app.domain.entities.file_record import FileRecord
from app.domain.exceptions import FileNotFoundError as DomainFileNotFoundError
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE
from app.infrastructure.storage.encrypted_adapter import EncryptedStorageAdapter
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter


async def stream_decompressed_archived(
    record: FileRecord,
    archive_manager: ArchiveManager,
    *,
    encrypted_adapter: EncryptedStorageAdapter | None = None,
) -> AsyncIterator[bytes]:
    if not record.archive_path:
        raise FileNotFoundError(f"Archive path is missing for file {record.id}")

    adapter = build_disk_root_adapter(record.disk_id)
    logger.info(
        "Decompressing archived file {} from disk_id={} path={}",
        record.id,
        record.disk_id,
        record.archive_path,
    )

    with tempfile.TemporaryDirectory(prefix=f"archive-read-{record.id}-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        archive_file = tmp_dir / "archive.zst"
        decompressed = tmp_dir / "decompressed"

        async with aiofiles.open(archive_file, mode="wb") as handle:
            async for chunk in adapter.read(record.archive_path):
                await handle.write(chunk)

        await archive_manager.decompress_async(archive_file, decompressed)

        if record.is_encrypted:
            if encrypted_adapter is None:
                raise ValueError(
                    f"Encrypted archived file {record.id} requires EncryptedStorageAdapter",
                )
            async for chunk in encrypted_adapter.read_encrypted_blob(decompressed):
                yield chunk
        else:
            async with aiofiles.open(decompressed, mode="rb") as file_handle:
                while True:
                    chunk = await file_handle.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

    logger.info("Cleaned up temporary decompressed file for {}", record.id)


async def delete_archived_blob(record: FileRecord) -> None:
    """Delete the archived object for ``record`` via the disk-root storage adapter."""
    if not record.archive_path:
        logger.warning("No archive_path for file {}, nothing to delete", record.id)
        return

    adapter = build_disk_root_adapter(record.disk_id)
    try:
        if await adapter.exists(record.archive_path):
            await adapter.delete(record.archive_path)
            logger.info(
                "Deleted archived blob for file {} at {}",
                record.id,
                record.archive_path,
            )
        else:
            logger.warning(
                "Archived blob for file {} not found at {}",
                record.id,
                record.archive_path,
            )
    except DomainFileNotFoundError:
        logger.warning(
            "Archived blob for file {} not found at {}",
            record.id,
            record.archive_path,
        )
