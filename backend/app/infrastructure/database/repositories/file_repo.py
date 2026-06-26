from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.file_record import FileRecord as FileRecordEntity
from app.domain.entities.file_record import FileSection, FileStatus
from app.infrastructure.database.models import FileRecord as FileRecordModel
from app.infrastructure.database.models import FileSection as FileSectionModel
from app.infrastructure.database.models import FileStatus as FileStatusModel


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: UUID,
        disk_id: str,
        relative_path: str,
        original_name: str,
        size_bytes: int,
        mime_type: str,
        is_encrypted: bool,
        section: FileSection,
        status: FileStatus = FileStatus.PENDING,
    ) -> FileRecordEntity:
        model = FileRecordModel(
            user_id=user_id,
            disk_id=disk_id,
            relative_path=relative_path,
            original_name=original_name,
            size_bytes=size_bytes,
            mime_type=mime_type,
            is_encrypted=is_encrypted,
            section=FileSectionModel(section.value),
            status=FileStatusModel(status.value),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        logger.info(
            "Created file record {} for user {} (status={})",
            model.id,
            user_id,
            status.value,
        )
        return self._to_entity(model)

    async def get_by_id(self, file_id: UUID) -> FileRecordEntity | None:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            return None
        return self._to_entity(model)

    async def update_status(
        self,
        file_id: UUID,
        status: FileStatus,
        *,
        checksum_sha256: str | None = None,
        relative_path: str | None = None,
        original_name: str | None = None,
    ) -> FileRecordEntity | None:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            logger.warning("File record {} not found for status update", file_id)
            return None

        model.status = FileStatusModel(status.value)
        if checksum_sha256 is not None:
            model.checksum_sha256 = checksum_sha256
        if relative_path is not None:
            model.relative_path = relative_path
        if original_name is not None:
            model.original_name = original_name

        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Updated file record {} status to {}", file_id, status.value)
        return self._to_entity(model)

    async def list_by_user_section(
        self,
        user_id: UUID,
        section: FileSection,
    ) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        records = [self._to_entity(model) for model in result.scalars().all()]
        logger.info(
            "Listed {} committed file records for user {} in section {}",
            len(records),
            user_id,
            section.value,
        )
        return records

    async def delete(self, file_id: UUID) -> FileRecordEntity | None:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            logger.warning("File record {} not found for deletion", file_id)
            return None

        model.status = FileStatusModel.DELETED
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Marked file record {} as deleted", file_id)
        return self._to_entity(model)

    @staticmethod
    def _to_entity(model: FileRecordModel) -> FileRecordEntity:
        return FileRecordEntity(
            id=model.id,
            user_id=model.user_id,
            disk_id=model.disk_id,
            relative_path=model.relative_path,
            original_name=model.original_name,
            size_bytes=model.size_bytes,
            mime_type=model.mime_type,
            is_encrypted=model.is_encrypted,
            section=FileSection(model.section.value),
            status=FileStatus(model.status.value),
            checksum_sha256=model.checksum_sha256,
            created_at=model.created_at,
            last_accessed_at=model.last_accessed_at,
            is_archived=model.is_archived,
            archive_path=model.archive_path,
        )
