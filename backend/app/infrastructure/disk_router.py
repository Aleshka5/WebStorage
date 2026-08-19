"""Disk volume selection and health probes.

FS backend (``STORAGE_BACKEND=fs``)
----------------------------------
Free/total space comes from ``statvfs`` / ``df`` on ``STORAGE_ROOT/{disk_id}``.

S3 backend (``STORAGE_BACKEND=s3``)
----------------------------------
Each ``STORAGE_DISKS`` entry maps to bucket ``{S3_BUCKET_PREFIX}{disk_id}``.
Availability is probed with ``HeadBucket``. Used bytes are the sum of object
``Size`` values in that bucket. Total capacity prefers MinIO Admin API
(``GET /minio/admin/v3/info`` drive totals); when that is unavailable, a
documented fallback total (1 TiB) is used so ``free ≈ total − used`` still
drives ``HEALTHY`` / ``LOW_SPACE`` against ``MIN_FREE_SPACE_MB``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from app.domain.entities.disk_volume import DiskVolume
from app.domain.exceptions import StorageUnavailableError
from config import Settings, get_settings

DISK_STATUS_HEALTHY = "HEALTHY"
DISK_STATUS_LOW_SPACE = "LOW_SPACE"
DISK_STATUS_UNAVAILABLE = "UNAVAILABLE"

# Used when MinIO Admin capacity is unreachable (no secrets; operator-facing approximation).
_S3_FALLBACK_TOTAL_BYTES = 1 << 40  # 1 TiB


class DiskRouter:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        s3_client_factory: Callable[[], Any] | None = None,
        s3_capacity_probe: Callable[[int], tuple[int, int]] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._s3_client_factory = s3_client_factory
        self._s3_capacity_probe = s3_capacity_probe
        self._space_cache: dict[str, tuple[dict[str, int] | None, float]] = {}
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
        logger.info(
            "DiskRouter initialized with {} disks at root {} (backend={})",
            len(disks),
            storage.root,
            storage.backend,
        )
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
        free_space = self._probe_free_space(disk)
        if free_space is None:
            raise StorageUnavailableError(f"Disk {disk_id} is unavailable")
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
        space_info = self._probe_disk_space(disk)
        if space_info is None:
            return None
        return space_info["free_bytes"]

    def get_disk_space_stats(self, disk_id: str) -> dict[str, int] | None:
        disk = self.get_disk_by_id(disk_id)
        return self._probe_disk_space(disk)

    def _probe_disk_space(self, disk: DiskVolume) -> dict[str, int] | None:
        cached = self._space_cache.get(disk.id)
        ttl = self._settings.storage.disk_space_cache_ttl
        now = time.monotonic()
        if cached is not None and now - cached[1] < ttl:
            return cached[0]

        if self._settings.storage.backend == "s3":
            result = self._probe_s3_disk_space(disk)
        else:
            result = self._probe_fs_disk_space(disk)

        self._space_cache[disk.id] = (result, now)
        return result

    def _bucket_for_disk(self, disk_id: str) -> str:
        return f"{self._settings.s3.bucket_prefix}{disk_id}"

    def _create_s3_client(self) -> Any:
        if self._s3_client_factory is not None:
            return self._s3_client_factory()

        s3 = self._settings.s3
        addressing = "path" if s3.path_style else "virtual"
        return boto3.client(
            "s3",
            endpoint_url=s3.endpoint_url or None,
            aws_access_key_id=s3.access_key or None,
            aws_secret_access_key=s3.secret_key or None,
            region_name=s3.region,
            use_ssl=s3.use_ssl,
            config=Config(s3={"addressing_style": addressing}),
        )

    def _probe_s3_disk_space(self, disk: DiskVolume) -> dict[str, int] | None:
        bucket = self._bucket_for_disk(disk.id)
        try:
            client = self._create_s3_client()
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "unknown")
            logger.warning(
                "S3 bucket {} for disk {} is unavailable (error={})",
                bucket,
                disk.id,
                error_code,
            )
            return None
        except (BotoCoreError, OSError):
            logger.exception(
                "Failed to reach S3 endpoint while probing disk {} (bucket={})",
                disk.id,
                bucket,
            )
            return None

        try:
            used_bytes = self._s3_bucket_used_bytes(client, bucket)
            total_bytes, free_bytes = self._s3_resolve_capacity(used_bytes)
        except (BotoCoreError, ClientError, OSError):
            logger.exception(
                "Failed to measure S3 capacity for disk {} (bucket={})",
                disk.id,
                bucket,
            )
            return None

        logger.info(
            "S3 disk {} bucket {} capacity: total_bytes={}, used_bytes={}, free_bytes={}",
            disk.id,
            bucket,
            total_bytes,
            used_bytes,
            free_bytes,
        )
        return {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
        }

    def _s3_bucket_used_bytes(self, client: Any, bucket: str) -> int:
        used = 0
        continuation: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            response = client.list_objects_v2(**kwargs)
            for obj in response.get("Contents") or ():
                used += int(obj.get("Size") or 0)
            if not response.get("IsTruncated"):
                break
            continuation = response.get("NextContinuationToken")
            if not continuation:
                break
        return used

    def _s3_resolve_capacity(self, used_bytes: int) -> tuple[int, int]:
        """Return ``(total_bytes, free_bytes)`` for routing and health.

        Prefers MinIO Admin drive totals; otherwise uses the 1 TiB fallback so
        ``free = max(0, total − used)`` remains comparable across buckets.
        """
        if self._s3_capacity_probe is not None:
            return self._s3_capacity_probe(used_bytes)

        admin_total = self._probe_minio_admin_total_bytes()
        total_bytes = admin_total if admin_total is not None else _S3_FALLBACK_TOTAL_BYTES
        if admin_total is None:
            logger.warning(
                "MinIO admin capacity unavailable; using fallback total_bytes={}",
                _S3_FALLBACK_TOTAL_BYTES,
            )
        free_bytes = max(0, total_bytes - used_bytes)
        return total_bytes, free_bytes

    def _probe_minio_admin_total_bytes(self) -> int | None:
        s3 = self._settings.s3
        endpoint = (s3.endpoint_url or "").rstrip("/")
        if not endpoint or not s3.access_key or not s3.secret_key:
            return None

        url = f"{endpoint}/minio/admin/v3/info"
        try:
            credentials = Credentials(s3.access_key, s3.secret_key)
            request = AWSRequest(method="GET", url=url)
            SigV4Auth(credentials, "s3", s3.region).add_auth(request)
            prepared = request.prepare()
            http_request = urllib.request.Request(
                prepared.url,
                headers=dict(prepared.headers),
                method="GET",
            )
            with urllib.request.urlopen(http_request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            logger.warning("MinIO admin info probe failed for endpoint {}", endpoint)
            return None

        total = 0
        found = False
        for server in payload.get("servers") or ():
            drives = server.get("drives") or server.get("Disks") or ()
            for drive in drives:
                space = drive.get("totalSpace", drive.get("TotalSpace"))
                if space is None:
                    continue
                total += int(space)
                found = True
        if not found:
            logger.warning("MinIO admin info response had no drive totalSpace fields")
            return None
        return total

    def _probe_fs_disk_space(self, disk: DiskVolume) -> dict[str, int] | None:
        mount_path = disk.mount_path
        if not mount_path.exists():
            return None
        try:
            stat = os.statvfs(mount_path)
            block_size = stat.f_frsize
            total_bytes = stat.f_blocks * block_size
            free_bytes = stat.f_bavail * block_size
            used_bytes = total_bytes - free_bytes
            # FUSE/9p mounts may return zero sizes — fall back to `df`
            if total_bytes == 0 and free_bytes == 0:
                return self._probe_disk_space_via_df(mount_path)
            return {
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "free_bytes": free_bytes,
            }
        except OSError:
            logger.warning("Failed to stat disk {} at {}", disk.id, mount_path)
            return None

    def _probe_disk_space_via_df(self, mount_path: Path) -> dict[str, int] | None:
        """Fallback: use `df -B1` to get byte-accurate space from FUSE mounts."""
        try:
            result = os.popen(f"df -B1 {mount_path}").read().strip().splitlines()
            if len(result) < 2:
                return None
            # Skip header, take last line (handles spaces in mount points)
            parts = result[-1].split()
            if len(parts) < 4:
                return None
            total = int(parts[1])
            used = int(parts[2])
            avail = int(parts[3])
            return {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": avail,
            }
        except (OSError, ValueError, IndexError):
            logger.warning("Failed to get disk space via df for {}", mount_path)
            return None
