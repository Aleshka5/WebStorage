from fastapi import Depends

from app.application.backup_service import BackupService
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.admin import get_disk_router
from config import get_settings


def get_backup_service(
    disk_router: DiskRouter = Depends(get_disk_router),
) -> BackupService:
    return BackupService(disk_router=disk_router, settings=get_settings())
