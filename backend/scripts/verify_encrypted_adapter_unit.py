"""Unit tests for EncryptedStorageAdapter crypto helpers."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from app.infrastructure.storage.encrypted_adapter import (
    MARKER_PLAINTEXT,
    EncryptedStorageAdapter,
    decrypt_blob,
    derive_encryption_key,
    encrypt_blob,
)
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter


async def _run_adapter_roundtrip(base_path: Path, key: bytes) -> None:
    inner = PlainStorageAdapter(base_path, disk_id="disk1")
    adapter = EncryptedStorageAdapter(inner=inner, key=key)

    async def data_stream():
        yield b"hello encrypted world"

    payload = b"hello encrypted world"
    checksum = await adapter.write("docs/note.txt", data_stream(), size=len(payload))
    assert len(checksum) == 64

    encrypted_name = adapter.encrypt_name("note.txt")
    assert adapter.decrypt_name(encrypted_name) == "note.txt"
    assert await adapter.exists("docs/note.txt")

    chunks: list[bytes] = []
    async for chunk in adapter.read("docs/note.txt"):
        chunks.append(chunk)
    assert b"".join(chunks) == payload

    nodes = await adapter.list("docs")
    assert len(nodes) == 1
    assert nodes[0].name == "note.txt"
    assert nodes[0].size == len(payload)


def main() -> int:
    user_id = uuid.uuid4()
    key = derive_encryption_key("phase5-test-passphrase", user_id)

    same_key = derive_encryption_key("phase5-test-passphrase", user_id)
    assert key == same_key

    other_key = derive_encryption_key("other-passphrase", user_id)
    assert key != other_key

    marker = encrypt_blob(key, MARKER_PLAINTEXT.encode())
    assert decrypt_blob(key, marker).decode() == MARKER_PLAINTEXT

    test_root = Path("/storage/disk1/users") / str(uuid.uuid4()) / "private"
    test_root.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_run_adapter_roundtrip(test_root, key))
    finally:
        import shutil

        shutil.rmtree(test_root.parent.parent, ignore_errors=True)

    print("All EncryptedStorageAdapter unit checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
