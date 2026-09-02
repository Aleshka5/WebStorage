"""US-S3-05: DI factory always returns S3; private marker via adapter API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aioboto3
import pytest
from botocore.config import Config
from moto.server import ThreadedMotoServer

from app.application.private_service import PrivateService
from app.infrastructure.storage.encrypted_adapter import MARKER_FILENAME, EncryptedStorageAdapter
from app.infrastructure.storage.s3_adapter import S3StorageAdapter, create_storage_adapter
from app.presentation.dependencies.storage_factory import (
    build_section_adapter,
    user_private_root_prefix,
)
from config import get_settings

DISK_ID = "storage"
BUCKET = "storage"


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
async def s3_env(moto_endpoint: str, monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("S3_ENDPOINT_URL", moto_endpoint)
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_PATH_STYLE", "true")
    monkeypatch.setenv("MIN_FREE_SPACE_MB", "1")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "30")
    get_settings.cache_clear()

    bucket = BUCKET
    session = aioboto3.Session()
    config = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=moto_endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
        config=config,
    ) as client:
        await client.create_bucket(Bucket=bucket)

    yield
    get_settings.cache_clear()


def test_create_storage_adapter_returns_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    get_settings.cache_clear()

    adapter = create_storage_adapter(
        disk_id="storage",
        root_prefix="users/u1/files",
    )

    assert isinstance(adapter, S3StorageAdapter)
    assert adapter.disk_id == "storage"
    assert adapter.root_prefix == "users/u1/files"
    assert adapter.bucket == BUCKET


@pytest.mark.asyncio
async def test_build_section_adapter_ensures_prefixes_on_s3(s3_env: None) -> None:
    user_id = uuid4()
    root_prefix = f"users/{user_id}/photos"

    adapter = await build_section_adapter(
        DISK_ID,
        root_prefix,
        ensure_subdirs=("originals", "previews"),
        user_id=user_id,
    )

    assert isinstance(adapter, S3StorageAdapter)
    assert await adapter.exists("originals")
    assert await adapter.exists("previews")


@pytest.mark.asyncio
async def test_private_unlock_creates_and_validates_marker_via_adapter(s3_env: None) -> None:
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

    inner = await build_section_adapter(
        DISK_ID,
        user_private_root_prefix(user_id),
        user_id=user_id,
    )
    assert await inner.exists(MARKER_FILENAME)

    denied = await service.unlock(user_id, "sess-2", "wrong-passphrase")
    assert denied is False

    unlocked_again = await service.unlock(user_id, "sess-3", passphrase)
    assert unlocked_again is True


@pytest.mark.asyncio
async def test_private_reset_deletes_via_adapter(s3_env: None) -> None:
    user_id = uuid4()
    root_prefix = user_private_root_prefix(user_id)

    inner = await build_section_adapter(DISK_ID, root_prefix, user_id=user_id)
    key = b"0" * 32
    encrypted = EncryptedStorageAdapter(inner=inner, key=key)
    await encrypted.write_marker()
    assert await inner.exists(MARKER_FILENAME)

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

    assert not await inner.exists(MARKER_FILENAME)
    file_repo.delete_all_by_user_section.assert_awaited_once()
    quota_repo.reset_private_usage.assert_awaited_once_with(user_id)
