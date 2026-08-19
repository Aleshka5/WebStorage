"""US-S3-05: DI factory selects FS vs S3; private marker via adapter API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.private_service import PrivateService
from app.infrastructure.storage.encrypted_adapter import MARKER_FILENAME, EncryptedStorageAdapter
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter
from app.infrastructure.storage.s3_adapter import S3StorageAdapter, create_storage_adapter
from app.presentation.dependencies.storage_factory import (
    build_section_adapter,
    user_private_root_prefix,
)
from config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_create_storage_adapter_selects_plain_for_fs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    get_settings.cache_clear()

    base = tmp_path / "disk1" / "users" / "u1" / "files"
    base.mkdir(parents=True)

    adapter = create_storage_adapter(
        disk_id="disk1",
        root_prefix="users/u1/files",
        base_path=base,
    )

    assert isinstance(adapter, PlainStorageAdapter)
    assert adapter.disk_id == "disk1"
    assert adapter.root_prefix == "users/u1/files"


def test_create_storage_adapter_selects_s3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_BUCKET_PREFIX", "hc-")
    get_settings.cache_clear()

    adapter = create_storage_adapter(
        disk_id="disk1",
        root_prefix="users/u1/files",
    )

    assert isinstance(adapter, S3StorageAdapter)
    assert adapter.disk_id == "disk1"
    assert adapter.root_prefix == "users/u1/files"


def test_create_storage_adapter_fs_requires_base_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="base_path is required"):
        create_storage_adapter(disk_id="disk1", root_prefix="users/u1/files")


@pytest.mark.asyncio
async def test_build_section_adapter_fs_mkdirs_under_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_DISKS", "disk1")
    get_settings.cache_clear()

    (tmp_path / "disk1").mkdir(parents=True)
    user_id = uuid4()
    root_prefix = f"users/{user_id}/photos"

    adapter = await build_section_adapter(
        "disk1",
        root_prefix,
        ensure_subdirs=("originals", "previews"),
        user_id=user_id,
    )

    assert isinstance(adapter, PlainStorageAdapter)
    base = tmp_path / "disk1" / "users" / str(user_id) / "photos"
    assert base.is_dir()
    assert (base / "originals").is_dir()
    assert (base / "previews").is_dir()


@pytest.mark.asyncio
async def test_private_unlock_creates_and_validates_marker_via_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_DISKS", "disk1")
    get_settings.cache_clear()

    (tmp_path / "disk1").mkdir(parents=True)
    user_id = uuid4()
    session_id = "sess-1"
    passphrase = "correct-horse-battery"

    session_store = MagicMock()
    session_store.set_private_key = AsyncMock()
    session_store.delete_private_key = AsyncMock()
    session_store.get_private_key = AsyncMock(return_value=None)
    session_store.get_private_key_ttl = AsyncMock(return_value=0)

    file_repo = MagicMock()
    file_repo.list_by_user_section = AsyncMock(return_value=[])
    quota_repo = MagicMock()

    service = PrivateService(
        session_store=session_store,
        quota_repo=quota_repo,
        file_repo=file_repo,
    )

    unlocked = await service.unlock(user_id, session_id, passphrase)
    assert unlocked is True
    session_store.set_private_key.assert_awaited_once()

    marker_path = (
        tmp_path / "disk1" / "users" / str(user_id) / "private" / MARKER_FILENAME
    )
    assert marker_path.is_file()

    # Wrong passphrase must fail against existing marker (adapter-backed validate).
    denied = await service.unlock(user_id, "sess-2", "wrong-passphrase")
    assert denied is False

    # Correct passphrase unlocks again.
    unlocked_again = await service.unlock(user_id, "sess-3", passphrase)
    assert unlocked_again is True


@pytest.mark.asyncio
async def test_private_reset_deletes_via_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_DISKS", "disk1")
    get_settings.cache_clear()

    (tmp_path / "disk1").mkdir(parents=True)
    user_id = uuid4()
    root_prefix = user_private_root_prefix(user_id)

    inner = await build_section_adapter("disk1", root_prefix, user_id=user_id)
    key = b"0" * 32
    encrypted = EncryptedStorageAdapter(inner=inner, key=key)
    await encrypted.write_marker()

    private_dir = tmp_path / "disk1" / "users" / str(user_id) / "private"
    assert (private_dir / MARKER_FILENAME).is_file()

    session_store = MagicMock()
    session_store.delete_private_key = AsyncMock()
    file_repo = MagicMock()
    file_repo.list_by_user_section = AsyncMock(return_value=[])
    file_repo.delete_all_by_user_section = AsyncMock(return_value=0)
    quota_repo = MagicMock()
    quota_repo.reset_private_usage = AsyncMock()

    service = PrivateService(
        session_store=session_store,
        quota_repo=quota_repo,
        file_repo=file_repo,
    )
    await service.reset_storage(user_id, "sess-reset")

    assert not (private_dir / MARKER_FILENAME).exists()
    assert private_dir.is_dir()
    file_repo.delete_all_by_user_section.assert_awaited_once()
    quota_repo.reset_private_usage.assert_awaited_once_with(user_id)
