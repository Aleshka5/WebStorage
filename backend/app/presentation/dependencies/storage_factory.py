"""Shared DI helpers for S3 storage adapters."""

from uuid import UUID

from loguru import logger

from app.infrastructure.storage.base_adapter import StorageAdapter
from app.infrastructure.storage.s3_adapter import create_storage_adapter


def user_files_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/files"


def user_photos_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/photos"


def user_private_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/private"


def user_resumes_root_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/resumes"


def shared_root_prefix() -> str:
    return "shared"


async def build_section_adapter(
    disk_id: str,
    root_prefix: str,
    *,
    ensure_subdirs: tuple[str, ...] = (),
    user_id: UUID | None = None,
) -> StorageAdapter:
    """Create a section-scoped S3 adapter and ensure logical prefixes via ``mkdir``."""
    adapter = create_storage_adapter(
        disk_id=disk_id,
        root_prefix=root_prefix,
    )
    await adapter.mkdir("")

    for subdir in ensure_subdirs:
        await adapter.mkdir(subdir)

    if user_id is not None:
        logger.info(
            "Storage adapter ready: user_id={}, disk_id={}, root_prefix={}",
            user_id,
            disk_id,
            root_prefix,
        )
    else:
        logger.info(
            "Storage adapter ready: disk_id={}, root_prefix={}",
            disk_id,
            root_prefix,
        )
    return adapter
