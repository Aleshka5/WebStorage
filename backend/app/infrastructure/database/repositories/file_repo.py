from datetime import UTC, datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.file_record import FileRecord as FileRecordEntity
from app.domain.entities.file_record import FileSection, FileStatus
from app.infrastructure.database.models import FileRecord as FileRecordModel
from app.infrastructure.database.models import FileSection as FileSectionModel
from app.infrastructure.database.models import FileStatus as FileStatusModel
from app.infrastructure.database.models import User as UserModel


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

    async def get_downloadable_by_relative_path(
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
                FileRecordModel.status.in_(
                    (FileStatusModel.COMMITTED, FileStatusModel.ARCHIVED),
                ),
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_downloadable_by_relative_path_in_section(
        self,
        relative_path: str,
        section: FileSection,
    ) -> FileRecordEntity | None:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.relative_path == relative_path,
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status.in_(
                    (FileStatusModel.COMMITTED, FileStatusModel.ARCHIVED),
                ),
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def list_archived_direct_children(
        self,
        section: FileSection,
        parent_disk_relative_path: str,
        *,
        user_id: UUID | None = None,
    ) -> list[FileRecordEntity]:
        stmt = select(FileRecordModel).where(
            FileRecordModel.section == FileSectionModel(section.value),
            FileRecordModel.status == FileStatusModel.ARCHIVED,
            FileRecordModel.is_archived.is_(True),
        )
        if user_id is not None:
            stmt = stmt.where(FileRecordModel.user_id == user_id)

        result = await self._session.execute(stmt)
        parent = parent_disk_relative_path.rstrip("/")
        records = [
            self._to_entity(model)
            for model in result.scalars().all()
            if self._is_direct_child(model.relative_path, parent)
        ]
        logger.info(
            "Listed {} archived file records in section {} under {}",
            len(records),
            section.value,
            parent_disk_relative_path,
        )
        return records

    async def get_uploaders_by_relative_paths_in_section(
        self,
        relative_paths: list[str],
        section: FileSection,
    ) -> dict[str, str]:
        if not relative_paths:
            return {}

        result = await self._session.execute(
            select(FileRecordModel.relative_path, UserModel.email)
            .join(UserModel, UserModel.id == FileRecordModel.user_id)
            .where(
                FileRecordModel.relative_path.in_(relative_paths),
                FileRecordModel.section == FileSectionModel(section.value),
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        uploaders = {relative_path: email for relative_path, email in result.all()}
        logger.info(
            "Resolved uploaders for {} of {} shared paths in section {}",
            len(uploaders),
            len(relative_paths),
            section.value,
        )
        return uploaders

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

    async def list_all_by_user(self, user_id: UUID) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel).where(FileRecordModel.user_id == user_id)
        )
        records = [self._to_entity(model) for model in result.scalars().all()]
        logger.info("Listed {} file records for user {}", len(records), user_id)
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

    async def list_candidates_for_archive(
        self,
        cutoff: datetime,
    ) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.last_accessed_at < cutoff,
                FileRecordModel.status == FileStatusModel.COMMITTED,
                FileRecordModel.is_archived.is_(False),
            )
        )
        records = [self._to_entity(model) for model in result.scalars().all()]
        logger.info(
            "Found {} file records eligible for archive (cutoff={})",
            len(records),
            cutoff.isoformat(),
        )
        return records

    async def list_archived_records(self) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.is_archived.is_(True),
                FileRecordModel.status == FileStatusModel.ARCHIVED,
            )
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def mark_archived(
        self,
        file_id: UUID,
        archive_path: str,
    ) -> FileRecordEntity | None:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            logger.warning("File record {} not found for archive update", file_id)
            return None

        model.is_archived = True
        model.status = FileStatusModel.ARCHIVED
        model.archive_path = archive_path
        await self._session.flush()
        await self._session.refresh(model)
        logger.info("Marked file record {} as archived at {}", file_id, archive_path)
        return self._to_entity(model)

    async def touch_last_accessed(self, file_id: UUID) -> None:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            logger.warning("File record {} not found for last_accessed update", file_id)
            return

        model.last_accessed_at = datetime.now(tz=UTC)
        await self._session.flush()
        logger.info("Updated last_accessed_at for file record {}", file_id)

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

    async def list_stale_pending(self, cutoff: datetime) -> list[FileRecordEntity]:
        result = await self._session.execute(
            select(FileRecordModel).where(
                FileRecordModel.status == FileStatusModel.PENDING,
                FileRecordModel.created_at < cutoff,
            )
        )
        records = [self._to_entity(model) for model in result.scalars().all()]
        logger.info(
            "Found {} stale PENDING file records (cutoff={})",
            len(records),
            cutoff.isoformat(),
        )
        return records

    async def hard_delete(self, file_id: UUID) -> bool:
        model = await self._session.get(FileRecordModel, file_id)
        if model is None:
            logger.warning("File record {} not found for hard deletion", file_id)
            return False

        await self._session.delete(model)
        await self._session.flush()
        logger.info("Hard-deleted file record {}", file_id)
        return True

    async def sum_committed_bytes_by_user(self, user_id: UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(FileRecordModel.size_bytes), 0)).where(
                FileRecordModel.user_id == user_id,
                FileRecordModel.status == FileStatusModel.COMMITTED,
            )
        )
        total = int(result.scalar_one())
        logger.info("Committed bytes for user {}: {}", user_id, total)
        return total

    async def list_distinct_user_ids_with_committed(self) -> list[UUID]:
        result = await self._session.execute(
            select(FileRecordModel.user_id)
            .where(FileRecordModel.status == FileStatusModel.COMMITTED)
            .distinct()
        )
        user_ids = list(result.scalars().all())
        logger.info("Found {} users with committed file records", len(user_ids))
        return user_ids

    @staticmethod
    def _is_direct_child(relative_path: str, parent_prefix: str) -> bool:
        parent = parent_prefix.rstrip("/")
        child_prefix = f"{parent}/"
        if not relative_path.startswith(child_prefix):
            return False
        suffix = relative_path[len(child_prefix) :]
        return bool(suffix) and "/" not in suffix

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
