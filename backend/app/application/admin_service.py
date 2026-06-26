from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from app.domain.entities.file_record import FileRecord, FileSection
from app.domain.entities.user import User
from app.domain.exceptions import SelfRoleChangeError, SelfUserDeletionError, UserNotFoundError
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.disk_router import DiskRouter
from config import Settings, get_settings

BYTES_PER_GB = 1024 * 1024 * 1024


@dataclass(frozen=True)
class UserAdminView:
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    quota_used_bytes: int
    private_limit_bytes: int


@dataclass(frozen=True)
class DiskStat:
    id: str
    mount_path: str
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
        page: int,
        limit: int,
        role_filter: Role | None = None,
        email_search: str | None = None,
    ) -> dict[str, object]:
        logger.info(
            "Admin listing users: page={}, limit={}, role_filter={}, email_search={}",
            page,
            limit,
            role_filter.value if role_filter else None,
            email_search,
        )
        rows, total = await self._user_repo.list_for_admin(
            page=page,
            limit=limit,
            role_filter=role_filter,
            email_search=email_search,
        )
        items = [
            UserAdminView(
                id=row.user.id,
                email=row.user.email,
                role=row.user.role,
                is_active=row.user.is_active,
                created_at=row.user.created_at,
                quota_used_bytes=row.quota_used_bytes,
                private_limit_bytes=row.private_limit_bytes,
            )
            for row in rows
        ]
        return {"items": items, "total": total}

    async def update_role(
        self,
        admin_id: UUID,
        target_user_id: UUID,
        new_role: Role,
    ) -> User:
        logger.info(
            "Admin {} updating role for user {} to {}",
            admin_id,
            target_user_id,
            new_role.value,
        )
        if admin_id == target_user_id:
            logger.warning("Admin {} attempted to change own role", admin_id)
            raise SelfRoleChangeError("Cannot change your own role")

        user = await self._user_repo.update_role(target_user_id, new_role)
        if user is None:
            logger.error("User {} not found for role update", target_user_id)
            raise UserNotFoundError(f"User {target_user_id} not found")

        logger.info("Role for user {} updated to {} by admin {}", target_user_id, new_role.value, admin_id)
        return user

    async def update_private_quota(
        self,
        admin_id: UUID,
        target_user_id: UUID,
        limit_gb: float,
    ) -> None:
        if limit_gb < 0:
            raise ValueError("Private quota limit cannot be negative")

        limit_bytes = int(limit_gb * BYTES_PER_GB)
        logger.info(
            "Admin {} updating private quota for user {} to {} GB ({} bytes)",
            admin_id,
            target_user_id,
            limit_gb,
            limit_bytes,
        )

        user = await self._user_repo.get_by_id(target_user_id)
        if user is None:
            logger.error("User {} not found for private quota update", target_user_id)
            raise UserNotFoundError(f"User {target_user_id} not found")

        await self._quota_repo.update_private_limit(target_user_id, limit_bytes)
        logger.info(
            "Private quota for user {} updated to {} bytes by admin {}",
            target_user_id,
            limit_bytes,
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
            user_dir = disk.mount_path / "users" / str(user_id)
            if not user_dir.exists():
                continue
            await asyncio.to_thread(shutil.rmtree, user_dir)
            logger.info("Removed user directory for {} on disk {}", user_id, disk.id)

        for record in records:
            if record.section != FileSection.SHARED:
                continue

            try:
                disk = self._disk_router.get_disk_by_id(record.disk_id)
            except KeyError:
                logger.warning(
                    "Skipping shared file cleanup for record {}: disk {} not configured",
                    record.id,
                    record.disk_id,
                )
                continue

            file_path = Path(disk.mount_path) / record.relative_path
            if not file_path.exists():
                logger.warning(
                    "Shared file path {} not found during user {} deletion cleanup",
                    file_path,
                    user_id,
                )
                continue

            if file_path.is_dir():
                await asyncio.to_thread(shutil.rmtree, file_path)
            else:
                await asyncio.to_thread(file_path.unlink)

            logger.info(
                "Removed shared file {} for deleted user {} at {}",
                record.id,
                user_id,
                file_path,
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
                        mount_path=str(disk.mount_path),
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
                    mount_path=str(disk.mount_path),
                    total_bytes=space["total_bytes"],
                    used_bytes=space["used_bytes"],
                    free_bytes=space["free_bytes"],
                    status=health.get(disk.id, "UNAVAILABLE"),
                )
            )

        logger.info("Storage stats collected for {} disks", len(disks))
        return {"disks": disks}
