"""Tests for DiskRouter S3 capacity probes (US-S3-06)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.domain.exceptions import StorageUnavailableError
from app.infrastructure.disk_router import (
    DISK_STATUS_HEALTHY,
    DISK_STATUS_LOW_SPACE,
    DISK_STATUS_UNAVAILABLE,
    DiskRouter,
)
from config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client_error(code: str = "404") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "Not Found"}},
        "HeadBucket",
    )


def _s3_settings(monkeypatch: pytest.MonkeyPatch, *, disks: str = "disk1,disk2") -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORAGE_DISKS", disks)
    monkeypatch.setenv("STORAGE_ROOT", "/storage")
    monkeypatch.setenv("MIN_FREE_SPACE_MB", "500")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "30")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_BUCKET_PREFIX", "hc-")
    get_settings.cache_clear()


def test_s3_unavailable_bucket_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch, disks="disk1")

    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404")

    router = DiskRouter(get_settings(), s3_client_factory=lambda: client)

    assert router.health_check() == {"disk1": DISK_STATUS_UNAVAILABLE}
    assert router.get_disk_space_stats("disk1") is None
    client.head_bucket.assert_called_with(Bucket="hc-disk1")


def test_s3_low_free_space_reports_low_space(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch, disks="disk1")

    client = MagicMock()
    client.head_bucket.return_value = {}
    # 100 MiB used against a 200 MiB total → 100 MiB free < 500 MiB min
    client.list_objects_v2.return_value = {
        "Contents": [{"Size": 100 * 1024 * 1024}],
        "IsTruncated": False,
    }

    def capacity_probe(used_bytes: int) -> tuple[int, int]:
        total = 200 * 1024 * 1024
        return total, max(0, total - used_bytes)

    router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: client,
        s3_capacity_probe=capacity_probe,
    )

    assert router.health_check() == {"disk1": DISK_STATUS_LOW_SPACE}
    stats = router.get_disk_space_stats("disk1")
    assert stats is not None
    assert stats["free_bytes"] == 100 * 1024 * 1024


def test_s3_get_write_disk_picks_most_free(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch, disks="disk1,disk2")

    used_by_bucket = {
        "hc-disk1": 800 * 1024 * 1024,
        "hc-disk2": 100 * 1024 * 1024,
    }

    def make_client() -> MagicMock:
        client = MagicMock()

        def head_bucket(*, Bucket: str) -> dict[str, Any]:
            if Bucket not in used_by_bucket:
                raise _client_error("404")
            return {}

        def list_objects_v2(**kwargs: Any) -> dict[str, Any]:
            bucket = kwargs["Bucket"]
            return {
                "Contents": [{"Size": used_by_bucket[bucket]}],
                "IsTruncated": False,
            }

        client.head_bucket.side_effect = head_bucket
        client.list_objects_v2.side_effect = list_objects_v2
        return client

    def capacity_probe(used_bytes: int) -> tuple[int, int]:
        total = 2 * 1024 * 1024 * 1024  # 2 GiB
        return total, max(0, total - used_bytes)

    router = DiskRouter(
        get_settings(),
        s3_client_factory=make_client,
        s3_capacity_probe=capacity_probe,
    )

    selected = router.get_write_disk()
    assert selected.id == "disk2"

    health = router.health_check()
    assert health["disk1"] == DISK_STATUS_HEALTHY
    assert health["disk2"] == DISK_STATUS_HEALTHY


def test_s3_no_healthy_disk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch, disks="disk1")

    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404")

    router = DiskRouter(get_settings(), s3_client_factory=lambda: client)

    with pytest.raises(StorageUnavailableError):
        router.get_write_disk()


def test_fs_mode_still_uses_statvfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    disk1 = tmp_path / "disk1"
    disk2 = tmp_path / "disk2"
    disk1.mkdir()
    disk2.mkdir()

    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("STORAGE_DISKS", "disk1,disk2")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("MIN_FREE_SPACE_MB", "1")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "30")
    get_settings.cache_clear()

    router = DiskRouter(get_settings())

    health = router.health_check()
    assert health["disk1"] == DISK_STATUS_HEALTHY
    assert health["disk2"] == DISK_STATUS_HEALTHY

    selected = router.get_write_disk()
    assert selected.id in {"disk1", "disk2"}

    stats = router.get_disk_space_stats("disk1")
    assert stats is not None
    assert stats["total_bytes"] > 0
    assert stats["free_bytes"] > 0


def test_s3_probe_results_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch, disks="disk1")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "60")
    get_settings.cache_clear()

    client = MagicMock()
    client.head_bucket.return_value = {}
    client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

    def capacity_probe(used_bytes: int) -> tuple[int, int]:
        return 10 * 1024 * 1024 * 1024, 10 * 1024 * 1024 * 1024

    router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: client,
        s3_capacity_probe=capacity_probe,
    )

    assert router.health_check()["disk1"] == DISK_STATUS_HEALTHY
    assert router.health_check()["disk1"] == DISK_STATUS_HEALTHY
    assert client.head_bucket.call_count == 1
