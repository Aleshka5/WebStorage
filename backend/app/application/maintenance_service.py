from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.exceptions import FileNotFoundError as DomainFileNotFoundError
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter

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
            adapter = build_disk_root_adapter(disk.id)
            stale_paths = await adapter.list_stale_tmp_entry_paths(cutoff_ts)
            for path in stale_paths:
                try:
                    await adapter.delete(path)
                    deleted += 1
                    logger.info(
                        "Removed stale tmp entry {} on disk {}",
                        path,
                        disk.id,
                    )
                except DomainFileNotFoundError:
                    logger.warning(
                        "Stale tmp entry {} already gone on disk {}",
                        path,
                        disk.id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to remove stale tmp entry {} on disk {}",
                        path,
                        disk.id,
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
        adapter = build_disk_root_adapter(record.disk_id)
        tmp_path = self._resolve_tmp_logical_path(record)
        try:
            if not await adapter.exists(tmp_path):
                logger.info(
                    "No tmp file to delete for stale PENDING record {} at {}",
                    record.id,
                    tmp_path,
                )
                return
            await adapter.delete(tmp_path)
            logger.info(
                "Deleted tmp file for stale PENDING record {} at {}",
                record.id,
                tmp_path,
            )
        except DomainFileNotFoundError:
            logger.info(
                "No tmp file to delete for stale PENDING record {} at {}",
                record.id,
                tmp_path,
            )

    @staticmethod
    def _resolve_tmp_logical_path(record: FileRecord) -> str:
        section_prefix = MaintenanceService._section_disk_prefix(record)
        return f"{section_prefix}/{TMP_DIR_NAME}/{record.id}"

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
