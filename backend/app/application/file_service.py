import asyncio
import io
import mimetypes
import zipfile
from collections.abc import AsyncIterator
from typing import BinaryIO
from pathlib import Path, PurePosixPath
from uuid import UUID

import aiofiles
import aiofiles.os
from loguru import logger

from app.application.archived_file_reader import (
    delete_archived_blob,
    stream_decompressed_archived,
)
from app.application.archive_service import ARCHIVE_EXTENSION
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.exceptions import AccessDeniedError, FileNotFoundError, QuotaExceededError
from app.domain.value_objects.role import Role
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import FileNode, StorageAdapter
from app.infrastructure.storage.encrypted_adapter import EncryptedStorageAdapter

DEFAULT_MIME_TYPE = "application/octet-stream"
TMP_DIR = ".tmp"
READ_CHUNK = 64 * 1024


class FileService:
    def __init__(
        self,
        adapter: StorageAdapter,
        quota_repo: QuotaRepository,
        file_repo: FileRepository,
        section: FileSection = FileSection.FILES,
        archive_manager: ArchiveManager | None = None,
        disk_router: DiskRouter | None = None,
    ) -> None:
        self._adapter = adapter
        self._quota_repo = quota_repo
        self._file_repo = file_repo
        self._section = section
        self._archive_manager = archive_manager
        self._disk_router = disk_router

    async def list_directory(self, user_id: UUID, path: str) -> list[FileNode]:
        normalized = self._normalize_path(path)
        logger.info("Listing directory for user {} at path {}", user_id, path)
        nodes = await self._adapter.list(normalized)
        nodes = await self._merge_archived_nodes(user_id, normalized, nodes)

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
        record = await self._get_downloadable_record(file_id, actor_id)
        logger.info("Downloading file {} for user {}", file_id, actor_id)

        if record.is_archived:
            async for chunk in self._stream_archived(record):
                yield chunk
        else:
            section_path = self._to_section_path(record)
            async for chunk in self._adapter.read(section_path):
                yield chunk

        await self._file_repo.touch_last_accessed(file_id)

    async def delete_file(
        self,
        actor_id: UUID,
        file_id: UUID,
        *,
        actor_role: Role | None = None,
    ) -> None:
        record = await self._get_downloadable_record(file_id, actor_id)
        self._ensure_can_delete_record(record, actor_id, actor_role)

        if record.is_archived:
            await self._delete_archived_file(record)
        else:
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

    async def upload_zip_folder(
        self,
        user_id: UUID,
        path: str,
        zip_filename: str,
        zip_source: BinaryIO,
        total_uncompressed_bytes: int,
        available_bytes: int,
    ) -> dict:
        """Extract an uploaded ZIP archive into the target directory.

        Validates ZIP entry paths (ZIP slip prevention), pre-checks quota against
        the sum of all uncompressed entry sizes, then writes files and creates DB
        records. Existing files with the same name are overwritten.
        Returns stats: {"files": int, "dirs": int, "total_bytes": int}.
        """
        normalized_path = self._normalize_path(path)
        logger.info(
            "Starting ZIP folder upload for user {} to path {} (uncompressed={})",
            user_id,
            path,
            total_uncompressed_bytes,
        )

        # --- Quota pre-check ---
        if total_uncompressed_bytes > available_bytes:
            raise QuotaExceededError(
                f"ZIP uncompressed size {total_uncompressed_bytes} bytes "
                f"exceeds available quota ({available_bytes} bytes remaining)",
                available_bytes=available_bytes,
            )

        # --- Open and validate ZIP ---
        try:
            zf = zipfile.ZipFile(zip_source, "r")
        except zipfile.BadZipFile:
            raise ValueError("Uploaded file is not a valid ZIP archive")

        entries = zf.infolist()
        if not entries:
            zf.close()
            raise ValueError("ZIP archive is empty")

        # Create a new directory named after the ZIP file (without .zip extension)
        # so contents are not dumped into the current open folder
        archive_name = PurePosixPath(zip_filename).stem  # strip .zip
        target_root = self._join_path(normalized_path, archive_name)
        effective_paths: list[tuple[str, str, zipfile.ZipInfo]] = []  # (section_path, arcname, info)
        try:
            for info in entries:
                raw_name = info.filename
                if raw_name.startswith("/") or raw_name.startswith("\\"):
                    raise ValueError(f"ZIP entry has an absolute path: {raw_name}")

                clean = raw_name.replace("\\", "/")
                parts = PurePosixPath(clean).parts
                if ".." in parts:
                    raise ValueError(f"ZIP entry escapes target directory: {raw_name}")

                # Resolve the entry relative to target_root
                if info.is_dir():
                    entry_section_path = self._join_path(target_root, clean.rstrip("/"))
                else:
                    entry_section_path = self._join_path(target_root, clean)
                    # Normalize (e.g. dir//file -> dir/file)
                    entry_section_path = self._normalize_path(entry_section_path)

                effective_paths.append((entry_section_path, clean, info))
        except ValueError:
            zf.close()
            raise

        # --- Create directories ---
        created_dirs: set[str] = set()
        # Create the archive root directory first so contents are not dumped
        # into the current open folder
        await self._adapter.mkdir(target_root)
        created_dirs.add(target_root)
        for section_path, arcname, info in effective_paths:
            if info.is_dir():
                if section_path not in created_dirs:
                    await self._adapter.mkdir(section_path)
                    created_dirs.add(section_path)

        # --- Write files and create records ---
        files_written = 0
        total_written_bytes = 0
        error_count = 0

        try:
            for section_path, arcname, info in effective_paths:
                if info.is_dir():
                    continue

                # Ensure parent directory exists
                parent = str(PurePosixPath(section_path).parent)
                parent = "" if parent in (".", "") else parent
                if parent and parent not in created_dirs:
                    try:
                        await self._adapter.mkdir(parent)
                        created_dirs.add(parent)
                    except FileExistsError:
                        pass

                try:
                    file_size = info.file_size
                    mime_type = mimetypes.guess_type(arcname)[0] or DEFAULT_MIME_TYPE
                    disk_relative_path = self._adapter.to_disk_relative_path(section_path)

                    # Use only the filename part, not the full arcname path
                    # so that deletion and listing can match by original_name
                    file_basename = PurePosixPath(arcname).name
                    record = await self._file_repo.create(
                        user_id=user_id,
                        disk_id=self._adapter.disk_id,
                        relative_path=disk_relative_path,
                        original_name=file_basename,
                        size_bytes=file_size,
                        mime_type=mime_type,
                        is_encrypted=self._section == FileSection.PRIVATE,
                        section=self._section,
                        status=FileStatus.PENDING,
                    )

                    tmp_path = self._join_path(TMP_DIR, str(record.id))
                    checksum = await self._adapter.write(
                        tmp_path,
                        self._iter_zip_entry(zf, info),
                        file_size,
                    )
                    await self._adapter.rename(tmp_path, section_path)
                    committed = await self._file_repo.update_status(
                        record.id,
                        FileStatus.COMMITTED,
                        checksum_sha256=checksum,
                        relative_path=self._adapter.to_disk_relative_path(section_path),
                    )
                    if committed is None:
                        logger.error("File record {} disappeared after ZIP extract", record.id)
                        await self._file_repo.delete(record.id)
                        error_count += 1
                        continue

                    await self._quota_repo.increment(user_id, file_size, self._section)
                    files_written += 1
                    total_written_bytes += file_size
                    logger.info(
                        "ZIP extracted file {} ({} bytes, checksum={})",
                        record.id,
                        file_size,
                        checksum,
                    )
                except Exception:
                    error_count += 1
                    logger.exception(
                        "Failed to extract ZIP entry {} to path {}",
                        arcname,
                        section_path,
                    )
        finally:
            zf.close()

        stats = {
            "files": files_written,
            "dirs": len(created_dirs),
            "total_bytes": total_written_bytes,
        }
        logger.info(
            "ZIP folder upload completed for user {}: {} files, {} dirs, {} bytes, {} errors",
            user_id,
            files_written,
            len(created_dirs),
            total_written_bytes,
            error_count,
        )
        return stats

    async def _iter_zip_entry(
        self,
        zip_file: zipfile.ZipFile,
        info: zipfile.ZipInfo,
    ) -> AsyncIterator[bytes]:
        source = zip_file.open(info, "r")
        try:
            while True:
                chunk = source.read(READ_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            source.close()

    async def rename(self, actor_id: UUID, file_id: UUID, new_name: str) -> FileRecord:
        record = await self._get_downloadable_record(file_id, actor_id)
        if record.is_archived:
            raise FileNotFoundError(f"Cannot rename archived file {file_id}")
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

    async def download_directory_as_zip(
        self, actor_id: UUID, path: str
    ) -> AsyncIterator[bytes]:
        normalized = self._normalize_path(path)
        await self._ensure_can_download_directory(actor_id, normalized)
        logger.info("Starting directory zip for user {} at path {}", actor_id, path)

        # Recursively collect (storage_path, arcname) for every entry
        entries = await self._walk_directory(normalized, normalized, set())

        # Build ZIP in memory via BytesIO, then stream out in chunks
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry_path, arcname in entries:
                if arcname.endswith("/"):
                    zf.writestr(arcname, b"")
                else:
                    data = await self._collect_file(entry_path)
                    zf.writestr(arcname, data)

        buf.seek(0)
        while True:
            chunk = buf.read(READ_CHUNK)
            if not chunk:
                break
            yield chunk

        logger.info(
            "Directory zip completed for user {} at path {} ({} entries)",
            actor_id,
            path,
            len(entries),
        )

    async def _walk_directory(
        self,
        root: str,
        current: str,
        visited: set[str],
    ) -> list[tuple[str, str]]:
        """Recursively walk the storage directory tree.

        Returns a list of (storage_path, zip_arcname) tuples.
        """
        result: list[tuple[str, str]] = []
        # Avoid symlinks / cycles
        normalized = self._normalize_path(current)
        if normalized in visited:
            logger.warning("Skipping cyclic directory {} during zip", normalized)
            return result
        visited.add(normalized)

        try:
            nodes = await self._adapter.list(normalized)
        except FileNotFoundError:
            return result

        for node in nodes:
            storage_path = self._join_path(normalized, node.name)
            arcname = self._arcname_from(storage_path, root, node.name)
            if node.is_dir:
                arcname_with_slash = arcname.rstrip("/") + "/"
                result.append((storage_path, arcname_with_slash))
                # Recurse into subdirectory
                result.extend(
                    await self._walk_directory(root, storage_path, visited)
                )
            else:
                result.append((storage_path, arcname))

        return result

    async def _collect_file(self, path: str) -> bytes:
        """Read entire file content from storage into memory."""
        buf = io.BytesIO()
        async for chunk in self._read_bytes(path):
            buf.write(chunk)
        return buf.getvalue()

    async def _read_bytes(self, path: str) -> AsyncIterator[bytes]:
        """Read bytes from storage adapter."""
        async for chunk in self._adapter.read(path):  # type: ignore[misc]
            yield chunk

    @staticmethod
    def _arcname_from(storage_path: str, root: str, filename: str) -> str:
        """Build a ZIP arcname relative to the download root.

        storage_path = /base/level1/level2/filename
        root         = /base/level1
        returns      = level2/filename
        """
        # Strip the root prefix and leading slash
        relative = storage_path[len(root):].lstrip("/")
        if not relative:
            return filename
        return relative

    @staticmethod
    def _clean_arcname(name: str) -> str:
        return name.strip().replace("\\", "/")

    async def _ensure_can_download_directory(self, actor_id: UUID, path: str) -> None:
        normalized = self._normalize_path(path)

        if not await self._adapter.exists(normalized):
            raise FileNotFoundError(f"Path {path!r} not found")

        is_dir = await self._is_directory(normalized)
        if not is_dir:
            raise FileNotFoundError(f"Path {path!r} is not a directory")

        if self._section != FileSection.SHARED:
            await self._get_record_by_section_path(actor_id, normalized, self._section)

    async def read_by_path(self, actor_id: UUID, path: str) -> AsyncIterator[bytes]:
        normalized = self._normalize_path(path)
        record = await self._get_record_by_section_path(actor_id, normalized, self._section)

        if record is None and normalized.endswith(ARCHIVE_EXTENSION):
            logical_path = normalized[: -len(ARCHIVE_EXTENSION)]
            record = await self._get_record_by_section_path(
                actor_id,
                logical_path,
                self._section,
            )

        if record is not None and record.is_archived:
            logger.info("Reading archived file {} at path {}", record.id, normalized)
            async for chunk in self._stream_archived(record):
                yield chunk
            await self._file_repo.touch_last_accessed(record.id)
            return

        logger.info("Reading file at path {}", normalized)
        async for chunk in self._adapter.read(normalized):
            yield chunk

        if record is not None:
            await self._file_repo.touch_last_accessed(record.id)

    async def delete_by_path(
        self,
        actor_id: UUID,
        path: str,
        *,
        actor_role: Role | None = None,
    ) -> None:
        normalized = self._normalize_path(path)
        record = await self._get_record_by_section_path(
            actor_id,
            normalized,
            self._section,
        )
        if record is not None:
            await self.delete_file(actor_id, record.id, actor_role=actor_role)
            return

        if not await self._adapter.exists(normalized):
            raise FileNotFoundError(f"Path {path!r} not found")

        is_dir = await self._is_directory(normalized)
        if is_dir:
            logger.info("Deleting directory {} for user {}", normalized, actor_id)
            await self._adapter.delete(normalized)
            return

        logger.warning(
            "Deleting untracked file at path {} for user {}",
            normalized,
            actor_id,
        )
        await self._adapter.delete(normalized)

    async def _merge_archived_nodes(
        self,
        user_id: UUID,
        normalized_path: str,
        nodes: list[FileNode],
    ) -> list[FileNode]:
        parent_disk_path = self._adapter.to_disk_relative_path(normalized_path)

        if self._section == FileSection.SHARED:
            archived_records = await self._file_repo.list_archived_direct_children(
                self._section,
                parent_disk_path,
            )
        else:
            archived_records = await self._file_repo.list_archived_direct_children(
                self._section,
                parent_disk_path,
                user_id=user_id,
            )

        archive_blob_names = {
            self._archive_blob_name(record) for record in archived_records
        }
        filtered = [
            node
            for node in nodes
            if not (not node.is_dir and node.name in archive_blob_names)
        ]

        for record in archived_records:
            filtered.append(
                FileNode(
                    name=record.original_name,
                    is_dir=False,
                    size=record.size_bytes,
                    modified_at=record.last_accessed_at,
                )
            )

        if archived_records:
            logger.info(
                "Merged {} archived file records into listing at path {}",
                len(archived_records),
                normalized_path or "/",
            )

        return filtered

    @staticmethod
    def _archive_blob_name(record: FileRecord) -> str:
        if record.archive_path:
            return PurePosixPath(record.archive_path).name
        return f"{PurePosixPath(record.relative_path).name}{ARCHIVE_EXTENSION}"

    async def _get_record_by_section_path(
        self,
        user_id: UUID,
        section_path: str,
        section: FileSection,
    ) -> FileRecord | None:
        disk_relative_path = self._adapter.to_disk_relative_path(section_path)
        if section == FileSection.SHARED:
            record = await self._file_repo.get_downloadable_by_relative_path_in_section(
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

        record = await self._file_repo.get_downloadable_by_relative_path(
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

    async def _get_downloadable_record(self, file_id: UUID, actor_id: UUID) -> FileRecord:
        record = await self._file_repo.get_by_id(file_id)
        if record is None or record.status not in (FileStatus.COMMITTED, FileStatus.ARCHIVED):
            raise FileNotFoundError(f"File {file_id} not found")
        if self._section != FileSection.SHARED and record.user_id != actor_id:
            raise AccessDeniedError(f"User {actor_id} cannot access file {file_id}")
        return record

    async def _get_committed_record(self, file_id: UUID, actor_id: UUID) -> FileRecord:
        record = await self._get_downloadable_record(file_id, actor_id)
        if record.status != FileStatus.COMMITTED:
            raise FileNotFoundError(f"File {file_id} not found")
        return record

    async def _stream_archived(self, record: FileRecord) -> AsyncIterator[bytes]:
        if self._archive_manager is None:
            raise FileNotFoundError(f"Archived file {record.id} cannot be read")

        encrypted_adapter = self._adapter if isinstance(self._adapter, EncryptedStorageAdapter) else None
        async for chunk in stream_decompressed_archived(
            record,
            self._archive_manager,
            encrypted_adapter=encrypted_adapter,
        ):
            yield chunk

    async def _delete_archived_file(self, record: FileRecord) -> None:
        await delete_archived_blob(record)

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
