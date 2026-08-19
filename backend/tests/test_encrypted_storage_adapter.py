"""Unit tests for EncryptedStorageAdapter decorating any StorageAdapter."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.exceptions import FileNotFoundError
from app.infrastructure.storage.base_adapter import FileNode, StorageAdapter
from app.infrastructure.storage.encrypted_adapter import (
    MARKER_FILENAME,
    MARKER_PLAINTEXT,
    EncryptedStorageAdapter,
    decrypt_blob,
    derive_encryption_key,
    encrypt_blob,
)
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


class InMemoryStorageAdapter(StorageAdapter):
    """Minimal StorageAdapter backed by dicts (files + directories)."""

    def __init__(self, disk_id: str = "disk1", root_prefix: str = "users/u/private") -> None:
        self._disk_id = disk_id
        self._root_prefix = root_prefix
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = {""}

    @property
    def root_prefix(self) -> str:
        return self._root_prefix

    @property
    def disk_id(self) -> str:
        return self._disk_id

    @property
    def disk_relative_prefix(self) -> str:
        return self._root_prefix

    def _norm(self, path: str) -> str:
        return path.strip().replace("\\", "/").strip("/")

    def _parent(self, path: str) -> str:
        normalized = self._norm(path)
        if "/" not in normalized:
            return ""
        return normalized.rsplit("/", 1)[0]

    async def list(self, path: str) -> list[FileNode]:
        normalized = self._norm(path)
        if normalized not in self._dirs:
            raise FileNotFoundError(f"Directory {path!r} not found")

        prefix = f"{normalized}/" if normalized else ""
        nodes: list[FileNode] = []
        now = datetime.now(tz=UTC)

        for directory in sorted(self._dirs):
            if directory == normalized or not directory.startswith(prefix):
                continue
            rest = directory[len(prefix) :]
            if "/" in rest or not rest:
                continue
            nodes.append(FileNode(name=rest, is_dir=True, size=0, modified_at=now))

        for file_path, payload in sorted(self._files.items()):
            if normalized:
                if not file_path.startswith(prefix):
                    continue
                rest = file_path[len(prefix) :]
            else:
                rest = file_path
            if "/" in rest or not rest:
                continue
            nodes.append(
                FileNode(name=rest, is_dir=False, size=len(payload), modified_at=now)
            )

        return nodes

    async def read(self, path: str) -> AsyncIterator[bytes]:
        normalized = self._norm(path)
        if normalized not in self._files:
            raise FileNotFoundError(f"File {path!r} not found")
        yield self._files[normalized]

    async def write(self, path: str, data: AsyncIterator[bytes], size: int) -> str:
        normalized = self._norm(path)
        parent = self._parent(normalized)
        if parent not in self._dirs:
            raise FileNotFoundError(f"Parent directory for {path!r} not found")

        payload = bytearray()
        hasher = hashlib.sha256()
        async for chunk in data:
            payload.extend(chunk)
            hasher.update(chunk)

        self._files[normalized] = bytes(payload)
        return hasher.hexdigest()

    async def delete(self, path: str) -> None:
        normalized = self._norm(path)
        if normalized in self._files:
            del self._files[normalized]
            return
        if normalized in self._dirs and normalized != "":
            prefix = f"{normalized}/"
            self._dirs = {
                item for item in self._dirs if item != normalized and not item.startswith(prefix)
            }
            self._files = {
                key: value for key, value in self._files.items() if not key.startswith(prefix)
            }
            return
        raise FileNotFoundError(f"Path {path!r} not found")

    async def mkdir(self, path: str) -> None:
        normalized = self._norm(path)
        if not normalized:
            return
        parts = normalized.split("/")
        for index in range(len(parts)):
            self._dirs.add("/".join(parts[: index + 1]))

    async def rename(self, old_path: str, new_path: str) -> None:
        old = self._norm(old_path)
        new = self._norm(new_path)
        new_parent = self._parent(new)
        if new_parent not in self._dirs:
            await self.mkdir(new_parent)

        if old in self._files:
            self._files[new] = self._files.pop(old)
            return

        if old in self._dirs:
            prefix = f"{old}/"
            renamed_dirs = {new}
            for directory in list(self._dirs):
                if directory.startswith(prefix):
                    renamed_dirs.add(new + directory[len(old) :])
            self._dirs.discard(old)
            self._dirs -= {d for d in self._dirs if d.startswith(prefix)}
            self._dirs |= renamed_dirs

            updates = {
                new + key[len(old) :]: value
                for key, value in self._files.items()
                if key == old or key.startswith(prefix)
            }
            self._files = {
                key: value
                for key, value in self._files.items()
                if key != old and not key.startswith(prefix)
            }
            self._files.update(updates)
            return

        raise FileNotFoundError(f"Path {old_path!r} not found")

    async def exists(self, path: str) -> bool:
        normalized = self._norm(path)
        return normalized in self._files or normalized in self._dirs

    async def get_size(self, path: str) -> int:
        normalized = self._norm(path)
        if normalized not in self._files:
            raise FileNotFoundError(f"File {path!r} not found")
        return len(self._files[normalized])

    async def list_stale_tmp_entry_paths(self, cutoff_ts: float) -> list[str]:
        del cutoff_ts
        stale: list[str] = []
        for file_path in self._files:
            parts = file_path.split("/")
            if ".tmp" not in parts:
                continue
            tmp_index = parts.index(".tmp")
            if tmp_index + 1 >= len(parts):
                continue
            stale.append("/".join(parts[: tmp_index + 2]))
        return sorted(set(stale))


@pytest.fixture
def key() -> bytes:
    return derive_encryption_key("test-passphrase", uuid4())


@pytest.fixture
def adapter(key: bytes) -> EncryptedStorageAdapter:
    return EncryptedStorageAdapter(inner=InMemoryStorageAdapter(), key=key)


@pytest.mark.asyncio
async def test_roundtrip_write_read_list_delete(adapter: EncryptedStorageAdapter) -> None:
    await adapter.mkdir("docs")
    payload = b"hello encrypted world"
    checksum = await adapter.write("docs/note.txt", _chunks(payload), len(payload))

    assert len(checksum) == 64
    assert await adapter.exists("docs/note.txt")

    chunks: list[bytes] = []
    async for chunk in adapter.read("docs/note.txt"):
        chunks.append(chunk)
    assert b"".join(chunks) == payload

    nodes = await adapter.list("docs")
    assert len(nodes) == 1
    assert nodes[0].name == "note.txt"
    assert nodes[0].size == len(payload)

    await adapter.delete("docs/note.txt")
    assert not await adapter.exists("docs/note.txt")


@pytest.mark.asyncio
async def test_encrypt_name_roundtrip(adapter: EncryptedStorageAdapter) -> None:
    encrypted = adapter.encrypt_name("report.pdf")
    assert adapter.decrypt_name(encrypted) == "report.pdf"
    assert "/" not in encrypted


@pytest.mark.asyncio
async def test_nested_mkdir_and_rename(adapter: EncryptedStorageAdapter) -> None:
    await adapter.mkdir("shared")
    await adapter.mkdir("parent")
    await adapter.mkdir("shared/inner")
    await adapter.write("shared/inner/a.txt", _chunks(b"data"), 4)

    await adapter.rename("shared/inner/a.txt", "parent/b.txt")
    assert not await adapter.exists("shared/inner/a.txt")
    assert await adapter.exists("parent/b.txt")

    chunks: list[bytes] = []
    async for chunk in adapter.read("parent/b.txt"):
        chunks.append(chunk)
    assert b"".join(chunks) == b"data"


@pytest.mark.asyncio
async def test_marker_create_and_validate(adapter: EncryptedStorageAdapter, key: bytes) -> None:
    assert not await adapter.marker_exists()
    assert not await adapter.validate_marker()

    await adapter.write_marker()
    assert await adapter.marker_exists()
    assert await adapter.validate_marker()

    # Marker is stored as encrypt_blob payload on the inner adapter (plain name).
    inner = adapter._inner  # noqa: SLF001
    assert await inner.exists(MARKER_FILENAME)
    raw = bytearray()
    async for chunk in inner.read(MARKER_FILENAME):
        raw.extend(chunk)
    assert decrypt_blob(key, bytes(raw)).decode() == MARKER_PLAINTEXT


@pytest.mark.asyncio
async def test_marker_wrong_key_fails(key: bytes) -> None:
    inner = InMemoryStorageAdapter()
    good = EncryptedStorageAdapter(inner=inner, key=key)
    await good.write_marker()

    other_key = derive_encryption_key("other-passphrase", uuid4())
    bad = EncryptedStorageAdapter(inner=inner, key=other_key)
    assert await bad.marker_exists()
    assert not await bad.validate_marker()


@pytest.mark.asyncio
async def test_marker_plaintext_mismatch(key: bytes) -> None:
    inner = InMemoryStorageAdapter()
    adapter = EncryptedStorageAdapter(inner=inner, key=key)

    async def wrong_marker() -> AsyncIterator[bytes]:
        yield encrypt_blob(key, b"NOT_THE_MARKER")

    await inner.write(MARKER_FILENAME, wrong_marker(), 64)
    assert not await adapter.validate_marker()


@pytest.mark.asyncio
async def test_base_path_gated_for_non_fs_inner(adapter: EncryptedStorageAdapter) -> None:
    with pytest.raises(AttributeError, match="filesystem-backed"):
        _ = adapter.base_path


@pytest.mark.asyncio
async def test_base_path_available_with_plain_inner(tmp_path: Path, key: bytes) -> None:
    disk_root = tmp_path / "disk1" / "users" / "u" / "private"
    disk_root.mkdir(parents=True)
    inner = PlainStorageAdapter(disk_root, disk_id="disk1")
    adapter = EncryptedStorageAdapter(inner=inner, key=key)
    assert adapter.base_path == disk_root.resolve()


@pytest.mark.asyncio
async def test_plain_tmp_path_not_name_encrypted(adapter: EncryptedStorageAdapter) -> None:
    await adapter.mkdir(".tmp")
    payload = b"pending-upload"
    await adapter.write(".tmp/upload-1", _chunks(payload), len(payload))

    assert await adapter.exists(".tmp/upload-1")
    assert await adapter._inner.exists(".tmp/upload-1")  # noqa: SLF001

    chunks: list[bytes] = []
    async for chunk in adapter.read(".tmp/upload-1"):
        chunks.append(chunk)
    assert b"".join(chunks) == payload


@pytest.mark.asyncio
async def test_delegates_root_prefix_and_disk_relative(adapter: EncryptedStorageAdapter) -> None:
    assert adapter.root_prefix == "users/u/private"
    assert adapter.disk_id == "disk1"
    assert adapter.disk_relative_prefix == "users/u/private"
    assert adapter.to_disk_relative_path("docs/a.txt") == "users/u/private/docs/a.txt"


@pytest.mark.asyncio
async def test_roundtrip_with_plain_adapter(tmp_path: Path, key: bytes) -> None:
    base = tmp_path / "disk1" / "users" / str(uuid4()) / "private"
    base.mkdir(parents=True)
    inner = PlainStorageAdapter(base, disk_id="disk1")
    adapter = EncryptedStorageAdapter(inner=inner, key=key)

    await adapter.mkdir("docs")
    payload = b"fs-backed encrypted"
    await adapter.write("docs/file.bin", _chunks(payload), len(payload))

    # Physical tree uses encrypted names, not logical ones.
    assert not (base / "docs").exists()
    listed = list(base.iterdir())
    assert listed  # encrypted dir name present

    chunks: list[bytes] = []
    async for chunk in adapter.read("docs/file.bin"):
        chunks.append(chunk)
    assert b"".join(chunks) == payload

    await adapter.write_marker()
    assert (base / MARKER_FILENAME).is_file()
    assert await adapter.validate_marker()


@pytest.mark.asyncio
async def test_encrypt_blob_helpers(key: bytes) -> None:
    marker = encrypt_blob(key, MARKER_PLAINTEXT.encode())
    assert decrypt_blob(key, marker).decode() == MARKER_PLAINTEXT

    with pytest.raises(Exception):
        decrypt_blob(os.urandom(32), marker)


@pytest.mark.asyncio
async def test_missing_file_read_raises(adapter: EncryptedStorageAdapter) -> None:
    with pytest.raises(FileNotFoundError):
        async for _ in adapter.read("missing.txt"):
            pass


@pytest.mark.asyncio
async def test_read_encrypted_blob_from_path(
    tmp_path: Path,
    adapter: EncryptedStorageAdapter,
) -> None:
    # Build a framed encrypted blob the same way write() does.
    await adapter.mkdir("x")
    payload = b"archive-temp-content"
    await adapter.write("x/blob.bin", _chunks(payload), len(payload))
    # Resolve the stored encrypted key (encrypt_path would mint a new leaf IV).
    encrypted_path = await adapter._resolve_encrypted_path("x/blob.bin")  # noqa: SLF001
    framed = bytearray()
    async for chunk in adapter._inner.read(encrypted_path):  # noqa: SLF001
        framed.extend(chunk)
    blob_file = tmp_path / "decompressed"
    blob_file.write_bytes(framed)

    chunks: list[bytes] = []
    async for chunk in adapter.read_encrypted_blob(blob_file):
        chunks.append(chunk)
    assert b"".join(chunks) == payload
