from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from loguru import logger

from app.application.ports.user_directory import DirectoryUser
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.entities.user import User
from app.domain.exceptions import SelfUserDeletionError, UserNotFoundError
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.repositories.user_repo import UserAdminRow, UserRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.s3_adapter import build_disk_root_adapter
from config import Settings, get_settings

BYTES_PER_MB = 1024 * 1024
BYTES_PER_GB = 1024 * 1024 * 1024


@dataclass(frozen=True)
class UserAdminView:
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    quota_used_bytes: int
    limit_bytes: int
    private_limit_bytes: int


@dataclass(frozen=True)
class DiskStat:
    id: str
    bucket: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    status: str


class AdminService:
    def __init__(
        self,
        user_repo: UserRepository,
        quota_repo: QuotaRepository,
        file_repo: FileRepository,
        disk_router: DiskRouter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._user_repo = user_repo
        self._quota_repo = quota_repo
        self._file_repo = file_repo
        self._settings = settings or get_settings()
        self._disk_router = disk_router or DiskRouter(self._settings)

    async def list_users(
        self,
        directory_users: list[DirectoryUser],
        page: int,
        limit: int,
        role_filter: Role | None = None,
        email_search: str | None = None,
    ) -> dict[str, object]:
        logger.info(
            "Admin listing users from User-Service GET /users: count={}, page={}, limit={}, role_filter={}",
            len(directory_users),
            page,
            limit,
            role_filter.value if role_filter else None,
        )
        filtered = self._filter_directory_users(directory_users, role_filter, email_search)
        total = len(filtered)
        offset = (page - 1) * limit
        page_users = filtered[offset : offset + limit]
        local_rows = await self._user_repo.get_admin_rows_by_ids(
            [item.id for item in page_users]
        )
        items = [
            self._view_from_directory_user(item, local_rows.get(item.id))
            for item in page_users
        ]
        logger.info(
            "Admin user list joined storage roles with local quota: page_items={}, total={}",
            len(items),
            total,
        )
        return {"items": items, "total": total}

    @staticmethod
    def _filter_directory_users(
        directory_users: list[DirectoryUser],
        role_filter: Role | None,
        email_search: str | None,
    ) -> list[DirectoryUser]:
        filtered = directory_users
        if role_filter is not None:
            filtered = [item for item in filtered if item.storage_role == role_filter]
        if email_search:
            needle = email_search.casefold()
            filtered = [item for item in filtered if needle in item.email.casefold()]
        return filtered

    def _default_limit_bytes(self) -> int:
        return self._settings.business_logic.default_user_quota_bytes

    def _view_from_directory_user(
        self,
        directory_user: DirectoryUser,
        local: UserAdminRow | None,
    ) -> UserAdminView:
        if local is None:
            return UserAdminView(
                id=directory_user.id,
                email=directory_user.email,
                role=directory_user.storage_role,
                is_active=True,
                created_at=datetime.now(UTC),
                quota_used_bytes=0,
                limit_bytes=self._default_limit_bytes(),
                private_limit_bytes=0,
            )
        return UserAdminView(
            id=directory_user.id,
            email=directory_user.email,
            role=directory_user.storage_role,
            is_active=local.user.is_active,
            created_at=local.user.created_at,
            quota_used_bytes=local.quota_used_bytes,
            limit_bytes=local.limit_bytes,
            private_limit_bytes=local.private_limit_bytes,
        )

    async def _ensure_local_user(
        self,
        target_user_id: UUID,
        directory_users: list[DirectoryUser],
        operation: str,
    ) -> None:
        directory_user = next(
            (item for item in directory_users if item.id == target_user_id),
            None,
        )
        if directory_user is None:
            logger.error(
                "User {} not found in User-Service GET /users for {}",
                target_user_id,
                operation,
            )
            raise UserNotFoundError(f"User {target_user_id} not found")

        principal = AuthPrincipal(
            id=directory_user.id,
            email=directory_user.email,
            name=directory_user.username,
            role=directory_user.storage_role,
        )
        user = await self._user_repo.upsert_from_principal(principal)
        if user is None:
            logger.error(
                "Cannot project user {} for {} (email/UUID conflict)",
                target_user_id,
                operation,
            )
            raise RuntimeError(f"Cannot project user {target_user_id} for {operation}")

    async def update_user_quota(
        self,
        admin_id: UUID,
        target_user_id: UUID,
        directory_users: list[DirectoryUser],
        *,
        limit_mb: float | None = None,
        private_limit_gb: float | None = None,
    ) -> None:
        if limit_mb is None and private_limit_gb is None:
            raise ValueError("At least one of limit_mb or private_limit_gb is required")
        if limit_mb is not None and limit_mb < 0:
            raise ValueError("Total quota limit cannot be negative")
        if private_limit_gb is not None and private_limit_gb < 0:
            raise ValueError("Private quota limit cannot be negative")

        await self._ensure_local_user(target_user_id, directory_users, "quota update")

        if limit_mb is not None:
            limit_bytes = int(limit_mb * BYTES_PER_MB)
            logger.info(
                "Admin {} updating total quota for user {} to {} MB ({} bytes)",
                admin_id,
                target_user_id,
                limit_mb,
                limit_bytes,
            )
            await self._quota_repo.update_limit(target_user_id, limit_bytes)
            logger.info(
                "Total quota for user {} updated to {} bytes by admin {}",
                target_user_id,
                limit_bytes,
                admin_id,
            )

        if private_limit_gb is not None:
            private_limit_bytes = int(private_limit_gb * BYTES_PER_GB)
            logger.info(
                "Admin {} updating private quota for user {} to {} GB ({} bytes)",
                admin_id,
                target_user_id,
                private_limit_gb,
                private_limit_bytes,
            )
            await self._quota_repo.update_private_limit(target_user_id, private_limit_bytes)
            logger.info(
                "Private quota for user {} updated to {} bytes by admin {}",
                target_user_id,
                private_limit_bytes,
                admin_id,
            )

    async def block_user(self, admin_id: UUID, target_user_id: UUID) -> None:
        logger.info("Admin {} blocking user {}", admin_id, target_user_id)

        user = await self._user_repo.set_active(target_user_id, is_active=False)
        if user is None:
            logger.error("User {} not found for block operation", target_user_id)
            raise UserNotFoundError(f"User {target_user_id} not found")

        logger.info("User {} blocked by admin {}", target_user_id, admin_id)

    async def delete_user(self, admin_id: UUID, target_user_id: UUID) -> None:
        logger.info("Admin {} deleting user {}", admin_id, target_user_id)

        if admin_id == target_user_id:
            logger.warning("Admin {} attempted to delete own account", admin_id)
            raise SelfUserDeletionError("Cannot delete your own account")

        user = await self._user_repo.get_by_id(target_user_id)
        if user is None:
            logger.error("User {} not found for deletion", target_user_id)
            raise UserNotFoundError(f"User {target_user_id} not found")

        records = await self._file_repo.list_all_by_user(target_user_id)
        await self._cleanup_user_storage(target_user_id, records)

        deleted = await self._user_repo.delete(target_user_id)
        if not deleted:
            raise UserNotFoundError(f"User {target_user_id} not found")

        logger.info("User {} deleted by admin {}", target_user_id, admin_id)

    async def _cleanup_user_storage(self, user_id: UUID, records: list[FileRecord]) -> None:
        for disk in self._disk_router.get_all_disks():
            adapter = build_disk_root_adapter(disk.id)
            user_prefix = f"users/{user_id}"
            try:
                if await adapter.exists(user_prefix):
                    await adapter.delete(user_prefix)
                    logger.info(
                        "Removed user storage prefix {} on disk {}",
                        user_prefix,
                        disk.id,
                    )
            except Exception:
                logger.exception(
                    "Failed to remove user storage prefix {} on disk {}",
                    user_prefix,
                    disk.id,
                )

        for record in records:
            if record.section != FileSection.SHARED:
                continue

            try:
                self._disk_router.get_disk_by_id(record.disk_id)
            except KeyError:
                logger.warning(
                    "Skipping shared file cleanup for record {}: disk {} not configured",
                    record.id,
                    record.disk_id,
                )
                continue

            adapter = build_disk_root_adapter(record.disk_id)
            path = record.archive_path or record.relative_path
            try:
                if await adapter.exists(path):
                    await adapter.delete(path)
                    logger.info(
                        "Removed shared file {} for deleted user {} at {}",
                        record.id,
                        user_id,
                        path,
                    )
                else:
                    logger.warning(
                        "Shared file path {} not found during user {} deletion cleanup",
                        path,
                        user_id,
                    )
            except Exception:
                logger.exception(
                    "Failed shared file cleanup for record {} user {}",
                    record.id,
                    user_id,
                )

    def get_storage_stats(self) -> dict[str, list[DiskStat]]:
        health = self._disk_router.health_check()
        disks: list[DiskStat] = []

        for disk in self._disk_router.get_all_disks():
            space = self._disk_router.get_disk_space_stats(disk.id)
            if space is None:
                disks.append(
                    DiskStat(
                        id=disk.id,
                        bucket=disk.bucket,
                        total_bytes=0,
                        used_bytes=0,
                        free_bytes=0,
                        status=health.get(disk.id, "UNAVAILABLE"),
                    )
                )
                continue

            disks.append(
                DiskStat(
                    id=disk.id,
                    bucket=disk.bucket,
                    total_bytes=space["total_bytes"],
                    used_bytes=space["used_bytes"],
                    free_bytes=space["free_bytes"],
                    status=health.get(disk.id, "UNAVAILABLE"),
                )
            )

        logger.info("Storage stats collected for {} disks", len(disks))
        return {"disks": disks}
