import asyncio
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from loguru import logger

from app.domain.entities.file_record import FileRecord, FileSection
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter

PENDING_STALE_HOURS = 1
TMP_STALE_HOURS = 1
QUOTA_MISMATCH_THRESHOLD_BYTES = 1_000_000
TMP_DIR_NAME = ".tmp"


@dataclass(frozen=True)
class ReconcileReport:
    checked: int
    fixed: int


@dataclass(frozen=True)
class MaintenanceStats:
    last_pending_cleanup: datetime | None
    pending_deleted: int
    last_tmp_cleanup: datetime | None
    tmp_deleted: int
    last_quota_reconcile: datetime | None
    quota_checked: int
    quota_fixed: int


class MaintenanceService:
    _last_stats: MaintenanceStats | None = None

    def __init__(
        self,
        file_repo: FileRepository,
        quota_repo: QuotaRepository,
        disk_router: DiskRouter,
    ) -> None:
        self._file_repo = file_repo
        self._quota_repo = quota_repo
        self._disk_router = disk_router

    async def cleanup_pending_records(self) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(hours=PENDING_STALE_HOURS)
        logger.info(
            "Starting stale PENDING records cleanup (cutoff={})",
            cutoff.isoformat(),
        )

        stale_records = await self._file_repo.list_stale_pending(cutoff)
        deleted = 0

        for record in stale_records:
            try:
                await self._delete_tmp_file(record)
                if await self._file_repo.hard_delete(record.id):
                    deleted += 1
            except Exception:
                logger.exception(
                    "Failed to cleanup stale PENDING record {}",
                    record.id,
                )

        self._update_stats(pending_deleted=deleted)
        logger.info("Stale PENDING cleanup completed: deleted={}", deleted)
        return deleted

    async def cleanup_tmp_dirs(self) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(hours=TMP_STALE_HOURS)
        cutoff_ts = cutoff.timestamp()
        logger.info(
            "Starting .tmp directory cleanup (cutoff={})",
            cutoff.isoformat(),
        )

        deleted = 0
        for disk in self._disk_router.get_all_disks():
            deleted += await asyncio.to_thread(
                self._cleanup_disk_tmp_dirs,
                disk.mount_path,
                cutoff_ts,
            )

        self._update_stats(tmp_deleted=deleted)
        logger.info(".tmp cleanup completed: deleted={}", deleted)
        return deleted

    async def reconcile_quotas(self) -> ReconcileReport:
        logger.info("Starting quota reconciliation")

        user_ids = set(await self._file_repo.list_distinct_user_ids_with_committed())
        user_ids.update(await self._quota_repo.list_all_user_ids())

        checked = 0
        fixed = 0

        for user_id in user_ids:
            checked += 1
            real_total = await self._file_repo.sum_committed_bytes_by_user(user_id)
            usage = await self._quota_repo.get_by_user_id(user_id)
            cached_total = usage.total_bytes

            if abs(real_total - cached_total) <= QUOTA_MISMATCH_THRESHOLD_BYTES:
                continue

            logger.warning(
                "Quota mismatch for user {}: cached={} vs real={}",
                user_id,
                cached_total,
                real_total,
            )
            await self._quota_repo.update_total_bytes(user_id, real_total)
            fixed += 1

        report = ReconcileReport(checked=checked, fixed=fixed)
        self._update_stats(quota_checked=checked, quota_fixed=fixed)
        logger.info(
            "Quota reconciliation completed: checked={}, fixed={}",
            checked,
            fixed,
        )
        return report

    async def get_stats(self) -> MaintenanceStats:
        if MaintenanceService._last_stats is None:
            return MaintenanceStats(
                last_pending_cleanup=None,
                pending_deleted=0,
                last_tmp_cleanup=None,
                tmp_deleted=0,
                last_quota_reconcile=None,
                quota_checked=0,
                quota_fixed=0,
            )
        return MaintenanceService._last_stats

    async def _delete_tmp_file(self, record: FileRecord) -> None:
        tmp_path = self._resolve_tmp_path(record)
        if not tmp_path.is_file():
            logger.info(
                "No tmp file to delete for stale PENDING record {} at {}",
                record.id,
                tmp_path,
            )
            return

        await asyncio.to_thread(tmp_path.unlink)
        logger.info(
            "Deleted tmp file for stale PENDING record {} at {}",
            record.id,
            tmp_path,
        )

    def _resolve_tmp_path(self, record: FileRecord) -> Path:
        disk = self._disk_router.get_disk_by_id(record.disk_id)
        section_prefix = self._section_disk_prefix(record)
        return disk.mount_path / section_prefix / TMP_DIR_NAME / str(record.id)

    @staticmethod
    def _section_disk_prefix(record: FileRecord) -> str:
        user_id = record.user_id
        match record.section:
            case FileSection.FILES:
                return f"users/{user_id}/files"
            case FileSection.PHOTOS:
                return f"users/{user_id}/photos/originals"
            case FileSection.PRIVATE:
                return f"users/{user_id}/private"
            case FileSection.SHARED:
                return "shared"

    @staticmethod
    def _cleanup_disk_tmp_dirs(mount_path: Path, cutoff_ts: float) -> int:
        if not mount_path.exists():
            logger.warning("Disk mount path {} does not exist, skipping tmp cleanup", mount_path)
            return 0

        deleted = 0
        for tmp_dir in mount_path.rglob(TMP_DIR_NAME):
            if not tmp_dir.is_dir():
                continue

            for entry in tmp_dir.iterdir():
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    logger.warning("Failed to stat tmp entry {}", entry)
                    continue

                if mtime >= cutoff_ts:
                    continue

                try:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                    deleted += 1
                    logger.info("Removed stale tmp entry {}", entry)
                except OSError:
                    logger.exception("Failed to remove stale tmp entry {}", entry)

        return deleted

    @classmethod
    def _update_stats(
        cls,
        *,
        pending_deleted: int | None = None,
        tmp_deleted: int | None = None,
        quota_checked: int | None = None,
        quota_fixed: int | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        previous = cls._last_stats

        cls._last_stats = MaintenanceStats(
            last_pending_cleanup=now if pending_deleted is not None else (
                previous.last_pending_cleanup if previous else None
            ),
            pending_deleted=pending_deleted if pending_deleted is not None else (
                previous.pending_deleted if previous else 0
            ),
            last_tmp_cleanup=now if tmp_deleted is not None else (
                previous.last_tmp_cleanup if previous else None
            ),
            tmp_deleted=tmp_deleted if tmp_deleted is not None else (
                previous.tmp_deleted if previous else 0
            ),
            last_quota_reconcile=now if quota_checked is not None else (
                previous.last_quota_reconcile if previous else None
            ),
            quota_checked=quota_checked if quota_checked is not None else (
                previous.quota_checked if previous else 0
            ),
            quota_fixed=quota_fixed if quota_fixed is not None else (
                previous.quota_fixed if previous else 0
            ),
        )
