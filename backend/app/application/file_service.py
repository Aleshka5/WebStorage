import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from uuid import UUID

from loguru import logger

from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.exceptions import AccessDeniedError, FileNotFoundError
from app.domain.value_objects.role import Role
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.storage.base_adapter import FileNode, StorageAdapter

DEFAULT_MIME_TYPE = "application/octet-stream"
TMP_DIR = ".tmp"


class FileService:
    def __init__(
        self,
        adapter: StorageAdapter,
        quota_repo: QuotaRepository,
        file_repo: FileRepository,
        section: FileSection = FileSection.FILES,
    ) -> None:
        self._adapter = adapter
        self._quota_repo = quota_repo
        self._file_repo = file_repo
        self._section = section

    async def list_directory(self, user_id: UUID, path: str) -> list[FileNode]:
        normalized = self._normalize_path(path)
        logger.info("Listing directory for user {} at path {}", user_id, path)
        nodes = await self._adapter.list(normalized)

        if self._section != FileSection.SHARED:
            return nodes

        file_nodes = [node for node in nodes if not node.is_dir]
        if not file_nodes:
            return nodes

        relative_paths = [
            self._adapter.to_disk_relative_path(self._join_path(normalized, node.name))
            for node in file_nodes
        ]
        uploaders = await self._file_repo.get_uploaders_by_relative_paths_in_section(
            relative_paths,
            FileSection.SHARED,
        )

        enriched: list[FileNode] = []
        for node in nodes:
            if node.is_dir:
                enriched.append(node)
                continue

            relative_path = self._adapter.to_disk_relative_path(
                self._join_path(normalized, node.name),
            )
            uploaded_by = uploaders.get(relative_path)
            enriched.append(
                FileNode(
                    name=node.name,
                    is_dir=node.is_dir,
                    size=node.size,
                    modified_at=node.modified_at,
                    uploaded_by=uploaded_by,
                )
            )

        logger.info(
            "Enriched {} shared files with uploader info at path {}",
            len(file_nodes),
            path,
        )
        return enriched

    async def upload_file(
        self,
        user_id: UUID,
        path: str,
        filename: str,
        data: AsyncIterator[bytes],
        size: int,
        section: FileSection,
    ) -> FileRecord:
        normalized_path = self._normalize_path(path)
        final_storage_path = self._join_path(normalized_path, filename)
        disk_relative_path = self._adapter.to_disk_relative_path(final_storage_path)
        mime_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE

        record = await self._file_repo.create(
            user_id=user_id,
            disk_id=self._adapter.disk_id,
            relative_path=disk_relative_path,
            original_name=filename,
            size_bytes=size,
            mime_type=mime_type,
            is_encrypted=section == FileSection.PRIVATE,
            section=section,
            status=FileStatus.PENDING,
        )

        tmp_path = self._join_path(TMP_DIR, str(record.id))
        logger.info(
            "Starting upload for user {} file {} (size={}, section={})",
            user_id,
            record.id,
            size,
            section.value,
        )

        try:
            checksum = await self._adapter.write(tmp_path, data, size)
            await self._adapter.rename(tmp_path, final_storage_path)
            committed = await self._file_repo.update_status(
                record.id,
                FileStatus.COMMITTED,
                checksum_sha256=checksum,
                relative_path=self._adapter.to_disk_relative_path(final_storage_path),
            )
            if committed is None:
                raise FileNotFoundError(f"File record {record.id} not found after upload")

            await self._quota_repo.increment(user_id, size, section)
            logger.info(
                "Upload completed for user {} file {} (checksum={})",
                user_id,
                record.id,
                checksum,
            )
            return committed
        except Exception:
            logger.exception(
                "Upload failed for user {} file {}, rolling back",
                user_id,
                record.id,
            )
            if await self._adapter.exists(tmp_path):
                await self._adapter.delete(tmp_path)
            await self._file_repo.delete(record.id)
            raise

    async def download_file(self, actor_id: UUID, file_id: UUID) -> AsyncIterator[bytes]:
        record = await self._get_committed_record(file_id, actor_id)
        section_path = self._to_section_path(record)
        logger.info("Downloading file {} for user {}", file_id, actor_id)

        async for chunk in self._adapter.read(section_path):
            yield chunk

    async def delete_file(
        self,
        actor_id: UUID,
        file_id: UUID,
        *,
        actor_role: Role | None = None,
    ) -> None:
        record = await self._get_committed_record(file_id, actor_id)
        self._ensure_can_delete_record(record, actor_id, actor_role)
        section_path = self._to_section_path(record)

        if await self._adapter.exists(section_path):
            await self._adapter.delete(section_path)

        await self._file_repo.delete(file_id)
        await self._quota_repo.decrement(record.user_id, record.size_bytes, record.section)
        logger.info("Deleted file {} by user {}", file_id, actor_id)

    async def create_directory(self, user_id: UUID, path: str, name: str) -> None:
        dir_path = self._join_path(self._normalize_path(path), name)
        logger.info("Creating directory {} for user {}", dir_path, user_id)
        await self._adapter.mkdir(dir_path)

    async def rename(self, actor_id: UUID, file_id: UUID, new_name: str) -> FileRecord:
        record = await self._get_committed_record(file_id, actor_id)
        old_section_path = self._to_section_path(record)
        parent = str(PurePosixPath(old_section_path).parent)
        new_section_path = (
            new_name if parent in (".", "") else f"{parent}/{new_name}"
        )

        await self._adapter.rename(old_section_path, new_section_path)
        updated = await self._file_repo.update_status(
            record.id,
            record.status,
            relative_path=self._adapter.to_disk_relative_path(new_section_path),
            original_name=new_name,
        )
        if updated is None:
            raise FileNotFoundError(f"File record {file_id} not found after rename")

        logger.info("Renamed file {} to {} for user {}", file_id, new_name, actor_id)
        return updated

    async def read_by_path(self, path: str) -> AsyncIterator[bytes]:
        normalized = self._normalize_path(path)
        logger.info("Reading file at path {}", normalized)
        async for chunk in self._adapter.read(normalized):
            yield chunk

    async def delete_by_path(
        self,
        actor_id: UUID,
        path: str,
        *,
        actor_role: Role | None = None,
    ) -> None:
        normalized = self._normalize_path(path)
        if not await self._adapter.exists(normalized):
            raise FileNotFoundError(f"Path {path!r} not found")

        is_dir = await self._is_directory(normalized)
        if is_dir:
            logger.info("Deleting directory {} for user {}", normalized, actor_id)
            await self._adapter.delete(normalized)
            return

        record = await self._get_record_by_section_path(
            actor_id,
            normalized,
            self._section,
        )
        if record is None:
            logger.warning(
                "Deleting untracked file at path {} for user {}",
                normalized,
                actor_id,
            )
            await self._adapter.delete(normalized)
            return

        await self.delete_file(actor_id, record.id, actor_role=actor_role)

    async def _get_record_by_section_path(
        self,
        user_id: UUID,
        section_path: str,
        section: FileSection,
    ) -> FileRecord | None:
        disk_relative_path = self._adapter.to_disk_relative_path(section_path)
        if section == FileSection.SHARED:
            record = await self._file_repo.get_committed_by_relative_path_in_section(
                disk_relative_path,
                section,
            )
            if record is not None:
                return record

            filename = PurePosixPath(section_path).name
            return await self._file_repo.heal_stale_relative_path_in_section(
                section,
                disk_relative_path,
                filename,
            )

        record = await self._file_repo.get_committed_by_relative_path(
            user_id,
            disk_relative_path,
            section,
        )
        if record is not None:
            return record

        filename = PurePosixPath(section_path).name
        return await self._file_repo.heal_stale_relative_path(
            user_id,
            section,
            disk_relative_path,
            filename,
        )

    async def rename_by_path(
        self,
        user_id: UUID,
        path: str,
        new_name: str,
    ) -> FileRecord | None:
        normalized = self._normalize_path(path)
        if not await self._adapter.exists(normalized):
            raise FileNotFoundError(f"Path {path!r} not found")

        parent = str(PurePosixPath(normalized).parent)
        parent = "" if parent in (".", "") else parent
        new_path = new_name if not parent else f"{parent}/{new_name}"

        if await self._is_directory(normalized):
            old_disk_prefix = self._adapter.to_disk_relative_path(normalized)
            new_disk_prefix = self._adapter.to_disk_relative_path(new_path)
            logger.info(
                "Renaming directory {} to {} for user {}",
                normalized,
                new_path,
                user_id,
            )
            await self._adapter.rename(normalized, new_path)
            if self._section == FileSection.SHARED:
                await self._file_repo.update_relative_path_prefix_in_section(
                    self._section,
                    old_disk_prefix,
                    new_disk_prefix,
                )
            else:
                await self._file_repo.update_relative_path_prefix(
                    user_id,
                    self._section,
                    old_disk_prefix,
                    new_disk_prefix,
                )
            return None

        record = await self._get_record_by_section_path(
            user_id,
            normalized,
            self._section,
        )
        if record is None:
            raise FileNotFoundError(f"File record for path {path!r} not found")

        return await self.rename(user_id, record.id, new_name)

    async def _is_directory(self, path: str) -> bool:
        try:
            await self._adapter.list(path)
        except FileNotFoundError:
            return False
        return True

    def _ensure_can_delete_record(
        self,
        record: FileRecord,
        actor_id: UUID,
        actor_role: Role | None,
    ) -> None:
        if self._section == FileSection.SHARED:
            if actor_role != Role.ADMIN and record.user_id != actor_id:
                raise AccessDeniedError(
                    f"User {actor_id} cannot delete shared file owned by {record.user_id}",
                )
            return

        if record.user_id != actor_id:
            raise AccessDeniedError(f"User {actor_id} cannot delete file {record.id}")

    async def _get_committed_record(self, file_id: UUID, actor_id: UUID) -> FileRecord:
        record = await self._file_repo.get_by_id(file_id)
        if record is None or record.status != FileStatus.COMMITTED:
            raise FileNotFoundError(f"File {file_id} not found")
        if self._section != FileSection.SHARED and record.user_id != actor_id:
            raise AccessDeniedError(f"User {actor_id} cannot access file {file_id}")
        return record

    def _to_section_path(self, record: FileRecord) -> str:
        prefix = f"{self._adapter.disk_relative_prefix}/"
        if record.relative_path.startswith(prefix):
            return record.relative_path[len(prefix) :]
        return Path(record.relative_path).name

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.strip().replace("\\", "/").strip("/")

    @staticmethod
    def _join_path(directory: str, name: str) -> str:
        if directory:
            return f"{directory}/{name}"
        return name
