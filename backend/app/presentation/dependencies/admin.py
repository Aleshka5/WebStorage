from fastapi import Depends

from app.application.admin_service import AdminService
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.repositories.user_repo import UserRepository
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.auth import get_quota_repository, get_user_repository
from app.presentation.dependencies.files import get_file_repository
from config import get_settings


def get_disk_router() -> DiskRouter:
    return DiskRouter(get_settings())


def get_admin_service(
    user_repo: UserRepository = Depends(get_user_repository),
    quota_repo: QuotaRepository = Depends(get_quota_repository),
    file_repo: FileRepository = Depends(get_file_repository),
    disk_router: DiskRouter = Depends(get_disk_router),
) -> AdminService:
    return AdminService(
        user_repo=user_repo,
        quota_repo=quota_repo,
        file_repo=file_repo,
        disk_router=disk_router,
        settings=get_settings(),
    )
