#!/usr/bin/env python3
"""CLI: migrate local FS blobs under STORAGE_ROOT to MinIO (US-S3-08).

Prerequisites
-------------
1. MinIO is up and buckets exist (``docker compose up -d minio minio-init``).
2. Backup PostgreSQL before switching backends.
3. Keep ``STORAGE_BACKEND=fs`` until this tool finishes and ``--verify-only`` is clean.
4. Configure ``STORAGE_ROOT``, ``STORAGE_DISKS``, and ``S3_*`` via ``.env`` / env
   (loaded by ``get_settings()``).

After a successful migrate + verify, set ``STORAGE_BACKEND=s3`` and restart the app.

Examples
--------
Dry-run (no uploads)::

    cd backend && uv run python scripts/migrate_fs_to_s3.py --dry-run

Migrate one disk::

    cd backend && uv run python scripts/migrate_fs_to_s3.py --disk disk1

Verify objects against DB checksums (and FS trees)::

    cd backend && uv run python scripts/migrate_fs_to_s3.py --verify-only

From the app container::

    docker compose exec app python scripts/migrate_fs_to_s3.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `uv run python scripts/migrate_fs_to_s3.py` from backend/ (Docker sets PYTHONPATH=/app).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.storage.fs_s3_migrator import (
    ChecksumIndex,
    load_checksum_index,
    run_migration,
)
from config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_fs_to_s3.py",
        description=(
            "Walk {STORAGE_ROOT}/{disk_id}/… and upload objects to MinIO with keys "
            "matching disk-relative paths (bucket={S3_BUCKET_PREFIX}{disk_id}). "
            "Optionally verify SHA-256 against file_records.checksum_sha256. "
            "Do NOT set STORAGE_BACKEND=s3 until migrate + verify succeed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Operator switchover:\n"
            "  1. Backup the database.\n"
            "  2. Ensure MinIO is healthy and buckets exist (minio-init).\n"
            "  3. Run with --dry-run, then without flags, then --verify-only.\n"
            "  4. Set STORAGE_BACKEND=s3 in .env and restart the app.\n"
            "Credentials are read via get_settings(); secrets are never logged."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan uploads and log actions without writing to S3.",
    )
    parser.add_argument(
        "--disk",
        metavar="DISK_ID",
        default=None,
        help="Migrate / verify only this disk_id (must be in STORAGE_DISKS).",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not upload; verify existing S3 objects (checksums where present).",
    )
    return parser


async def _load_checksums(disk_ids: list[str]) -> ChecksumIndex:
    async with async_session_factory() as session:
        session: AsyncSession
        return await load_checksum_index(session, disk_ids)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.dry_run and args.verify_only:
        parser.error("--dry-run and --verify-only are mutually exclusive")

    settings = get_settings()
    logger.info(
        "FS→S3 migration starting: storage_root={}, disks={}, backend={}, "
        "s3_endpoint={!r}, bucket_prefix={!r}, dry_run={}, verify_only={}, disk_filter={}",
        settings.storage.root,
        settings.storage.disks,
        settings.storage.backend,
        settings.s3.endpoint_url or None,
        settings.s3.bucket_prefix,
        args.dry_run,
        args.verify_only,
        args.disk,
    )
    if settings.storage.backend == "s3" and not args.verify_only:
        logger.warning(
            "STORAGE_BACKEND is already 's3'; migration normally runs while "
            "backend is still 'fs'. Continuing anyway."
        )

    def checksum_loader(disk_ids: list[str]) -> ChecksumIndex:
        return asyncio.run(_load_checksums(disk_ids))

    try:
        result = run_migration(
            dry_run=args.dry_run,
            verify_only=args.verify_only,
            disk_filter=args.disk,
            settings=settings,
            checksum_loader=checksum_loader,
        )
    except ValueError as exc:
        logger.error("{}", exc)
        return 2
    except Exception:
        logger.exception("Migration aborted due to unexpected error")
        return 1

    if result.ok:
        logger.info(
            "Migration finished successfully. "
            "After --verify-only is clean, set STORAGE_BACKEND=s3 and restart."
        )
        return 0

    logger.error(
        "Migration finished with failures (checksum mismatches / missing objects / errors). "
        "Do not switch STORAGE_BACKEND=s3 until resolved."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
