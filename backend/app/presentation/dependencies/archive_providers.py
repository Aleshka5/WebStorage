from functools import lru_cache

from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.disk_router import DiskRouter
from config import get_settings


@lru_cache
def get_archive_manager() -> ArchiveManager:
    return ArchiveManager()


@lru_cache
def get_archive_disk_router() -> DiskRouter:
    return DiskRouter(get_settings())
