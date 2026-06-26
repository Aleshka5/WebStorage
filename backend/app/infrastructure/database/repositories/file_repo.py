from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
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

    async def get_committed_by_relative_path(
        self,
        user_id: UUID,
        relative_path: str,
        section: FileSection,
    ) -> FileRecordEntity | None:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.relative_path == relative_path,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_committed_by_relative_path_in_section(
        self,
        relative_path: str,
        section: FileSection,
    ) -> FileRecordEntity | None:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.relative_path == relative_path,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def list_by_section(
        self,
        section: FileSection,
        *,
        limit: int | None = None,
    ) -> list[FileRecordEntity]:
        stmt = select(FileRecordModel).where(
            FileRecordModel.section == FileSectionModel(section.value),
            FileRecordModel.status == FileStatusModel.COMMITTED,
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [self._to_entity(model) for model in result.scalars().all()]

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

    async def count_by_user_section(self, user_id: UUID, section: FileSection) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(FileRecordModel)
            .where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        return int(result.scalar_one())

    async def list_by_user_section_paginated(
        self,
        user_id: UUID,
        section: FileSection,
        *,
        offset: int,
        limit: int,
    ) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel)
            .where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
            .order_by(FileRecordModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        records = [self._to_entity(model) for model in result.scalars().all()]
        logger.info(
            "Listed {} paginated file records for user {} in section {} (offset={}, limit={})",
            len(records),
            user_id,
            section.value,
            offset,
            limit,
        )
        return records

    async def update_relative_path_prefix(
        self,
        user_id: UUID,
        section: FileSection,
        old_prefix: str,
        new_prefix: str,
    ) -> int:
        prefix_with_slash = f"{old_prefix}/"
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
                FileRecordModel.relative_path.startswith(prefix_with_slash),
            )
        )
        models = list(result.scalars().all())
        for model in models:
            model.relative_path = f"{new_prefix}{model.relative_path[len(old_prefix):]}"

        if models:
            await self._session.flush()

        logger.info(
            "Updated relative_path prefix for {} file records: {} -> {}",
            len(models),
            old_prefix,
            new_prefix,
        )
        return len(models)

    async def update_relative_path_prefix_in_section(
        self,
        section: FileSection,
        old_prefix: str,
        new_prefix: str,
    ) -> int:
        prefix_with_slash = f"{old_prefix}/"
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
                FileRecordModel.relative_path.startswith(prefix_with_slash),
            )
        )
        models = list(result.scalars().all())
        for model in models:
            model.relative_path = f"{new_prefix}{model.relative_path[len(old_prefix):]}"

        if models:
            await self._session.flush()

        logger.info(
            "Updated relative_path prefix for {} shared file records: {} -> {}",
            len(models),
            old_prefix,
            new_prefix,
        )
        return len(models)

    async def heal_stale_relative_path(
        self,
        user_id: UUID,
        section: FileSection,
        correct_relative_path: str,
        original_name: str,
    ) -> FileRecordEntity | None:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
                FileRecordModel.original_name == original_name,
            )
        )
        models = list(result.scalars().all())

        if len(models) != 1:
            return None

        model = models[0]
        if model.relative_path == correct_relative_path:
            return self._to_entity(model)

        logger.warning(
            "Healing stale relative_path for file {}: {} -> {}",
            model.id,
            model.relative_path,
            correct_relative_path,
        )
        model.relative_path = correct_relative_path
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def heal_stale_relative_path_in_section(
        self,
        section: FileSection,
        correct_relative_path: str,
        original_name: str,
    ) -> FileRecordEntity | None:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
                FileRecordModel.original_name == original_name,
            )
        )
        models = list(result.scalars().all())

        if len(models) != 1:
            return None

        model = models[0]
        if model.relative_path == correct_relative_path:
            return self._to_entity(model)

        logger.warning(
            "Healing stale relative_path for shared file {}: {} -> {}",
            model.id,
            model.relative_path,
            correct_relative_path,
        )
        model.relative_path = correct_relative_path
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def delete_all_by_user_section(self, user_id: UUID, section: FileSection) -> int:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        models = list(result.scalars().all())

        for model in models:
            model.status = FileStatusModel.DELETED

        if models:
            await self._session.flush()

        logger.info(
            "Marked {} file records as deleted for user {} in section {}",
            len(models),
            user_id,
            section.value,
        )
        return len(models)

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
