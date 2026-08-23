#!/usr/bin/env python3
"""CLI: rewrite local users.id to Auth-Service UUIDs (US-AUTHZ-11).

Match local ``users.email`` to Auth ``google_email`` (file input; no live gRPC).
Dry-run by default. Pass ``--apply`` to write PostgreSQL and rename storage
prefixes ``users/{old_id}`` → ``users/{new_id}``.

Does **not** copy local ``role`` into Auth-Service.

Examples
--------
Dry-run::

    cd backend && uv run python scripts/migrate_local_users_to_auth.py auth-users.json

Apply::

    cd backend && uv run python scripts/migrate_local_users_to_auth.py auth-users.json --apply

From the app container::

    docker compose exec app python scripts/migrate_local_users_to_auth.py /tmp/auth-users.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import FileRecord
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.user_uuid_migration import (
    MigrationPlan,
    apply_plan,
    build_plan,
    load_local_users,
    parse_auth_users_file,
    parse_disk_ids,
    rename_storage_user_prefix,
    rewrite_user_prefix,
)
from config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_local_users_to_auth.py",
        description=(
            "Match local users.email to Auth-Service google_email and rewrite "
            "local UUIDs so users/{user_id}/ paths and file_records stay valid. "
            "Dry-run unless --apply is set. Does not PATCH Auth-Service roles."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Auth users file: JSON list (or {\"users\": [...]}) or CSV with columns "
            "id,google_email. ListUsers export can be used later; file input is enough.\n"
            "Unmatched password-only users are reported (hashed emails by default) "
            "and cannot silent-login.\n"
            "Backup PostgreSQL (and FS/MinIO) before --apply."
        ),
    )
    parser.add_argument(
        "auth_users",
        type=Path,
        help="JSON or CSV of Auth users {id, google_email}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write DB remaps and rename storage prefixes. Default is dry-run.",
    )
    parser.add_argument(
        "--list-emails",
        action="store_true",
        help="Print unmatched local emails in plaintext (never passwords).",
    )
    return parser


async def _log_planned_blob_keys(
    session: AsyncSession,
    plan: MigrationPlan,
    backend: str,
) -> None:
    for remap in plan.remaps:
        result = await session.execute(
            select(FileRecord.disk_id, FileRecord.relative_path, FileRecord.archive_path).where(
                FileRecord.user_id == remap.local_id
            )
        )
        rows = result.all()
        logger.info(
            "Checksum local_id={} -> auth_id={}: file_records={} quota_rows={} upload_sessions={}",
            remap.local_id,
            remap.auth_id,
            remap.file_record_count,
            remap.quota_row_count,
            remap.upload_session_count,
        )
        if backend != "s3" or not rows:
            continue
        logger.info(
            "S3 keys to rename for local_id={} (count={})",
            remap.local_id,
            len(rows),
        )
        for disk_id, relative_path, archive_path in rows:
            new_rel = rewrite_user_prefix(relative_path, remap.local_id, remap.auth_id)
            logger.info("S3 object key disk={} {} -> {}", disk_id, relative_path, new_rel)
            if archive_path:
                new_archive = rewrite_user_prefix(
                    archive_path,
                    remap.local_id,
                    remap.auth_id,
                )
                logger.info("S3 archive key disk={} {} -> {}", disk_id, archive_path, new_archive)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger.info(
        "US-AUTHZ-11 starting: apply={}, backend={}, storage_root={}, disks={}, auth_users_file={}",
        args.apply,
        settings.storage.backend,
        settings.storage.root,
        settings.storage.disks,
        args.auth_users,
    )
    logger.info("Database settings loaded via get_settings(); credentials are never logged")

    try:
        auth_users = parse_auth_users_file(args.auth_users)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to read Auth users file: {}", exc)
        return 2

    disk_ids = parse_disk_ids(settings.storage.disks)
    backend = settings.storage.backend
    if backend == "s3":
        logger.info(
            "STORAGE_BACKEND=s3: users/{{old_id}}/ object keys will be copied then "
            "deleted via S3StorageAdapter.rename (list + copy). Dry-run logs keys only."
        )
    else:
        logger.info(
            "STORAGE_BACKEND=fs: will rename {{STORAGE_ROOT}}/{{disk}}/users/{{old_id}} "
            "to users/{{new_id}} on each disk"
        )

    async with async_session_factory() as session:
        local_users = await load_local_users(session)
        plan = build_plan(local_users, auth_users)
        await _log_planned_blob_keys(session, plan, backend)

        async def _renamer(old_id, new_id) -> None:
            await rename_storage_user_prefix(
                old_id,
                new_id,
                disk_ids=disk_ids,
                backend=backend,
            )

        await apply_plan(
            plan,
            apply=args.apply,
            session=session if args.apply else None,
            storage_renamer=_renamer if args.apply else None,
            list_emails=args.list_emails,
        )

    if plan.duplicates:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception:
        logger.exception("US-AUTHZ-11 migration aborted due to unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
