"""S3/MinIO blob backend implementing StorageAdapter.

Directory representation
------------------------
Directories are modeled as:
1. Key prefixes used with ListObjectsV2 ``Delimiter='/'``.
2. Optional zero-byte marker objects whose keys end with ``/``
   (created by ``mkdir`` so empty folders are visible).

Object keys are isomorphic to FS relative paths under the disk root:
``{root_prefix}/{normalized_section_path}`` inside bucket
``{S3_BUCKET_PREFIX}{disk_id}``.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from loguru import logger

from app.domain.exceptions import FileNotFoundError, PathTraversalError
from app.infrastructure.storage.base_adapter import (
    HIDDEN_DIR_NAMES,
    READ_CHUNK_SIZE,
    FileNode,
    StorageAdapter,
)
from config import get_settings

DIR_MARKER_SUFFIX = "/"
TMP_DIR_NAME = ".tmp"
_SPOOL_MAX_SIZE = 8 * 1024 * 1024


class S3StorageAdapter(StorageAdapter):
    """Async S3-compatible StorageAdapter (MinIO / generic S3 API)."""

    def __init__(
        self,
        disk_id: str,
        root_prefix: str,
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        settings = get_settings()
        if settings.storage.backend != "s3":
            logger.warning(
                "S3StorageAdapter created while STORAGE_BACKEND={!r} (expected 's3')",
                settings.storage.backend,
            )

        self._disk_id = disk_id
        self._root_prefix = root_prefix.strip().strip("/")
        self._bucket = f"{settings.s3.bucket_prefix}{disk_id}"
        self._endpoint_url = settings.s3.endpoint_url or None
        self._access_key = settings.s3.access_key
        self._secret_key = settings.s3.secret_key
        self._region = settings.s3.region
        self._path_style = settings.s3.path_style
        self._client_factory = client_factory
        self._session = aioboto3.Session()

        logger.info(
            "S3StorageAdapter initialized: disk_id={}, bucket={}, root_prefix={}, path_style={}",
            self._disk_id,
            self._bucket,
            self._root_prefix,
            self._path_style,
        )

    @property
    def root_prefix(self) -> str:
        return self._root_prefix

    @property
    def disk_id(self) -> str:
        return self._disk_id

    @property
    def disk_relative_prefix(self) -> str:
        return self._root_prefix

    @property
    def bucket(self) -> str:
        return self._bucket

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        if self._client_factory is not None:
            factory_cm = self._client_factory()
            async with factory_cm as client:
                yield client
            return

        addressing = "path" if self._path_style else "virtual"
        config = Config(s3={"addressing_style": addressing})
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key or None,
            aws_secret_access_key=self._secret_key or None,
            region_name=self._region,
            config=config,
        ) as client:
            yield client

    def _object_key(self, path: str, *, as_dir: bool = False) -> str:
        logical = self._safe_logical_key(path)
        if logical:
            key = f"{self._root_prefix}/{logical}" if self._root_prefix else logical
        else:
            key = self._root_prefix
        if as_dir and key and not key.endswith(DIR_MARKER_SUFFIX):
            key = f"{key}{DIR_MARKER_SUFFIX}"
        return key

    def _list_prefix(self, path: str) -> str:
        key = self._object_key(path, as_dir=True)
        if not key:
            return ""
        return key if key.endswith(DIR_MARKER_SUFFIX) else f"{key}{DIR_MARKER_SUFFIX}"

    @staticmethod
    def _is_hidden_name(name: str) -> bool:
        return name in HIDDEN_DIR_NAMES or name.startswith(".")

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        code = exc.response.get("Error", {}).get("Code", "")
        return code in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}

    async def list(self, path: str) -> list[FileNode]:
        prefix = self._list_prefix(path)
        nodes: list[FileNode] = []

        try:
            async with self._client() as client:
                if path.strip().strip("/"):
                    if not await self._exists_prefix_or_marker(client, path):
                        raise FileNotFoundError(f"Directory {path!r} not found")

                paginator = client.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=self._bucket,
                    Prefix=prefix,
                    Delimiter=DIR_MARKER_SUFFIX,
                ):
                    for common in page.get("CommonPrefixes", []) or []:
                        raw_prefix = common.get("Prefix", "")
                        name = raw_prefix[len(prefix) :].rstrip("/")
                        if not name or self._is_hidden_name(name):
                            continue
                        nodes.append(
                            FileNode(
                                name=name,
                                is_dir=True,
                                size=0,
                                modified_at=datetime.now(tz=UTC),
                            )
                        )

                    for obj in page.get("Contents", []) or []:
                        key = obj["Key"]
                        if key == prefix or key.endswith(DIR_MARKER_SUFFIX):
                            continue
                        name = key[len(prefix) :]
                        if DIR_MARKER_SUFFIX in name:
                            continue
                        if not name or self._is_hidden_name(name):
                            continue
                        last_modified = obj.get("LastModified") or datetime.now(tz=UTC)
                        if last_modified.tzinfo is None:
                            last_modified = last_modified.replace(tzinfo=UTC)
                        nodes.append(
                            FileNode(
                                name=name,
                                is_dir=False,
                                size=int(obj.get("Size") or 0),
                                modified_at=last_modified,
                            )
                        )
        except FileNotFoundError:
            raise
        except ClientError:
            logger.exception(
                "Failed to list S3 prefix bucket={} prefix={}",
                self._bucket,
                prefix,
            )
            raise
        except PathTraversalError:
            raise
        except Exception:
            logger.exception(
                "Unexpected error listing S3 prefix bucket={} prefix={}",
                self._bucket,
                prefix,
            )
            raise

        nodes.sort(key=lambda node: (not node.is_dir, node.name.lower()))
        logger.info(
            "Listed {} entries in {} (bucket={}, disk_id={})",
            len(nodes),
            path,
            self._bucket,
            self._disk_id,
        )
        return nodes

    async def read(self, path: str) -> AsyncIterator[bytes]:
        key = self._object_key(path)
        if not key or key.endswith(DIR_MARKER_SUFFIX):
            raise FileNotFoundError(f"File {path!r} not found")

        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if self._is_not_found(exc):
                    raise FileNotFoundError(f"File {path!r} not found") from exc
                logger.exception(
                    "Failed to read S3 object bucket={} key={}",
                    self._bucket,
                    key,
                )
                raise

            body = response["Body"]
            try:
                while True:
                    chunk = await body.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                close = getattr(body, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result

        logger.info("Read S3 object bucket={} key={}", self._bucket, key)

    async def write(
        self,
        path: str,
        data: AsyncIterator[bytes],
        size: int,
    ) -> str:
        final_key = self._object_key(path)
        if not final_key or final_key.endswith(DIR_MARKER_SUFFIX):
            raise PathTraversalError(f"Invalid write path {path!r}")

        tmp_key = self._object_key(f"{TMP_DIR_NAME}/{uuid4().hex}")
        hasher = hashlib.sha256()
        bytes_written = 0

        try:
            with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_SIZE) as spool:
                async for chunk in data:
                    hasher.update(chunk)
                    bytes_written += len(chunk)
                    spool.write(chunk)
                spool.seek(0)

                async with self._client() as client:
                    await client.put_object(
                        Bucket=self._bucket,
                        Key=tmp_key,
                        Body=spool,
                    )
                    await client.copy_object(
                        Bucket=self._bucket,
                        CopySource={"Bucket": self._bucket, "Key": tmp_key},
                        Key=final_key,
                    )
                    await client.delete_object(Bucket=self._bucket, Key=tmp_key)
        except PathTraversalError:
            raise
        except ClientError:
            logger.exception(
                "Failed to write S3 object bucket={} key={}",
                self._bucket,
                final_key,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error writing S3 object bucket={} key={}",
                self._bucket,
                final_key,
            )
            raise

        if bytes_written != size:
            logger.warning(
                "Written byte count {} differs from declared size {} for key {}",
                bytes_written,
                size,
                final_key,
            )

        checksum = hasher.hexdigest()
        logger.info(
            "Wrote {} bytes to S3 key {} (bucket={}, checksum={})",
            bytes_written,
            final_key,
            self._bucket,
            checksum,
        )
        return checksum

    async def delete(self, path: str) -> None:
        logical = self._safe_logical_key(path)
        if not logical and not self._root_prefix:
            raise PathTraversalError("Refusing to delete empty S3 root")

        file_key = self._object_key(path)
        dir_prefix = self._list_prefix(path)

        try:
            async with self._client() as client:
                keys: list[str] = []
                if await self._head_exists(client, file_key):
                    keys.append(file_key)

                paginator = client.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self._bucket, Prefix=dir_prefix):
                    for obj in page.get("Contents", []) or []:
                        keys.append(obj["Key"])

                unique_keys = list(dict.fromkeys(keys))
                if not unique_keys:
                    raise FileNotFoundError(f"Path {path!r} not found")

                for offset in range(0, len(unique_keys), 1000):
                    batch = [{"Key": key} for key in unique_keys[offset : offset + 1000]]
                    await client.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": batch, "Quiet": True},
                    )
        except FileNotFoundError:
            raise
        except PathTraversalError:
            raise
        except ClientError:
            logger.exception(
                "Failed to delete S3 path bucket={} path={}",
                self._bucket,
                path,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error deleting S3 path bucket={} path={}",
                self._bucket,
                path,
            )
            raise

        logger.info("Deleted S3 path {} (bucket={})", path, self._bucket)

    async def mkdir(self, path: str) -> None:
        logical = self._safe_logical_key(path)
        if not logical:
            logger.info("mkdir ignored for empty path (bucket={})", self._bucket)
            return

        try:
            async with self._client() as client:
                parts = logical.split("/")
                for index in range(len(parts)):
                    partial = "/".join(parts[: index + 1])
                    marker_key = self._object_key(partial, as_dir=True)
                    await client.put_object(
                        Bucket=self._bucket,
                        Key=marker_key,
                        Body=b"",
                    )
        except PathTraversalError:
            raise
        except ClientError:
            logger.exception(
                "Failed to create S3 directory marker bucket={} path={}",
                self._bucket,
                path,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error creating S3 directory bucket={} path={}",
                self._bucket,
                path,
            )
            raise

        logger.info("Created S3 directory {} (bucket={})", path, self._bucket)

    async def rename(self, old_path: str, new_path: str) -> None:
        old_key = self._object_key(old_path)
        new_key = self._object_key(new_path)
        old_prefix = self._list_prefix(old_path)
        new_prefix = self._list_prefix(new_path)

        try:
            async with self._client() as client:
                # Single object rename.
                if await self._head_exists(client, old_key):
                    await client.copy_object(
                        Bucket=self._bucket,
                        CopySource={"Bucket": self._bucket, "Key": old_key},
                        Key=new_key,
                    )
                    await client.delete_object(Bucket=self._bucket, Key=old_key)
                    logger.info(
                        "Renamed S3 object {} -> {} (bucket={})",
                        old_key,
                        new_key,
                        self._bucket,
                    )
                    return

                # Directory / prefix rename.
                paginator = client.get_paginator("list_objects_v2")
                source_keys: list[str] = []
                async for page in paginator.paginate(Bucket=self._bucket, Prefix=old_prefix):
                    for obj in page.get("Contents", []) or []:
                        source_keys.append(obj["Key"])

                if not source_keys:
                    raise FileNotFoundError(f"Path {old_path!r} not found")

                for source_key in source_keys:
                    suffix = source_key[len(old_prefix) :]
                    dest_key = f"{new_prefix}{suffix}"
                    await client.copy_object(
                        Bucket=self._bucket,
                        CopySource={"Bucket": self._bucket, "Key": source_key},
                        Key=dest_key,
                    )

                for offset in range(0, len(source_keys), 1000):
                    batch = [{"Key": key} for key in source_keys[offset : offset + 1000]]
                    await client.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": batch, "Quiet": True},
                    )
        except FileNotFoundError:
            raise
        except PathTraversalError:
            raise
        except ClientError:
            logger.exception(
                "Failed to rename S3 path {} -> {} (bucket={})",
                old_path,
                new_path,
                self._bucket,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error renaming S3 path {} -> {} (bucket={})",
                old_path,
                new_path,
                self._bucket,
            )
            raise

        logger.info(
            "Renamed S3 prefix {} -> {} (bucket={})",
            old_path,
            new_path,
            self._bucket,
        )

    async def exists(self, path: str) -> bool:
        file_key = self._object_key(path)
        try:
            async with self._client() as client:
                if await self._head_exists(client, file_key):
                    return True
                return await self._exists_prefix_or_marker(client, path)
        except PathTraversalError:
            raise
        except ClientError:
            logger.exception(
                "Failed exists() check for S3 path {} (bucket={})",
                path,
                self._bucket,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected exists() error for S3 path {} (bucket={})",
                path,
                self._bucket,
            )
            raise

    async def get_size(self, path: str) -> int:
        key = self._object_key(path)
        if not key or key.endswith(DIR_MARKER_SUFFIX):
            raise FileNotFoundError(f"File {path!r} not found")

        try:
            async with self._client() as client:
                response = await client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if self._is_not_found(exc):
                raise FileNotFoundError(f"File {path!r} not found") from exc
            logger.exception(
                "Failed get_size for S3 object bucket={} key={}",
                self._bucket,
                key,
            )
            raise

        size = int(response.get("ContentLength") or 0)
        logger.info("S3 object size bucket={} key={} size={}", self._bucket, key, size)
        return size

    async def list_stale_tmp_entry_paths(self, cutoff_ts: float) -> list[str]:
        """Find stale children of any ``.tmp/`` prefix in this adapter's key space."""
        list_prefix = f"{self._root_prefix}/" if self._root_prefix else ""
        entry_mtime: dict[str, float] = {}

        try:
            async with self._client() as client:
                paginator = client.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self._bucket, Prefix=list_prefix):
                    for obj in page.get("Contents", []) or []:
                        key = obj["Key"]
                        entry_path = self._tmp_entry_path_from_key(key)
                        if entry_path is None:
                            continue
                        last_modified = obj.get("LastModified") or datetime.now(tz=UTC)
                        if last_modified.tzinfo is None:
                            last_modified = last_modified.replace(tzinfo=UTC)
                        mtime = last_modified.timestamp()
                        previous = entry_mtime.get(entry_path)
                        if previous is None or mtime > previous:
                            entry_mtime[entry_path] = mtime
        except ClientError:
            logger.exception(
                "Failed listing S3 tmp entries bucket={} prefix={}",
                self._bucket,
                list_prefix,
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected error listing S3 tmp entries bucket={} prefix={}",
                self._bucket,
                list_prefix,
            )
            raise

        stale = [
            path for path, mtime in entry_mtime.items() if mtime < cutoff_ts
        ]
        logger.info(
            "Found {} stale tmp entries in S3 bucket={} (disk_id={})",
            len(stale),
            self._bucket,
            self._disk_id,
        )
        return stale

    def _tmp_entry_path_from_key(self, key: str) -> str | None:
        relative = key
        if self._root_prefix:
            prefix = f"{self._root_prefix}/"
            if not key.startswith(prefix) and key != self._root_prefix:
                return None
            relative = key[len(prefix) :] if key.startswith(prefix) else ""

        if not relative or relative.endswith(DIR_MARKER_SUFFIX):
            # Directory markers under .tmp are not standalone stale entries.
            parts_for_marker = relative.rstrip(DIR_MARKER_SUFFIX).split("/")
            if len(parts_for_marker) >= 2 and parts_for_marker[-2] == TMP_DIR_NAME:
                return "/".join(parts_for_marker)
            return None

        parts = relative.split("/")
        try:
            tmp_index = parts.index(TMP_DIR_NAME)
        except ValueError:
            return None

        if tmp_index + 1 >= len(parts):
            return None

        return "/".join(parts[: tmp_index + 2])

    async def _head_exists(self, client: Any, key: str) -> bool:
        if not key:
            return False
        try:
            await client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if self._is_not_found(exc):
                return False
            raise

    async def _exists_prefix_or_marker(self, client: Any, path: str) -> bool:
        marker_key = self._object_key(path, as_dir=True)
        if await self._head_exists(client, marker_key):
            return True

        prefix = self._list_prefix(path)
        response = await client.list_objects_v2(
            Bucket=self._bucket,
            Prefix=prefix,
            MaxKeys=1,
        )
        return bool(response.get("KeyCount") or response.get("Contents") or response.get("CommonPrefixes"))


