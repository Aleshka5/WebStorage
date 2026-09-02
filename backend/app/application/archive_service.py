import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiofiles
from loguru import logger

from app.domain.entities.file_record import FileRecord
from app.domain.exceptions import FileNotFoundError as DomainFileNotFoundError
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import READ_CHUNK_SIZE, StorageAdapter
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter
from config import Settings, get_settings

ARCHIVE_EXTENSION = ".zst"


@dataclass(frozen=True)
class ArchiveReport:
    processed: int
    skipped: int
    errors: int


@dataclass(frozen=True)
class ArchiveStats:
    last_run: datetime | None
    processed: int
    skipped: int
    errors: int
    total_archived_bytes: int


class ArchiveService:
    _last_stats: ArchiveStats | None = None

    def __init__(
        self,
        file_repo: FileRepository,
        archive_manager: ArchiveManager,
        disk_router: DiskRouter,
        settings: Settings | None = None,
    ) -> None:
        self._file_repo = file_repo
        self._archive_manager = archive_manager
        self._disk_router = disk_router
        self._settings = settings or get_settings()

    async def run_daily_archive(self) -> ArchiveReport:
        threshold_days = self._settings.business_logic.archive_days_threshold
        cutoff = datetime.now(tz=UTC) - timedelta(days=threshold_days)
        logger.info(
            "Starting daily archive run (threshold={} days, cutoff={})",
            threshold_days,
            cutoff.isoformat(),
        )

        candidates = await self._file_repo.list_candidates_for_archive(cutoff)
        processed = 0
        skipped = 0
        errors = 0

        for record in candidates:
            try:
                archived = await self._archive_record(record)
                if archived:
                    processed += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
                logger.exception("Failed to archive file record {}", record.id)

        report = ArchiveReport(processed=processed, skipped=skipped, errors=errors)
        total_archived_bytes = await self._compute_total_archived_bytes()

        ArchiveService._last_stats = ArchiveStats(
            last_run=datetime.now(tz=UTC),
            processed=report.processed,
            skipped=report.skipped,
            errors=report.errors,
            total_archived_bytes=total_archived_bytes,
        )
        logger.info(
            "Daily archive run completed: processed={}, skipped={}, errors={}",
            processed,
            skipped,
            errors,
        )
        return report

    async def get_stats(self) -> ArchiveStats:
        total_archived_bytes = await self._compute_total_archived_bytes()
        if ArchiveService._last_stats is None:
            return ArchiveStats(
                last_run=None,
                processed=0,
                skipped=0,
                errors=0,
                total_archived_bytes=total_archived_bytes,
            )

        return ArchiveStats(
            last_run=ArchiveService._last_stats.last_run,
            processed=ArchiveService._last_stats.processed,
            skipped=ArchiveService._last_stats.skipped,
            errors=ArchiveService._last_stats.errors,
            total_archived_bytes=total_archived_bytes,
        )

    async def _archive_record(self, record: FileRecord) -> bool:
        adapter = build_disk_root_adapter(record.disk_id)
        if not await adapter.exists(record.relative_path):
            logger.warning(
                "Skipping archive for file {}: source missing at {}",
                record.id,
                record.relative_path,
            )
            return False

        archive_relative_path = f"{record.relative_path}{ARCHIVE_EXTENSION}"
        compress_mode = "post_encrypt" if record.is_encrypted else "pre_encrypt"

        with tempfile.TemporaryDirectory(prefix=f"archive-{record.id}-") as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)
            source_tmp = tmp_dir / "source"
            archive_tmp = tmp_dir / "archive.zst"

            await self._download_to_path(adapter, record.relative_path, source_tmp)
            await self._archive_manager.compress_async(
                source_tmp,
                archive_tmp,
                compress_mode,
            )
            await self._upload_from_path(adapter, archive_tmp, archive_relative_path)

        await adapter.delete(record.relative_path)

        updated = await self._file_repo.mark_archived(record.id, archive_relative_path)
        if updated is None:
            logger.error("File record {} disappeared during archive update", record.id)
            return False

        logger.info(
            "Archived file {} to {} (mode={})",
            record.id,
            archive_relative_path,
            compress_mode,
        )
        return True

    async def _compute_total_archived_bytes(self) -> int:
        records = await self._file_repo.list_archived_records()
        total = 0

        for record in records:
            if not record.archive_path:
                continue
            adapter = build_disk_root_adapter(record.disk_id)
            try:
                total += await adapter.get_size(record.archive_path)
            except DomainFileNotFoundError:
                logger.warning(
                    "Archived blob missing for file {} at {}",
                    record.id,
                    record.archive_path,
                )
            except FileNotFoundError:
                logger.warning(
                    "Archived blob missing for file {} at {}",
                    record.id,
                    record.archive_path,
                )

        return total

    @staticmethod
    async def _download_to_path(
        adapter: StorageAdapter,
        remote_path: str,
        local_path: Path,
    ) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(local_path, mode="wb") as handle:
            async for chunk in adapter.read(remote_path):
                await handle.write(chunk)

    @staticmethod
    async def _upload_from_path(
        adapter: StorageAdapter,
        local_path: Path,
        remote_path: str,
    ) -> None:
        size = local_path.stat().st_size

        async def _stream() -> AsyncIterator[bytes]:
            async with aiofiles.open(local_path, mode="rb") as handle:
                while True:
                    chunk = await handle.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        await adapter.write(remote_path, _stream(), size)
