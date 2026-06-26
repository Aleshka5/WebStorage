import os
import time
from pathlib import Path

from loguru import logger

from app.domain.entities.disk_volume import DiskVolume
from app.domain.exceptions import StorageUnavailableError
from config import Settings, get_settings

DISK_STATUS_HEALTHY = "HEALTHY"
DISK_STATUS_LOW_SPACE = "LOW_SPACE"
DISK_STATUS_UNAVAILABLE = "UNAVAILABLE"


class DiskRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._space_cache: dict[str, tuple[int, float]] = {}
        self._disks = self._build_disk_volumes()

    def _build_disk_volumes(self) -> list[DiskVolume]:
        storage = self._settings.storage
        root = Path(storage.root)
        disk_ids = [disk_id.strip() for disk_id in storage.disks.split(",") if disk_id.strip()]
        disks = [
            DiskVolume(
                id=disk_id,
                mount_path=root / disk_id,
                priority=index,
                is_active=True,
            )
            for index, disk_id in enumerate(disk_ids)
        ]
        logger.info("DiskRouter initialized with {} disks at root {}", len(disks), storage.root)
        return disks

    def get_all_disks(self) -> list[DiskVolume]:
        return [disk for disk in self._disks if disk.is_active]

    def get_disk_by_id(self, disk_id: str) -> DiskVolume:
        for disk in self._disks:
            if disk.id == disk_id:
                return disk
        raise KeyError(f"Disk {disk_id} is not configured")

    def get_write_disk(self) -> DiskVolume:
        min_free_bytes = self._settings.storage.min_free_space_mb * 1024 * 1024
        candidates: list[tuple[DiskVolume, int]] = []

        for disk in self.get_all_disks():
            free_space = self._probe_free_space(disk)
            if free_space is None:
                logger.warning("Disk {} is unavailable for write", disk.id)
                continue
            if free_space < min_free_bytes:
                logger.warning(
                    "Disk {} has insufficient free space: {} bytes (minimum {} bytes)",
                    disk.id,
                    free_space,
                    min_free_bytes,
                )
                continue
            candidates.append((disk, free_space))

        if not candidates:
            logger.error("No storage disks available for write operations")
            raise StorageUnavailableError("No storage disks are available for write operations")

        selected_disk, free_space = max(candidates, key=lambda item: item[1])
        logger.info("Selected disk {} for write with {} bytes free", selected_disk.id, free_space)
        return selected_disk

    def get_free_space(self, disk_id: str) -> int:
        disk = self.get_disk_by_id(disk_id)
        cached = self._space_cache.get(disk_id)
        ttl = self._settings.storage.disk_space_cache_ttl
        now = time.monotonic()

        if cached is not None and now - cached[1] < ttl:
            return cached[0]

        free_space = self._probe_free_space(disk)
        if free_space is None:
            raise StorageUnavailableError(f"Disk {disk_id} is unavailable")

        self._space_cache[disk_id] = (free_space, now)
        return free_space

    def health_check(self) -> dict[str, str]:
        min_free_bytes = self._settings.storage.min_free_space_mb * 1024 * 1024
        result: dict[str, str] = {}

        for disk in self._disks:
            free_space = self._probe_free_space(disk)
            if free_space is None:
                result[disk.id] = DISK_STATUS_UNAVAILABLE
            elif free_space < min_free_bytes:
                result[disk.id] = DISK_STATUS_LOW_SPACE
            else:
                result[disk.id] = DISK_STATUS_HEALTHY

        logger.info("Disk health check completed: {}", result)
        return result

    def _probe_free_space(self, disk: DiskVolume) -> int | None:
        mount_path = disk.mount_path
        if not mount_path.exists():
            return None
        try:
            stat = os.statvfs(mount_path)
            return stat.f_frsize * stat.f_bavail
        except OSError:
            logger.warning("Failed to stat disk {} at {}", disk.id, mount_path)
            return None
