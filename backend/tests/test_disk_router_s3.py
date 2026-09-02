"""Tests for DiskRouter S3 capacity probes (single bucket storage)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.domain.exceptions import StorageUnavailableError
from app.infrastructure.disk_router import (
    DISK_STATUS_HEALTHY,
    DISK_STATUS_LOW_SPACE,
    DISK_STATUS_UNAVAILABLE,
    STORAGE_VOLUME_ID,
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


def _s3_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_FREE_SPACE_MB", "500")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "30")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_BUCKET", "storage")
    get_settings.cache_clear()


def test_s3_unavailable_bucket_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)

    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404")

    router = DiskRouter(get_settings(), s3_client_factory=lambda: client)

    assert router.health_check() == {STORAGE_VOLUME_ID: DISK_STATUS_UNAVAILABLE}
    assert router.get_disk_space_stats(STORAGE_VOLUME_ID) is None
    client.head_bucket.assert_called_with(Bucket="storage")


def test_s3_low_free_space_reports_low_space(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)

    client = MagicMock()
    client.head_bucket.return_value = {}
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

    assert router.health_check() == {STORAGE_VOLUME_ID: DISK_STATUS_LOW_SPACE}
    stats = router.get_disk_space_stats(STORAGE_VOLUME_ID)
    assert stats is not None
    assert stats["free_bytes"] == 100 * 1024 * 1024


def test_s3_get_write_disk_returns_single_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)

    client = MagicMock()
    client.head_bucket.return_value = {}
    client.list_objects_v2.return_value = {
        "Contents": [{"Size": 100 * 1024 * 1024}],
        "IsTruncated": False,
    }

    def capacity_probe(used_bytes: int) -> tuple[int, int]:
        total = 2 * 1024 * 1024 * 1024
        return total, max(0, total - used_bytes)

    router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: client,
        s3_capacity_probe=capacity_probe,
    )

    selected = router.get_write_disk()
    assert selected.id == STORAGE_VOLUME_ID
    assert selected.bucket == "storage"
    assert router.health_check()[STORAGE_VOLUME_ID] == DISK_STATUS_HEALTHY


def test_s3_no_healthy_disk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)

    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404")

    router = DiskRouter(get_settings(), s3_client_factory=lambda: client)

    with pytest.raises(StorageUnavailableError):
        router.get_write_disk()


def test_disk_volume_exposes_s3_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)

    router = DiskRouter(get_settings(), s3_client_factory=lambda: MagicMock())
    disks = router.get_all_disks()

    assert len(disks) == 1
    assert disks[0].id == STORAGE_VOLUME_ID
    assert disks[0].bucket == "storage"
    assert not hasattr(disks[0], "mount_path")


def test_s3_probe_results_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_settings(monkeypatch)
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

    assert router.health_check()[STORAGE_VOLUME_ID] == DISK_STATUS_HEALTHY
    assert router.health_check()[STORAGE_VOLUME_ID] == DISK_STATUS_HEALTHY
    assert client.head_bucket.call_count == 1
