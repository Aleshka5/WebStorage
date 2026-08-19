"""Unit tests for FS→S3 migration helpers (US-S3-08)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from moto.server import ThreadedMotoServer

from app.infrastructure.storage.fs_s3_migrator import (
    FsToS3Migrator,
    bucket_for_disk,
    iter_disk_files,
    parse_disk_ids,
    resolve_target_disks,
    run_migration,
    sha256_file,
    should_skip_existing,
)
from config import get_settings

BUCKET_PREFIX = "hc-"
DISK_ID = "disk1"


@pytest.fixture(scope="module")
def moto_endpoint() -> str:
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    endpoint = f"http://{host}:{port}"
    yield endpoint
    server.stop()


@pytest.fixture
def s3_client(moto_endpoint: str, monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("S3_ENDPOINT_URL", moto_endpoint)
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET_PREFIX", BUCKET_PREFIX)
    monkeypatch.setenv("S3_PATH_STYLE", "true")
    get_settings.cache_clear()

    client = boto3.client(
        "s3",
        endpoint_url=moto_endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = f"{BUCKET_PREFIX}{DISK_ID}"
    client.create_bucket(Bucket=bucket)
    yield client
    get_settings.cache_clear()


def test_parse_and_resolve_disks() -> None:
    assert parse_disk_ids("disk1, disk2 ,") == ["disk1", "disk2"]
    assert resolve_target_disks("disk1,disk2") == ["disk1", "disk2"]
    assert resolve_target_disks("disk1,disk2", disk_filter="disk2") == ["disk2"]
    with pytest.raises(ValueError, match="not listed"):
        resolve_target_disks("disk1", disk_filter="disk9")
    with pytest.raises(ValueError, match="at least one"):
        resolve_target_disks(" , ")


def test_bucket_for_disk() -> None:
    assert bucket_for_disk("disk1", "") == "disk1"
    assert bucket_for_disk("disk1", "hc-") == "hc-disk1"


def test_should_skip_existing() -> None:
    assert should_skip_existing(local_size=10, remote_size=10) is True
    assert should_skip_existing(local_size=10, remote_size=9) is False
    assert should_skip_existing(local_size=10, remote_size=None) is False


def test_iter_disk_files_skips_tmp(tmp_path: Path) -> None:
    disk_root = tmp_path / DISK_ID
    (disk_root / "users" / "u1" / "files").mkdir(parents=True)
    target = disk_root / "users" / "u1" / "files" / "a.txt"
    target.write_bytes(b"hello")
    tmp_file = disk_root / ".tmp" / "upload.bin"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_bytes(b"tmp")

    files = list(iter_disk_files(disk_root, DISK_ID))
    assert len(files) == 1
    assert files[0].object_key == "users/u1/files/a.txt"
    assert files[0].size_bytes == 5


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    payload = b"abc123"
    path.write_bytes(payload)
    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_migrate_uploads_and_resumes(
    tmp_path: Path,
    s3_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk_root = tmp_path / DISK_ID
    rel = Path("users") / "u1" / "files" / "doc.txt"
    local = disk_root / rel
    local.parent.mkdir(parents=True)
    payload = b"migrate-me"
    local.write_bytes(payload)

    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_DISKS", DISK_ID)
    get_settings.cache_clear()

    file_id = uuid4()
    expected = hashlib.sha256(payload).hexdigest()
    checksum_index = {(DISK_ID, rel.as_posix()): (file_id, expected)}

    migrator = FsToS3Migrator(
        storage_root=tmp_path,
        bucket_prefix=BUCKET_PREFIX,
        s3_client=s3_client,
        checksum_index=checksum_index,
    )
    first = migrator.migrate([DISK_ID], dry_run=False, verify_only=False)
    assert first.ok
    assert first.stats.uploaded == 1
    assert first.stats.checksum_checked == 1
    assert first.stats.verified_ok == 1
    assert first.stats.checksum_mismatches == 0

    bucket = f"{BUCKET_PREFIX}{DISK_ID}"
    obj = s3_client.get_object(Bucket=bucket, Key=rel.as_posix())
    assert obj["Body"].read() == payload

    second = migrator.migrate([DISK_ID], dry_run=False, verify_only=False)
    assert second.ok
    assert second.stats.uploaded == 0
    assert second.stats.skipped_existing == 1


def test_dry_run_does_not_upload(
    tmp_path: Path,
    s3_client,
) -> None:
    disk_root = tmp_path / DISK_ID
    local = disk_root / "shared" / "note.txt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"dry")

    migrator = FsToS3Migrator(
        storage_root=tmp_path,
        bucket_prefix=BUCKET_PREFIX,
        s3_client=s3_client,
    )
    result = migrator.migrate([DISK_ID], dry_run=True, verify_only=False)
    assert result.ok
    assert result.stats.dry_run_planned == 1
    assert result.stats.uploaded == 0

    bucket = f"{BUCKET_PREFIX}{DISK_ID}"
    with pytest.raises(ClientError):
        s3_client.head_object(Bucket=bucket, Key="shared/note.txt")


def test_checksum_mismatch_logged_as_failure(
    tmp_path: Path,
    s3_client,
) -> None:
    disk_root = tmp_path / DISK_ID
    key = "users/u1/files/bad.txt"
    local = disk_root / Path(key)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"actual")

    file_id = uuid4()
    checksum_index = {(DISK_ID, key): (file_id, "0" * 64)}

    migrator = FsToS3Migrator(
        storage_root=tmp_path,
        bucket_prefix=BUCKET_PREFIX,
        s3_client=s3_client,
        checksum_index=checksum_index,
    )
    result = migrator.migrate([DISK_ID])
    assert result.ok is False
    assert result.stats.checksum_mismatches == 1
    assert result.stats.uploaded == 1


def test_verify_only_detects_missing(
    tmp_path: Path,
    s3_client,
) -> None:
    disk_root = tmp_path / DISK_ID
    local = disk_root / "shared" / "gone.txt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"x")

    migrator = FsToS3Migrator(
        storage_root=tmp_path,
        bucket_prefix=BUCKET_PREFIX,
        s3_client=s3_client,
    )
    result = migrator.migrate([DISK_ID], verify_only=True)
    assert result.ok is False
    assert result.stats.missing_in_s3 == 1


def test_run_migration_with_injected_settings(
    tmp_path: Path,
    s3_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk_root = tmp_path / DISK_ID
    local = disk_root / "shared" / "z.txt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"zz")

    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_DISKS", DISK_ID)
    monkeypatch.setenv("S3_BUCKET_PREFIX", BUCKET_PREFIX)
    get_settings.cache_clear()
    settings = get_settings()

    result = run_migration(
        settings=settings,
        s3_client=s3_client,
        checksum_index={},
    )
    assert result.ok
    assert result.stats.uploaded == 1