def create_storage_adapter(
    disk_id: str,
    root_prefix: str,
    *,
    base_path: Path | None = None,
) -> StorageAdapter:
    """Optional factory: select FS or S3 adapter from ``get_settings().storage.backend``.

    Full DI wiring remains US-S3-05; this helper is for callers that opt in early.
    """
    from app.infrastructure.storage.plain_adapter import PlainStorageAdapter

    settings = get_settings()
    if settings.storage.backend == "s3":
        return S3StorageAdapter(disk_id=disk_id, root_prefix=root_prefix)

    if base_path is None:
        raise ValueError("base_path is required when STORAGE_BACKEND=fs")
    return PlainStorageAdapter(base_path, disk_id=disk_id)


def build_disk_root_adapter(disk_id: str) -> StorageAdapter:
    """Adapter scoped to a whole disk (empty root prefix / mount root).

    Used by archive, backup, and maintenance for disk-relative keys such as
    ``users/...``, ``shared/...``, and ``_meta/backups/...``.
    """
    from app.infrastructure.disk_router import DiskRouter

    settings = get_settings()
    base_path: Path | None = None
    if settings.storage.backend == "fs":
        disk = DiskRouter(settings).get_disk_by_id(disk_id)
        base_path = disk.mount_path

    adapter = create_storage_adapter(
        disk_id=disk_id,
        root_prefix="",
        base_path=base_path,
    )
    logger.info(
        "Disk-root storage adapter ready: backend={}, disk_id={}",
        settings.storage.backend,
        disk_id,
    )
    return adapter

