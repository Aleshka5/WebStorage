"""Shared DI helpers for FS vs S3 storage adapter selection."""

from pathlib import Path
from uuid import UUID

from loguru import logger

from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import StorageAdapter
from app.infrastructure.storage.s3_adapter import create_storage_adapter
from config import get_settings


def user_files_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/files"


def user_photos_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/photos"


def user_private_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/private"


def shared_root_prefix() -> str:
    return "shared"


def resolve_fs_base_path(disk_id: str, root_prefix: str) -> Path:
    """Absolute FS path under the disk mount for ``root_prefix`` (fs backend only)."""
    disk_router = DiskRouter(get_settings())
    disk = disk_router.get_disk_by_id(disk_id)
    if not root_prefix:
        return disk.mount_path
    return disk.mount_path.joinpath(*root_prefix.split("/"))


async def build_section_adapter(
    disk_id: str,
    root_prefix: str,
    *,
    ensure_subdirs: tuple[str, ...] = (),
    user_id: UUID | None = None,
) -> StorageAdapter:
    """Create a section-scoped adapter via ``create_storage_adapter``.

    - ``fs``: mkdir parents under the disk mount, then PlainStorageAdapter.
    - ``s3``: no Path.mkdir; ensure logical prefixes via adapter.mkdir.
    """
    settings = get_settings()
    backend = settings.storage.backend
    base_path: Path | None = None

    if backend == "fs":
        base_path = resolve_fs_base_path(disk_id, root_prefix)
        base_path.mkdir(parents=True, exist_ok=True)
    else:
        base_path = None

    adapter = create_storage_adapter(
        disk_id=disk_id,
        root_prefix=root_prefix,
        base_path=base_path,
    )

    if backend == "s3":
        # Root marker is a no-op on S3 today; call kept for explicit prefix ensure.
        await adapter.mkdir("")

    for subdir in ensure_subdirs:
        await adapter.mkdir(subdir)

    if user_id is not None:
        logger.info(
            "Storage adapter ready: backend={}, user_id={}, disk_id={}, root_prefix={}",
            backend,
            user_id,
            disk_id,
            root_prefix,
        )
    else:
        logger.info(
            "Storage adapter ready: backend={}, disk_id={}, root_prefix={}",
            backend,
            disk_id,
            root_prefix,
        )
    return adapter
