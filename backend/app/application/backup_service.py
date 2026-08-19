import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import zstandard as zstd
from loguru import logger

from app.domain.exceptions import FileNotFoundError as DomainFileNotFoundError
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import StorageAdapter
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter
from config import Settings, get_settings

BACKUP_RETENTION_DAYS = 30
BACKUP_DIR = "_meta/backups"
BACKUP_FILENAME_PREFIX = "db_backup_"
BACKUP_FILENAME_SUFFIX = ".sql.zst"
ZSTD_COMPRESSION_LEVEL = 19


@dataclass(frozen=True)
class BackupEntry:
    filename: str
    created_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class BackupResult:
    filename: str
    logical_path: str
    size_bytes: int
    disk_id: str


class BackupService:
    def __init__(
        self,
        disk_router: DiskRouter,
        settings: Settings | None = None,
    ) -> None:
        self._disk_router = disk_router
        self._settings = settings or get_settings()

    async def run_db_backup(self) -> BackupResult:
        adapter = self._meta_adapter()
        disk_id = adapter.disk_id
        await adapter.mkdir(BACKUP_DIR)

        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{BACKUP_FILENAME_PREFIX}{timestamp}{BACKUP_FILENAME_SUFFIX}"
        logical_path = f"{BACKUP_DIR}/{filename}"

        logger.bind(
            action="db_backup",
            disk_id=disk_id,
            result="started",
        ).info("Starting database backup to {}", filename)

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

        async def _payload() -> AsyncIterator[bytes]:
            yield compressed

        await adapter.write(logical_path, _payload(), len(compressed))
        deleted = await self._cleanup_old_backups(adapter)
        size_bytes = await adapter.get_size(logical_path)

        logger.bind(
            action="db_backup",
            disk_id=disk_id,
            result="success",
        ).info(
            "Database backup completed: filename={}, size_bytes={}, old_deleted={}",
            filename,
            size_bytes,
            deleted,
        )
        return BackupResult(
            filename=filename,
            logical_path=logical_path,
            size_bytes=size_bytes,
            disk_id=disk_id,
        )

    async def list_backups(self) -> list[BackupEntry]:
        adapter = self._meta_adapter()
        try:
            nodes = await adapter.list(BACKUP_DIR)
        except DomainFileNotFoundError:
            logger.bind(action="db_backup_list", result="success").info(
                "Backup directory missing; returning empty list",
            )
            return []

        entries: list[BackupEntry] = []
        for node in nodes:
            if node.is_dir:
                continue
            if not (
                node.name.startswith(BACKUP_FILENAME_PREFIX)
                and node.name.endswith(BACKUP_FILENAME_SUFFIX)
            ):
                continue
            entries.append(
                BackupEntry(
                    filename=node.name,
                    created_at=node.modified_at,
                    size_bytes=node.size,
                ),
            )

        entries.sort(key=lambda item: item.created_at, reverse=True)
        logger.bind(action="db_backup_list", result="success").info(
            "Listed {} database backups",
            len(entries),
        )
        return entries

    def _meta_adapter(self) -> StorageAdapter:
        disk_id = self._disk_router.get_all_disks()[0].id
        return build_disk_root_adapter(disk_id)

    def _build_pg_dump_dsn(self) -> str:
        database_url = self._settings.database.url
        if "+asyncpg" in database_url:
            return database_url.replace("+asyncpg", "", 1)
        return database_url

    async def _cleanup_old_backups(self, adapter: StorageAdapter) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(days=BACKUP_RETENTION_DAYS)
        try:
            nodes = await adapter.list(BACKUP_DIR)
        except DomainFileNotFoundError:
            return 0

        deleted = 0
        for node in nodes:
            if node.is_dir:
                continue
            if not (
                node.name.startswith(BACKUP_FILENAME_PREFIX)
                and node.name.endswith(BACKUP_FILENAME_SUFFIX)
            ):
                continue
            if node.modified_at >= cutoff:
                continue

            remote_path = f"{BACKUP_DIR}/{node.name}"
            await adapter.delete(remote_path)
            deleted += 1
            logger.bind(action="db_backup_cleanup", result="success").info(
                "Deleted old backup {}",
                node.name,
            )

        return deleted
