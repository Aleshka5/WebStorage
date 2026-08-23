"""Match local HomeCloud users to Auth-Service UUIDs (US-AUTHZ-11).

Rewrites ``users.id`` (and FK ``user_id`` columns) so ``users/{user_id}/``
storage prefixes and ``file_records`` stay valid. Does **not** PATCH Auth-Service
roles. Password-only unmatched users are reported; they cannot silent-login.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import FileRecord, UploadSession, User, UserQuotaUsage

StoragePrefixRenamer = Callable[[UUID, UUID], Awaitable[None]]

_USER_PREFIX = "users/"
_EMAIL_HASH_LEN = 16
_MIGRATING_EMAIL_DOMAIN = "authz11.invalid"


@dataclass(frozen=True)
class AuthUserRow:
    id: UUID
    google_email: str


@dataclass(frozen=True)
class LocalUserRow:
    id: UUID
    email: str
    file_record_count: int = 0
    quota_row_count: int = 0
    upload_session_count: int = 0


@dataclass(frozen=True)
class UserIdRemap:
    local_id: UUID
    auth_id: UUID
    email_hash: str
    file_record_count: int = 0
    quota_row_count: int = 0
    upload_session_count: int = 0


@dataclass(frozen=True)
class DuplicateMatch:
    email_hash: str
    reason: str
    local_ids: tuple[UUID, ...] = ()
    auth_ids: tuple[UUID, ...] = ()


@dataclass
class MigrationPlan:
    remaps: list[UserIdRemap] = field(default_factory=list)
    unmatched_local: list[LocalUserRow] = field(default_factory=list)
    already_aligned: list[LocalUserRow] = field(default_factory=list)
    duplicates: list[DuplicateMatch] = field(default_factory=list)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_email(email: str) -> str:
    digest = hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()
    return digest[:_EMAIL_HASH_LEN]


def rewrite_user_prefix(path: str | None, old_id: UUID, new_id: UUID) -> str | None:
    """Rewrite ``users/{old_id}`` prefix in a disk-relative path. None stays None."""
    if path is None:
        return None
    old_prefix = f"{_USER_PREFIX}{old_id}"
    new_prefix = f"{_USER_PREFIX}{new_id}"
    if path == old_prefix or path.startswith(f"{old_prefix}/"):
        return new_prefix + path[len(old_prefix) :]
    return path


def parse_auth_users_file(path: Path) -> list[AuthUserRow]:
    """Load Auth-Service users from JSON or CSV ``{id, google_email}``."""
    if not path.is_file():
        raise FileNotFoundError(f"Auth users file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        rows = _parse_auth_users_csv(path)
    elif suffix in {".json", ".jsonl"}:
        rows = _parse_auth_users_json(path)
    else:
        try:
            rows = _parse_auth_users_json(path)
        except json.JSONDecodeError:
            rows = _parse_auth_users_csv(path)

    if not rows:
        logger.warning("Auth users file {} contained zero rows", path)
    else:
        logger.info("Loaded {} Auth user(s) from {}", len(rows), path)
    return rows


def _parse_auth_users_json(path: Path) -> list[AuthUserRow]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_rows = payload.get("users")
        if raw_rows is None:
            raise ValueError("JSON object must contain a 'users' array of {id, google_email}")
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raise ValueError("Auth users JSON must be a list or an object with 'users'")

    rows: list[AuthUserRow] = []
    for index, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            raise ValueError(f"Auth users JSON item {index} is not an object")
        rows.append(_auth_user_from_mapping(item, source=f"{path}:{index}"))
    return rows


def _parse_auth_users_csv(path: Path) -> list[AuthUserRow]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV {path} has no header row")
        rows: list[AuthUserRow] = []
        for index, item in enumerate(reader):
            rows.append(_auth_user_from_mapping(item, source=f"{path}:{index + 2}"))
        return rows


def _auth_user_from_mapping(item: dict[str, Any], *, source: str) -> AuthUserRow:
    raw_id = item.get("id")
    raw_email = item.get("google_email")
    if raw_email is None or raw_email == "":
        raw_email = item.get("email")
    if raw_id is None or str(raw_id).strip() == "":
        raise ValueError(f"Missing Auth user id in {source}")
    if raw_email is None or str(raw_email).strip() == "":
        raise ValueError(f"Missing Auth google_email in {source}")
    try:
        user_id = UUID(str(raw_id).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid Auth user UUID in {source}: {raw_id!r}") from exc
    return AuthUserRow(id=user_id, google_email=str(raw_email).strip())


def build_plan(
    local_users: Sequence[LocalUserRow],
    auth_users: Sequence[AuthUserRow],
) -> MigrationPlan:
    """Match local ``users.email`` to Auth ``google_email`` (case-insensitive).

    Duplicate matches are errors and are skipped (not remapped).
    """
    auth_by_email: dict[str, list[AuthUserRow]] = defaultdict(list)
    auth_by_id: dict[UUID, list[AuthUserRow]] = defaultdict(list)
    for auth in auth_users:
        auth_by_email[normalize_email(auth.google_email)].append(auth)
        auth_by_id[auth.id].append(auth)

    local_by_email: dict[str, list[LocalUserRow]] = defaultdict(list)
    local_ids = {row.id for row in local_users}
    for local in local_users:
        local_by_email[normalize_email(local.email)].append(local)

    duplicate_auth_emails = {email for email, rows in auth_by_email.items() if len(rows) > 1}
    duplicate_local_emails = {email for email, rows in local_by_email.items() if len(rows) > 1}
    duplicate_auth_ids = {user_id for user_id, rows in auth_by_id.items() if len(rows) > 1}

    plan = MigrationPlan()
    claimed_auth_ids: dict[UUID, UUID] = {}

    for local in local_users:
        email_key = normalize_email(local.email)
        email_digest = hash_email(local.email)

        if email_key in duplicate_local_emails:
            siblings = local_by_email[email_key]
            plan.duplicates.append(
                DuplicateMatch(
                    email_hash=email_digest,
                    reason="multiple_local_emails",
                    local_ids=tuple(row.id for row in siblings),
                )
            )
            logger.error(
                "Duplicate local email hash={} local_ids={}; skipping (US-AUTHZ-11)",
                email_digest,
                [str(row.id) for row in siblings],
            )
            continue

        if email_key in duplicate_auth_emails:
            siblings = auth_by_email[email_key]
            plan.duplicates.append(
                DuplicateMatch(
                    email_hash=email_digest,
                    reason="multiple_auth_emails",
                    local_ids=(local.id,),
                    auth_ids=tuple(row.id for row in siblings),
                )
            )
            logger.error(
                "Duplicate Auth google_email hash={} auth_ids={}; skipping local_id={}",
                email_digest,
                [str(row.id) for row in siblings],
                local.id,
            )
            continue

        matches = auth_by_email.get(email_key, [])
        if not matches:
            plan.unmatched_local.append(local)
            continue

        auth = matches[0]
        if auth.id in duplicate_auth_ids:
            plan.duplicates.append(
                DuplicateMatch(
                    email_hash=email_digest,
                    reason="duplicate_auth_id",
                    local_ids=(local.id,),
                    auth_ids=(auth.id,),
                )
            )
            logger.error(
                "Duplicate Auth id={} in input file; skipping local_id={}",
                auth.id,
                local.id,
            )
            continue

        if local.id == auth.id:
            plan.already_aligned.append(local)
            logger.info(
                "Local user_id={} already matches Auth UUID (email_hash={})",
                local.id,
                email_digest,
            )
            continue

        if auth.id in local_ids and auth.id != local.id:
            plan.duplicates.append(
                DuplicateMatch(
                    email_hash=email_digest,
                    reason="auth_id_already_used_locally",
                    local_ids=(local.id, auth.id),
                    auth_ids=(auth.id,),
                )
            )
            logger.error(
                "Auth UUID {} already used by another local user; skipping remap of local_id={}",
                auth.id,
                local.id,
            )
            continue

        claimed_by = claimed_auth_ids.get(auth.id)
        if claimed_by is not None and claimed_by != local.id:
            plan.duplicates.append(
                DuplicateMatch(
                    email_hash=email_digest,
                    reason="auth_id_claimed_twice",
                    local_ids=(claimed_by, local.id),
                    auth_ids=(auth.id,),
                )
            )
            logger.error(
                "Auth UUID {} claimed by local_id={} and local_id={}; skipping",
                auth.id,
                claimed_by,
                local.id,
            )
            continue

        claimed_auth_ids[auth.id] = local.id
        remap = UserIdRemap(
            local_id=local.id,
            auth_id=auth.id,
            email_hash=email_digest,
            file_record_count=local.file_record_count,
            quota_row_count=local.quota_row_count,
            upload_session_count=local.upload_session_count,
        )
        plan.remaps.append(remap)
        logger.info(
            "Plan remap local_id={} -> auth_id={} email_hash={} "
            "file_records={} quota_rows={} upload_sessions={}",
            remap.local_id,
            remap.auth_id,
            remap.email_hash,
            remap.file_record_count,
            remap.quota_row_count,
            remap.upload_session_count,
        )

    _dedupe_duplicate_entries(plan)
    return plan


def _dedupe_duplicate_entries(plan: MigrationPlan) -> None:
    seen: set[tuple[str, str, tuple[UUID, ...]]] = set()
    unique: list[DuplicateMatch] = []
    for item in plan.duplicates:
        key = (item.reason, item.email_hash, item.local_ids)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    plan.duplicates = unique


def log_unmatched_users(
    unmatched: Sequence[LocalUserRow],
    *,
    list_emails: bool = False,
) -> None:
    logger.warning(
        "Unmatched local users (password-only / no Google link): count={}. "
        "They cannot log in after Auth-Service cutover; no silent login (US-AUTHZ-11)",
        len(unmatched),
    )
    for row in unmatched:
        if list_emails:
            logger.warning(
                "Unmatched local user_id={} email={} (email listed by operator flag; no password)",
                row.id,
                row.email,
            )
        else:
            logger.warning(
                "Unmatched local user_id={} email_hash={}",
                row.id,
                hash_email(row.email),
            )


def log_plan_summary(plan: MigrationPlan, *, apply: bool, list_emails: bool = False) -> None:
    mode = "apply" if apply else "dry-run"
    logger.info(
        "US-AUTHZ-11 plan ({}): remaps={} unmatched={} already_aligned={} duplicates={}",
        mode,
        len(plan.remaps),
        len(plan.unmatched_local),
        len(plan.already_aligned),
        len(plan.duplicates),
    )
    log_unmatched_users(plan.unmatched_local, list_emails=list_emails)
    if plan.duplicates:
        logger.error(
            "Duplicate matches skipped: count={}. Fix the Auth export / local emails and re-run",
            len(plan.duplicates),
        )
    logger.info(
        "Local users.role is not copied into Auth-Service. Review storage_roles in hub admin"
    )
    if not apply:
        logger.info("Dry-run: no DB or storage writes")


async def load_local_users(session: AsyncSession) -> list[LocalUserRow]:
    result = await session.execute(select(User.id, User.email))
    users = [(user_id, email) for user_id, email in result.all()]
    rows: list[LocalUserRow] = []
    for user_id, email in users:
        counts = await count_user_fk_rows(session, user_id)
        rows.append(
            LocalUserRow(
                id=user_id,
                email=email,
                file_record_count=counts["file_records"],
                quota_row_count=counts["quota_rows"],
                upload_session_count=counts["upload_sessions"],
            )
        )
    logger.info("Loaded {} local user(s) from PostgreSQL", len(rows))
    return rows


async def count_user_fk_rows(session: AsyncSession, user_id: UUID) -> dict[str, int]:
    file_count = await session.scalar(
        select(func.count()).select_from(FileRecord).where(FileRecord.user_id == user_id)
    )
    quota_count = await session.scalar(
        select(func.count()).select_from(UserQuotaUsage).where(UserQuotaUsage.user_id == user_id)
    )
    session_count = await session.scalar(
        select(func.count())
        .select_from(UploadSession)
        .where(UploadSession.user_id == user_id)
    )
    return {
        "file_records": int(file_count or 0),
        "quota_rows": int(quota_count or 0),
        "upload_sessions": int(session_count or 0),
    }


async def apply_plan(
    plan: MigrationPlan,
    *,
    apply: bool,
    session: AsyncSession | None = None,
    storage_renamer: StoragePrefixRenamer | None = None,
    list_emails: bool = False,
) -> None:
    """Apply remaps. Dry-run (apply=False) performs no DB or storage writes."""
    log_plan_summary(plan, apply=apply, list_emails=list_emails)
    if not apply:
        return
    if session is None:
        raise ValueError("session is required when apply=True")

    applied = 0
    for remap in plan.remaps:
        if await _apply_one_remap(
            session,
            remap,
            storage_renamer=storage_renamer,
        ):
            applied += 1
    logger.info("Committed {} user UUID remap(s)", applied)


async def _apply_one_remap(
    session: AsyncSession,
    remap: UserIdRemap,
    *,
    storage_renamer: StoragePrefixRenamer | None,
) -> bool:
    existing_target = await session.get(User, remap.auth_id)
    if existing_target is not None:
        logger.error(
            "Target Auth UUID {} already exists locally; skipping local_id={}",
            remap.auth_id,
            remap.local_id,
        )
        return False

    user = await session.get(User, remap.local_id)
    if user is None:
        logger.error("Local user_id={} disappeared before remap; skipping", remap.local_id)
        return False

    renamed = False
    try:
        if storage_renamer is not None:
            await storage_renamer(remap.local_id, remap.auth_id)
            renamed = True
        await _rewrite_local_user_row(session, user, remap)
        await session.commit()
        logger.info(
            "Remapped local_id={} -> auth_id={} email_hash={}",
            remap.local_id,
            remap.auth_id,
            remap.email_hash,
        )
        return True
    except Exception:
        logger.exception(
            "Failed remapping local_id={} -> auth_id={}; attempting storage rollback",
            remap.local_id,
            remap.auth_id,
        )
        await session.rollback()
        if renamed and storage_renamer is not None:
            try:
                await storage_renamer(remap.auth_id, remap.local_id)
            except Exception:
                logger.exception(
                    "Failed rolling back storage prefix auth_id={} -> local_id={}",
                    remap.auth_id,
                    remap.local_id,
                )
        raise


async def _rewrite_local_user_row(
    session: AsyncSession,
    user: User,
    remap: UserIdRemap,
) -> None:
    original_email = user.email
    user.email = f"migrating+{remap.local_id}@{_MIGRATING_EMAIL_DOMAIN}"
    await session.flush()

    replacement = User(
        id=remap.auth_id,
        email=original_email,
        password_hash=user.password_hash,
        google_id=user.google_id,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )
    session.add(replacement)
    await session.flush()

    file_result = await session.execute(
        select(FileRecord).where(FileRecord.user_id == remap.local_id)
    )
    for record in file_result.scalars().all():
        rewritten = rewrite_user_prefix(record.relative_path, remap.local_id, remap.auth_id)
        if rewritten is not None:
            record.relative_path = rewritten
        record.archive_path = rewrite_user_prefix(
            record.archive_path,
            remap.local_id,
            remap.auth_id,
        )
        record.user_id = remap.auth_id

    await session.execute(
        update(UserQuotaUsage)
        .where(UserQuotaUsage.user_id == remap.local_id)
        .values(user_id=remap.auth_id)
    )
    await session.execute(
        update(UploadSession)
        .where(UploadSession.user_id == remap.local_id)
        .values(user_id=remap.auth_id)
    )
    await session.delete(user)
    await session.flush()


async def rename_storage_user_prefix(
    old_id: UUID,
    new_id: UUID,
    *,
    disk_ids: Sequence[str],
    backend: str,
    adapter_factory: Callable[[str], Any] | None = None,
) -> None:
    """Rename ``users/{old_id}`` → ``users/{new_id}`` on each configured disk.

    FS uses directory rename. S3 uses ``S3StorageAdapter.rename`` (list + copy +
    delete of the object prefix). Keys are logged when a prefix is missing.
    """
    from app.domain.exceptions import FileNotFoundError
    from app.infrastructure.storage.s3_adapter import build_disk_root_adapter

    factory = adapter_factory or build_disk_root_adapter
    old_prefix = f"{_USER_PREFIX}{old_id}"
    new_prefix = f"{_USER_PREFIX}{new_id}"

    for disk_id in disk_ids:
        adapter = factory(disk_id)
        try:
            dest_exists = await adapter.exists(new_prefix)
        except FileNotFoundError:
            dest_exists = False
        if dest_exists:
            logger.error(
                "Storage destination prefix {} already exists on disk={} backend={}; not renaming",
                new_prefix,
                disk_id,
                backend,
            )
            raise FileExistsError(
                f"Destination prefix {new_prefix} already exists on disk {disk_id}"
            )

        try:
            source_exists = await adapter.exists(old_prefix)
        except FileNotFoundError:
            source_exists = False
        if not source_exists:
            logger.info(
                "No storage prefix {} on disk={} backend={}; skipping disk",
                old_prefix,
                disk_id,
                backend,
            )
            continue

        await adapter.rename(old_prefix, new_prefix)
        logger.info(
            "Renamed storage prefix {} -> {} on disk={} backend={}",
            old_prefix,
            new_prefix,
            disk_id,
            backend,
        )


def parse_disk_ids(disks: str) -> list[str]:
    return [disk_id.strip() for disk_id in disks.split(",") if disk_id.strip()]
