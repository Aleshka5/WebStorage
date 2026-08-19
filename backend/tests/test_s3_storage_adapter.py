"""Unit tests for S3StorageAdapter against moto S3 server."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aioboto3
import pytest
from botocore.config import Config
from moto.server import ThreadedMotoServer

from app.domain.exceptions import FileNotFoundError, PathTraversalError
from app.infrastructure.storage.s3_adapter import S3StorageAdapter
from config import get_settings

DISK_ID = "disk1"
ROOT_PREFIX = "users/11111111-1111-1111-1111-111111111111/files"
BUCKET_PREFIX = "hc-"


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.fixture(scope="module")
def moto_endpoint() -> str:
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    # Prefer loopback; moto may report 0.0.0.0
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    endpoint = f"http://{host}:{port}"
    yield endpoint
    server.stop()


@pytest.fixture
async def s3_adapter(
    moto_endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> S3StorageAdapter:
    get_settings.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT_URL", moto_endpoint)
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET_PREFIX", BUCKET_PREFIX)
    monkeypatch.setenv("S3_PATH_STYLE", "true")
    get_settings.cache_clear()

    bucket = f"{BUCKET_PREFIX}{DISK_ID}"
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

    adapter = S3StorageAdapter(disk_id=DISK_ID, root_prefix=ROOT_PREFIX)
    yield adapter
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_write_read_roundtrip(s3_adapter: S3StorageAdapter) -> None:
    payload = b"hello-s3-storage"
    checksum = await s3_adapter.write("docs/readme.txt", _chunks(payload), len(payload))

    assert len(checksum) == 64
    assert await s3_adapter.exists("docs/readme.txt")

    chunks: list[bytes] = []
    async for chunk in s3_adapter.read("docs/readme.txt"):
        chunks.append(chunk)
    assert b"".join(chunks) == payload


@pytest.mark.asyncio
async def test_list_mkdir_hides_tmp_and_dotfiles(s3_adapter: S3StorageAdapter) -> None:
    await s3_adapter.mkdir("folder")
    await s3_adapter.write("folder/visible.txt", _chunks(b"ok"), 2)
    await s3_adapter.write("folder/.secret", _chunks(b"nope"), 4)
    await s3_adapter.write(".tmp/upload-id", _chunks(b"tmp"), 3)

    entries = await s3_adapter.list("")
    names = {node.name for node in entries}
    assert "folder" in names
    assert ".tmp" not in names
    assert ".secret" not in names

    folder_entries = await s3_adapter.list("folder")
    folder_names = {node.name for node in folder_entries}
    assert folder_names == {"visible.txt"}


@pytest.mark.asyncio
async def test_delete_file_and_directory(s3_adapter: S3StorageAdapter) -> None:
    await s3_adapter.write("a.txt", _chunks(b"a"), 1)
    await s3_adapter.mkdir("dir")
    await s3_adapter.write("dir/b.txt", _chunks(b"b"), 1)

    await s3_adapter.delete("a.txt")
    assert not await s3_adapter.exists("a.txt")

    await s3_adapter.delete("dir")
    assert not await s3_adapter.exists("dir")
    assert not await s3_adapter.exists("dir/b.txt")


@pytest.mark.asyncio
async def test_rename_file_and_directory(s3_adapter: S3StorageAdapter) -> None:
    await s3_adapter.write("old.txt", _chunks(b"data"), 4)
    await s3_adapter.rename("old.txt", "new.txt")
    assert not await s3_adapter.exists("old.txt")
    assert await s3_adapter.exists("new.txt")

    await s3_adapter.mkdir("src")
    await s3_adapter.write("src/nested.txt", _chunks(b"n"), 1)
    await s3_adapter.rename("src", "dst")
    assert not await s3_adapter.exists("src/nested.txt")
    assert await s3_adapter.exists("dst/nested.txt")


@pytest.mark.asyncio
async def test_exists_and_missing_paths(s3_adapter: S3StorageAdapter) -> None:
    assert not await s3_adapter.exists("missing.txt")
    await s3_adapter.mkdir("only-dir")
    assert await s3_adapter.exists("only-dir")

    with pytest.raises(FileNotFoundError):
        async for _ in s3_adapter.read("missing.txt"):
            pass

    with pytest.raises(FileNotFoundError):
        await s3_adapter.list("no-such-dir")


@pytest.mark.asyncio
async def test_path_traversal_rejected(s3_adapter: S3StorageAdapter) -> None:
    with pytest.raises(PathTraversalError):
        await s3_adapter.exists("../escape.txt")

    with pytest.raises(PathTraversalError):
        await s3_adapter.write("../../etc/passwd", _chunks(b"x"), 1)

    with pytest.raises(PathTraversalError):
        await s3_adapter.list("foo/../../bar")


@pytest.mark.asyncio
async def test_root_prefix_and_bucket_mapping(s3_adapter: S3StorageAdapter) -> None:
    assert s3_adapter.root_prefix == ROOT_PREFIX
    assert s3_adapter.disk_relative_prefix == ROOT_PREFIX
    assert s3_adapter.disk_id == DISK_ID
    assert s3_adapter.bucket == f"{BUCKET_PREFIX}{DISK_ID}"
    assert s3_adapter.to_disk_relative_path("a/b.txt") == f"{ROOT_PREFIX}/a/b.txt"
