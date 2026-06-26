import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from loguru import logger

from app.domain.entities.file_record import FileRecord, FileStatus
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.disk_router import DiskRouter
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
        source_path = self._resolve_disk_path(record.disk_id, record.relative_path)
        if not source_path.is_file():
            logger.warning(
                "Skipping archive for file {}: source missing at {}",
                record.id,
                source_path,
            )
            return False

        archive_relative_path = f"{record.relative_path}{ARCHIVE_EXTENSION}"
        archive_path = self._resolve_disk_path(record.disk_id, archive_relative_path)
        compress_mode = "post_encrypt" if record.is_encrypted else "pre_encrypt"

        await self._archive_manager.compress_async(source_path, archive_path, compress_mode)
        await asyncio.to_thread(source_path.unlink)

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
            archive_path = self._resolve_disk_path(record.disk_id, record.archive_path)
            if archive_path.is_file():
                total += archive_path.stat().st_size

        return total

    def resolve_archive_path(self, record: FileRecord) -> Path:
        if not record.archive_path:
            raise FileNotFoundError(f"Archive path is missing for file {record.id}")
        return self._resolve_disk_path(record.disk_id, record.archive_path)

    def resolve_disk_path(self, disk_id: str, relative_path: str) -> Path:
        return self._resolve_disk_path(disk_id, relative_path)

    def temp_decompress_path(self, record: FileRecord) -> Path:
        disk = self._disk_router.get_disk_by_id(record.disk_id)
        return disk.mount_path / ".archive_tmp" / str(record.id) / "decompressed"

    def _resolve_disk_path(self, disk_id: str, relative_path: str) -> Path:
        disk = self._disk_router.get_disk_by_id(disk_id)
        return disk.mount_path / relative_path
