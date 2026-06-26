from fastapi import Depends

from app.application.archive_service import ArchiveService
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.presentation.dependencies.archive_providers import (
    get_archive_disk_router,
    get_archive_manager,
)
from app.presentation.dependencies.files import get_file_repository
from config import get_settings


def get_archive_service(
    file_repo: FileRepository = Depends(get_file_repository),
) -> ArchiveService:
    return ArchiveService(
        file_repo=file_repo,
        archive_manager=get_archive_manager(),
        disk_router=get_archive_disk_router(),
        settings=get_settings(),
    )
