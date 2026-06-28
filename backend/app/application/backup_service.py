import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import zstandard as zstd
from loguru import logger

from app.infrastructure.disk_router import DiskRouter
from config import Settings, get_settings

BACKUP_RETENTION_DAYS = 30
BACKUP_FILENAME_PREFIX = "db_backup_"
BACKUP_FILENAME_SUFFIX = ".sql.zst"
ZSTD_COMPRESSION_LEVEL = 19


@dataclass(frozen=True)
class BackupEntry:
    filename: str
    created_at: datetime
    size_bytes: int


class BackupService:
    def __init__(
        self,
        disk_router: DiskRouter,
        settings: Settings | None = None,
    ) -> None:
        self._disk_router = disk_router
        self._settings = settings or get_settings()

    def run_db_backup(self) -> Path:
        backup_dir = self._ensure_backup_dir()
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H-%M-%S")
        output_path = backup_dir / f"{BACKUP_FILENAME_PREFIX}{timestamp}{BACKUP_FILENAME_SUFFIX}"
        disk_id = self._get_meta_disk_id()

        logger.bind(
            action="db_backup",
            disk_id=disk_id,
            result="started",
        ).info("Starting database backup to {}", output_path.name)

        dsn = self._build_pg_dump_dsn()
        try:
            dump_result = subprocess.run(
                ["pg_dump", dsn, "--no-owner", "--no-acl"],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
            logger.bind(
                action="db_backup",
                disk_id=disk_id,
                result="error",
                error_code="BACKUP_DUMP_FAILED",
            ).error("pg_dump failed: {}", stderr)
            raise RuntimeError("Database backup failed during pg_dump") from exc
        except FileNotFoundError as exc:
            logger.bind(
                action="db_backup",
                disk_id=disk_id,
                result="error",
                error_code="BACKUP_DUMP_FAILED",
            ).error("pg_dump executable not found")
            raise RuntimeError("pg_dump is not available in PATH") from exc

        compressor = zstd.ZstdCompressor(level=ZSTD_COMPRESSION_LEVEL)
        compressed = compressor.compress(dump_result.stdout)
        output_path.write_bytes(compressed)

        deleted = self._cleanup_old_backups(backup_dir)
        size_bytes = output_path.stat().st_size

        logger.bind(
            action="db_backup",
            disk_id=disk_id,
            result="success",
        ).info(
            "Database backup completed: filename={}, size_bytes={}, old_deleted={}",
            output_path.name,
            size_bytes,
            deleted,
        )
        return output_path

    def list_backups(self) -> list[BackupEntry]:
        backup_dir = self._ensure_backup_dir()
        entries: list[BackupEntry] = []

        for path in sorted(
            backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            stat = path.stat()
            entries.append(
                BackupEntry(
                    filename=path.name,
                    created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    size_bytes=stat.st_size,
                ),
            )

        logger.bind(action="db_backup_list", result="success").info(
            "Listed {} database backups",
            len(entries),
        )
        return entries

    def _ensure_backup_dir(self) -> Path:
        disk = self._disk_router.get_all_disks()[0]
        backup_dir = disk.mount_path / "_meta" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def _get_meta_disk_id(self) -> str:
        return self._disk_router.get_all_disks()[0].id

    def _build_pg_dump_dsn(self) -> str:
        database_url = self._settings.database.url
        if "+asyncpg" in database_url:
            return database_url.replace("+asyncpg", "", 1)
        return database_url

    def _cleanup_old_backups(self, backup_dir: Path) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(days=BACKUP_RETENTION_DAYS)
        deleted = 0

        for path in backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if mtime >= cutoff:
                continue
            path.unlink()
            deleted += 1
            logger.bind(action="db_backup_cleanup", result="success").info(
                "Deleted old backup {}",
                path.name,
            )

        return deleted
