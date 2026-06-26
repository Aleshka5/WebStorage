import base64
import hashlib
import os
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import aiofiles
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger

from app.domain.exceptions import FileNotFoundError
from app.infrastructure.storage.base_adapter import FileNode, StorageAdapter
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter

IV_SIZE = 12
CHUNK_SIZE_FIELD = 4
PLAIN_TMP_PREFIX = ".tmp"
MARKER_FILENAME = ".marker"
MARKER_PLAINTEXT = "HOMECLOUD_MARKER_V1"


def derive_encryption_key(passphrase: str, user_id: UUID) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.bytes,
        iterations=100_000,
    )
    return kdf.derive(passphrase.encode())


def encrypt_blob(key: bytes, plaintext: bytes) -> bytes:
    iv = os.urandom(IV_SIZE)
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    return iv + ciphertext


def decrypt_blob(key: bytes, payload: bytes) -> bytes:
    iv = payload[:IV_SIZE]
    ciphertext = payload[IV_SIZE:]
    return AESGCM(key).decrypt(iv, ciphertext, None)


class EncryptedStorageAdapter(StorageAdapter):
    def __init__(self, inner: PlainStorageAdapter, key: bytes) -> None:
        self._inner = inner
        self._key = key
        logger.info(
            "EncryptedStorageAdapter initialized for disk {} at {}",
            inner.disk_id,
            inner.base_path,
        )

    @property
    def base_path(self) -> Path:
        return self._inner.base_path

    @property
    def disk_id(self) -> str:
        return self._inner.disk_id

    @property
    def disk_relative_prefix(self) -> str:
        return self._inner.disk_relative_prefix

    def encrypt_name(self, name: str) -> str:
        iv = os.urandom(IV_SIZE)
        ciphertext = AESGCM(self._key).encrypt(iv, name.encode(), None)
        encoded = base64.urlsafe_b64encode(iv + ciphertext).decode("ascii")
        return encoded.rstrip("=")

    def decrypt_name(self, enc_name: str) -> str:
        padding = "=" * (-len(enc_name) % 4)
        payload = base64.urlsafe_b64decode(enc_name + padding)
        iv = payload[:IV_SIZE]
        ciphertext = payload[IV_SIZE:]
        plaintext = AESGCM(self._key).decrypt(iv, ciphertext, None)
        return plaintext.decode()

    async def list(self, path: str) -> list[FileNode]:
        encrypted_path = await self._resolve_encrypted_path(path)
        encrypted_nodes = await self._inner.list(encrypted_path)
        nodes: list[FileNode] = []

        for node in encrypted_nodes:
            try:
                decrypted_name = self.decrypt_name(node.name)
            except Exception:
                logger.warning("Skipping entry with undecryptable name in {}", encrypted_path)
                continue

            size = node.size
            if not node.is_dir:
                size = await self._decrypted_file_size(
                    self._join_encrypted_path(encrypted_path, node.name)
                )

            nodes.append(
                FileNode(
                    name=decrypted_name,
                    is_dir=node.is_dir,
                    size=size,
                    modified_at=node.modified_at,
                )
            )

        logger.info("Listed {} decrypted entries in {}", len(nodes), path)
        return nodes

    async def read(self, path: str) -> AsyncIterator[bytes]:
        encrypted_path = await self._resolve_encrypted_path(path)
        target = self._inner._safe_path(self._inner.base_path, encrypted_path)  # noqa: SLF001
        if not target.is_file():
            raise FileNotFoundError(f"File {path!r} not found")

        async with aiofiles.open(target, mode="rb") as file_handle:
            async for chunk in self._decrypt_stream(file_handle):
                yield chunk

    async def write(
        self,
        path: str,
        data: AsyncIterator[bytes],
        size: int,
    ) -> str:
        encrypted_path = self._encrypt_path(path)
        hasher = hashlib.sha256()
        bytes_written = 0

        target = self._inner._safe_path(self._inner.base_path, encrypted_path)  # noqa: SLF001
        target.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(target, mode="wb") as file_handle:
            async for plaintext_chunk in data:
                hasher.update(plaintext_chunk)
                bytes_written += len(plaintext_chunk)
                iv, ciphertext = self._encrypt_chunk(plaintext_chunk)
                await file_handle.write(iv + struct.pack(">I", len(ciphertext)) + ciphertext)

        if bytes_written != size:
            logger.warning(
                "Written byte count {} differs from declared size {} for path {}",
                bytes_written,
                size,
                path,
            )

        checksum = hasher.hexdigest()
        logger.info("Wrote {} encrypted bytes for logical path {}", bytes_written, path)
        return checksum

    async def delete(self, path: str) -> None:
        encrypted_path = await self._resolve_encrypted_path(path)
        await self._inner.delete(encrypted_path)

    async def mkdir(self, path: str) -> None:
        encrypted_path = self._encrypt_path(path)
        await self._inner.mkdir(encrypted_path)

    async def rename(self, old_path: str, new_path: str) -> None:
        encrypted_old = await self._resolve_encrypted_path(old_path)
        encrypted_new = self._encrypt_path(new_path)
        await self._inner.rename(encrypted_old, encrypted_new)

    async def exists(self, path: str) -> bool:
        if self._is_plain_storage_path(path):
            return await self._inner.exists(path)
        try:
            encrypted_path = await self._resolve_encrypted_path(path)
        except FileNotFoundError:
            return False
        return await self._inner.exists(encrypted_path)

    def _encrypt_chunk(self, plaintext: bytes) -> tuple[bytes, bytes]:
        iv = os.urandom(IV_SIZE)
        ciphertext = AESGCM(self._key).encrypt(iv, plaintext, None)
        return iv, ciphertext

    async def _decrypt_stream(self, file_handle) -> AsyncIterator[bytes]:
        while True:
            iv = await file_handle.read(IV_SIZE)
            if not iv:
                break
            if len(iv) < IV_SIZE:
                raise ValueError("Truncated encrypted file: incomplete IV")

            size_bytes = await file_handle.read(CHUNK_SIZE_FIELD)
            if len(size_bytes) < CHUNK_SIZE_FIELD:
                raise ValueError("Truncated encrypted file: incomplete chunk size")

            encrypted_size = struct.unpack(">I", size_bytes)[0]
            ciphertext = await file_handle.read(encrypted_size)
            if len(ciphertext) < encrypted_size:
                raise ValueError("Truncated encrypted file: incomplete chunk payload")

            plaintext = AESGCM(self._key).decrypt(iv, ciphertext, None)
            yield plaintext

    async def _decrypted_file_size(self, encrypted_path: str) -> int:
        target = self._inner._safe_path(self._inner.base_path, encrypted_path)  # noqa: SLF001
        total = 0
        async with aiofiles.open(target, mode="rb") as file_handle:
            async for chunk in self._decrypt_stream(file_handle):
                total += len(chunk)
        return total

    def _encrypt_path(self, logical_path: str) -> str:
        normalized = self._normalize_path(logical_path)
        if not normalized:
            return ""
        if self._is_plain_storage_path(normalized):
            return normalized

        encrypted_segments = [
            segment if segment.startswith(".") else self.encrypt_name(segment)
            for segment in normalized.split("/")
        ]
        return "/".join(encrypted_segments)

    async def _resolve_encrypted_path(self, logical_path: str) -> str:
        normalized = self._normalize_path(logical_path)
        if not normalized:
            return ""
        if self._is_plain_storage_path(normalized):
            return normalized

        segments = normalized.split("/")
        encrypted_parts: list[str] = []

        for segment in segments:
            parent_encrypted = "/".join(encrypted_parts)
            entries = await self._inner.list(parent_encrypted)
            matched_name: str | None = None

            for entry in entries:
                try:
                    decrypted_name = self.decrypt_name(entry.name)
                except Exception:
                    continue
                if decrypted_name == segment:
                    matched_name = entry.name
                    break

            if matched_name is None:
                raise FileNotFoundError(f"Path {logical_path!r} not found")

            encrypted_parts.append(matched_name)

        return "/".join(encrypted_parts)

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.strip().replace("\\", "/").strip("/")

    @staticmethod
    def _is_plain_storage_path(path: str) -> bool:
        normalized = path.strip().replace("\\", "/").strip("/")
        return normalized == PLAIN_TMP_PREFIX or normalized.startswith(f"{PLAIN_TMP_PREFIX}/")

    @staticmethod
    def _join_encrypted_path(parent: str, name: str) -> str:
        if not parent:
            return name
        return f"{parent}/{name}"
