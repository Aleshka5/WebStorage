from fastapi import Depends

from app.application.maintenance_service import MaintenanceService
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.admin import get_disk_router
from app.presentation.dependencies.auth import get_quota_repository
from app.presentation.dependencies.files import get_file_repository


def get_maintenance_service(
    file_repo: FileRepository = Depends(get_file_repository),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    disk_router: DiskRouter = Depends(get_disk_router),
) -> MaintenanceService:
    return MaintenanceService(
        file_repo=file_repo,
        quota_repo=quota_repo,
        disk_router=disk_router,
    )
