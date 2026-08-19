"""FS → MinIO (S3) blob migration helpers (US-S3-08).

Walks ``{STORAGE_ROOT}/{disk_id}/…``, uploads objects with keys isomorphic to
disk-relative POSIX paths into bucket ``{S3_BUCKET_PREFIX}{disk_id}``, and
optionally verifies SHA-256 against ``file_records.checksum_sha256``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import FileRecord as FileRecordModel
from app.infrastructure.storage.base_adapter import HIDDEN_DIR_NAMES, READ_CHUNK_SIZE
from config import Settings, get_settings

ChecksumIndex = dict[tuple[str, str], tuple[UUID, str]]


@dataclass(frozen=True)
class DiskFile:
    """A regular file under a disk root."""

    absolute_path: Path
    disk_id: str
    object_key: str
    size_bytes: int


@dataclass
class MigrationStats:
    """Counters for a migration / verify run."""

    files_seen: int = 0
    bytes_seen: int = 0
    uploaded: int = 0
    bytes_uploaded: int = 0
    skipped_existing: int = 0
    dry_run_planned: int = 0
    verified_ok: int = 0
    checksum_mismatches: int = 0
    missing_in_s3: int = 0
    checksum_checked: int = 0
    errors: int = 0

    def summary_line(self) -> str:
        return (
            "Migration summary: "
            f"files_seen={self.files_seen}, bytes_seen={self.bytes_seen}, "
            f"uploaded={self.uploaded}, bytes_uploaded={self.bytes_uploaded}, "
            f"skipped_existing={self.skipped_existing}, "
            f"dry_run_planned={self.dry_run_planned}, "
            f"checksum_checked={self.checksum_checked}, "
            f"verified_ok={self.verified_ok}, "
            f"checksum_mismatches={self.checksum_mismatches}, "
            f"missing_in_s3={self.missing_in_s3}, errors={self.errors}"
        )


@dataclass
class MigrationResult:
    stats: MigrationStats = field(default_factory=MigrationStats)
    ok: bool = True


def parse_disk_ids(disks_csv: str) -> list[str]:
    return [part.strip() for part in disks_csv.split(",") if part.strip()]


def resolve_target_disks(
    disks_csv: str,
    *,
    disk_filter: str | None = None,
) -> list[str]:
    disks = parse_disk_ids(disks_csv)
    if not disks:
        raise ValueError("STORAGE_DISKS must contain at least one disk identifier")
    if disk_filter is None:
        return disks
    if disk_filter not in disks:
        raise ValueError(
            f"Disk {disk_filter!r} is not listed in STORAGE_DISKS={disks_csv!r}"
        )
    return [disk_filter]


def bucket_for_disk(disk_id: str, bucket_prefix: str) -> str:
    return f"{bucket_prefix}{disk_id}"


def iter_disk_files(disk_root: Path, disk_id: str) -> Iterator[DiskFile]:
    """Yield regular files under ``disk_root`` (skip ``.tmp`` trees)."""
    if not disk_root.is_dir():
        logger.warning("Disk root missing or not a directory: {}", disk_root)
        return

    for path in disk_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(disk_root)
        except ValueError:
            logger.warning("Skipping path outside disk root: {}", path)
            continue

        parts = relative.parts
        if any(part in HIDDEN_DIR_NAMES for part in parts):
            continue

        yield DiskFile(
            absolute_path=path,
            disk_id=disk_id,
            object_key=relative.as_posix(),
            size_bytes=path.stat().st_size,
        )


def sha256_file(path: Path, *, chunk_size: int = READ_CHUNK_SIZE) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_stream(body: Any, *, chunk_size: int = READ_CHUNK_SIZE) -> str:
    hasher = hashlib.sha256()
    while True:
        chunk = body.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()


def should_skip_existing(*, local_size: int, remote_size: int | None) -> bool:
    """Resume heuristic: skip upload when remote object exists with the same size."""
    return remote_size is not None and remote_size == local_size


def create_s3_client(settings: Settings | None = None) -> BaseClient:
    """Build a sync boto3 S3 client from ``get_settings().s3`` (no secrets logged)."""
    cfg = (settings or get_settings()).s3
    addressing = "path" if cfg.path_style else "virtual"
    logger.info(
        "Creating S3 client for migration: endpoint={!r}, region={}, path_style={}",
        cfg.endpoint_url or None,
        cfg.region,
        cfg.path_style,
    )
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url or None,
        aws_access_key_id=cfg.access_key or None,
        aws_secret_access_key=cfg.secret_key or None,
        region_name=cfg.region,
        use_ssl=cfg.use_ssl,
        config=Config(s3={"addressing_style": addressing}),
    )


async def load_checksum_index(
    session: AsyncSession,
    disk_ids: list[str],
) -> ChecksumIndex:
    """Map ``(disk_id, relative_path)`` → ``(file_id, checksum_sha256)``."""
    stmt = (
        select(
            FileRecordModel.id,
            FileRecordModel.disk_id,
            FileRecordModel.relative_path,
            FileRecordModel.checksum_sha256,
        )
        .where(FileRecordModel.disk_id.in_(disk_ids))
        .where(FileRecordModel.checksum_sha256.is_not(None))
    )
    result = await session.execute(stmt)
    index: ChecksumIndex = {}
    for file_id, disk_id, relative_path, checksum in result.all():
        if not checksum:
            continue
        key = (disk_id, relative_path)
        index[key] = (file_id, checksum)
    logger.info(
        "Loaded {} file_records with checksum_sha256 for disks={}",
        len(index),
        disk_ids,
    )
    return index


class FsToS3Migrator:
    """One-shot / resumable-ish FS → S3 uploader with checksum verification."""

    def __init__(
        self,
        *,
        storage_root: Path,
        bucket_prefix: str,
        s3_client: BaseClient,
        checksum_index: Mapping[tuple[str, str], tuple[UUID, str]] | None = None,
        progress_every: int = 100,
    ) -> None:
        self._storage_root = storage_root
        self._bucket_prefix = bucket_prefix
        self._s3 = s3_client
        self._checksum_index: Mapping[tuple[str, str], tuple[UUID, str]] = (
            checksum_index or {}
        )
        self._progress_every = max(1, progress_every)

    def _bucket(self, disk_id: str) -> str:
        return bucket_for_disk(disk_id, self._bucket_prefix)

    def _head_size(self, bucket: str, key: str) -> int | None:
        try:
            response = self._s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound", "404 Not Found"}:
                return None
            raise
        return int(response.get("ContentLength") or 0)

    def _upload_file(self, bucket: str, key: str, path: Path) -> None:
        self._s3.upload_file(str(path), bucket, key)

    def _object_sha256(self, bucket: str, key: str) -> str | None:
        try:
            response = self._s3.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}:
                return None
            raise
        body = response["Body"]
        try:
            return sha256_stream(body)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()

    def _check_db_checksum(
        self,
        stats: MigrationStats,
        disk_file: DiskFile,
        *,
        actual_checksum: str,
    ) -> None:
        entry = self._checksum_index.get((disk_file.disk_id, disk_file.object_key))
        if entry is None:
            return
        file_id, expected = entry
        stats.checksum_checked += 1
        if actual_checksum.lower() != expected.lower():
            stats.checksum_mismatches += 1
            logger.error(
                "Checksum mismatch file_id={} disk_id={} path={} expected={} actual={}",
                file_id,
                disk_file.disk_id,
                disk_file.object_key,
                expected,
                actual_checksum,
            )
        else:
            stats.verified_ok += 1

    def _log_progress(self, stats: MigrationStats, disk_id: str) -> None:
        if stats.files_seen % self._progress_every == 0:
            logger.info(
                "Progress disk_id={}: files_done={}, bytes_done={}, uploaded={}, skipped={}",
                disk_id,
                stats.files_seen,
                stats.bytes_seen,
                stats.uploaded,
                stats.skipped_existing,
            )

    def migrate_disk(
        self,
        disk_id: str,
        *,
        dry_run: bool = False,
        verify_only: bool = False,
    ) -> MigrationStats:
        disk_root = self._storage_root / disk_id
        bucket = self._bucket(disk_id)
        stats = MigrationStats()

        logger.info(
            "Starting FS→S3 pass: disk_id={}, disk_root={}, bucket={}, dry_run={}, verify_only={}",
            disk_id,
            disk_root,
            bucket,
            dry_run,
            verify_only,
        )

        for disk_file in iter_disk_files(disk_root, disk_id):
            stats.files_seen += 1
            stats.bytes_seen += disk_file.size_bytes
            try:
                if verify_only:
                    self._verify_one(stats, bucket, disk_file)
                elif dry_run:
                    remote_size = self._head_size(bucket, disk_file.object_key)
                    if should_skip_existing(
                        local_size=disk_file.size_bytes,
                        remote_size=remote_size,
                    ):
                        stats.skipped_existing += 1
                        logger.info(
                            "Dry-run would skip existing object disk_id={} key={} size={}",
                            disk_id,
                            disk_file.object_key,
                            disk_file.size_bytes,
                        )
                    else:
                        stats.dry_run_planned += 1
                        logger.info(
                            "Dry-run would upload disk_id={} key={} size={}",
                            disk_id,
                            disk_file.object_key,
                            disk_file.size_bytes,
                        )
                    local_hash = sha256_file(disk_file.absolute_path)
                    self._check_db_checksum(stats, disk_file, actual_checksum=local_hash)
                else:
                    self._upload_one(stats, bucket, disk_file)
            except Exception:
                stats.errors += 1
                logger.exception(
                    "Failed processing disk_id={} key={}",
                    disk_id,
                    disk_file.object_key,
                )

            self._log_progress(stats, disk_id)

        logger.info(
            "Finished disk_id={}: {}",
            disk_id,
            stats.summary_line(),
        )
        return stats

    def _upload_one(self, stats: MigrationStats, bucket: str, disk_file: DiskFile) -> None:
        remote_size = self._head_size(bucket, disk_file.object_key)
        if should_skip_existing(
            local_size=disk_file.size_bytes,
            remote_size=remote_size,
        ):
            stats.skipped_existing += 1
            logger.info(
                "Skipping existing object (same size) disk_id={} key={} size={}",
                disk_file.disk_id,
                disk_file.object_key,
                disk_file.size_bytes,
            )
            local_hash = sha256_file(disk_file.absolute_path)
            self._check_db_checksum(stats, disk_file, actual_checksum=local_hash)
            return

        self._upload_file(bucket, disk_file.object_key, disk_file.absolute_path)
        stats.uploaded += 1
        stats.bytes_uploaded += disk_file.size_bytes
        logger.info(
            "Uploaded disk_id={} key={} size={}",
            disk_file.disk_id,
            disk_file.object_key,
            disk_file.size_bytes,
        )
        local_hash = sha256_file(disk_file.absolute_path)
        self._check_db_checksum(stats, disk_file, actual_checksum=local_hash)

    def _verify_one(self, stats: MigrationStats, bucket: str, disk_file: DiskFile) -> None:
        remote_hash = self._object_sha256(bucket, disk_file.object_key)
        if remote_hash is None:
            stats.missing_in_s3 += 1
            logger.error(
                "Object missing in S3 during verify-only disk_id={} key={}",
                disk_file.disk_id,
                disk_file.object_key,
            )
            return

        entry = self._checksum_index.get((disk_file.disk_id, disk_file.object_key))
        if entry is None:
            local_hash = sha256_file(disk_file.absolute_path)
            if local_hash.lower() != remote_hash.lower():
                stats.checksum_mismatches += 1
                logger.error(
                    "FS vs S3 content mismatch (no DB checksum) disk_id={} key={}",
                    disk_file.disk_id,
                    disk_file.object_key,
                )
            else:
                stats.verified_ok += 1
            return

        self._check_db_checksum(stats, disk_file, actual_checksum=remote_hash)

    def migrate(
        self,
        disk_ids: list[str],
        *,
        dry_run: bool = False,
        verify_only: bool = False,
    ) -> MigrationResult:
        total = MigrationStats()
        result = MigrationResult(stats=total, ok=True)

        for disk_id in disk_ids:
            disk_stats = self.migrate_disk(
                disk_id,
                dry_run=dry_run,
                verify_only=verify_only,
            )
            total.files_seen += disk_stats.files_seen
            total.bytes_seen += disk_stats.bytes_seen
            total.uploaded += disk_stats.uploaded
            total.bytes_uploaded += disk_stats.bytes_uploaded
            total.skipped_existing += disk_stats.skipped_existing
            total.dry_run_planned += disk_stats.dry_run_planned
            total.verified_ok += disk_stats.verified_ok
            total.checksum_mismatches += disk_stats.checksum_mismatches
            total.missing_in_s3 += disk_stats.missing_in_s3
            total.checksum_checked += disk_stats.checksum_checked
            total.errors += disk_stats.errors

        if (
            total.checksum_mismatches
            or total.missing_in_s3
            or total.errors
        ):
            result.ok = False

        logger.info("All disks: {}", total.summary_line())
        return result


def run_migration(
    *,
    dry_run: bool = False,
    verify_only: bool = False,
    disk_filter: str | None = None,
    settings: Settings | None = None,
    s3_client: BaseClient | None = None,
    checksum_index: Mapping[tuple[str, str], tuple[UUID, str]] | None = None,
    checksum_loader: Callable[[list[str]], ChecksumIndex] | None = None,
) -> MigrationResult:
    """Orchestrate migration using settings (injectable for tests)."""
    cfg = settings or get_settings()
    disk_ids = resolve_target_disks(cfg.storage.disks, disk_filter=disk_filter)
    storage_root = Path(cfg.storage.root)

    index = checksum_index
    if index is None and checksum_loader is not None:
        index = checksum_loader(disk_ids)

    client = s3_client or create_s3_client(cfg)
    migrator = FsToS3Migrator(
        storage_root=storage_root,
        bucket_prefix=cfg.s3.bucket_prefix,
        s3_client=client,
        checksum_index=index,
    )
    return migrator.migrate(
        disk_ids,
        dry_run=dry_run,
        verify_only=verify_only,
    )
