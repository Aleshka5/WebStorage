import asyncio
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

import aiofiles
from loguru import logger

from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.exceptions import AccessDeniedError, FileNotFoundError, UnsupportedFormatError
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.storage.base_adapter import StorageAdapter
from app.infrastructure.thumbnail_service import ThumbnailService
from config import get_settings

DEFAULT_MIME_TYPE = "application/octet-stream"
ORIGINALS_DIR = "originals"
PREVIEWS_DIR = "previews"
SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})


@dataclass(frozen=True)
class PhotoItem:
    id: UUID
    preview_url: str
    original_url: str
    created_at: datetime
    size: int


@dataclass(frozen=True)
class PhotosPage:
    items: list[PhotoItem]
    total: int
    has_next: bool


class PhotoService:
    def __init__(
        self,
        adapter: StorageAdapter,
        thumbnail_service: ThumbnailService,
        file_repo: FileRepository,
        quota_repo: QuotaRepository,
        disk_router: DiskRouter,
    ) -> None:
        self._adapter = adapter
        self._thumbnail_service = thumbnail_service
        self._file_repo = file_repo
        self._quota_repo = quota_repo
        self._disk_router = disk_router
        self._thumbnail_max_px = get_settings().business_logic.thumbnail_max_px

    async def upload_photo(
        self,
        user_id: UUID,
        filename: str,
        data: AsyncIterator[bytes],
        size: int,
    ) -> FileRecord:
        self._validate_photo_filename(filename)
        extension = Path(filename).suffix.lower()
        mime_type = mimetypes.guess_type(filename)[0] or DEFAULT_MIME_TYPE

        record = await self._file_repo.create(
            user_id=user_id,
            disk_id=self._adapter.disk_id,
            relative_path="",
            original_name=filename,
            size_bytes=size,
            mime_type=mime_type,
            is_encrypted=False,
            section=FileSection.PHOTOS,
            status=FileStatus.PENDING,
        )

        original_section_path = self._original_path(record.id, extension)
        disk_relative_path = self._adapter.to_disk_relative_path(original_section_path)

        logger.info(
            "Starting photo upload for user {} file {} (size={})",
            user_id,
            record.id,
            size,
        )

        try:
            checksum = await self._adapter.write(original_section_path, data, size)
            committed = await self._file_repo.update_status(
                record.id,
                FileStatus.COMMITTED,
                checksum_sha256=checksum,
                relative_path=disk_relative_path,
            )
            if committed is None:
                raise FileNotFoundError(f"Photo record {record.id} not found after upload")

            await self._quota_repo.increment(user_id, size, FileSection.PHOTOS)

            preview_section_path = self._preview_path(record.id)
            asyncio.create_task(
                self._generate_preview(original_section_path, preview_section_path),
                name=f"photo-preview-{record.id}",
            )

            logger.info(
                "Photo upload completed for user {} file {} (checksum={})",
                user_id,
                record.id,
                checksum,
            )
            return committed
        except Exception:
            logger.exception(
                "Photo upload failed for user {} file {}, rolling back",
                user_id,
                record.id,
            )
            if await self._adapter.exists(original_section_path):
                await self._adapter.delete(original_section_path)
            await self._file_repo.delete(record.id)
            raise

    async def list_photos(self, user_id: UUID, page: int, limit: int) -> PhotosPage:
        if page < 1:
            page = 1
        if limit < 1:
            limit = 1

        offset = (page - 1) * limit
        total = await self._file_repo.count_by_user_section(user_id, FileSection.PHOTOS)
        records = await self._file_repo.list_by_user_section_paginated(
            user_id,
            FileSection.PHOTOS,
            offset=offset,
            limit=limit,
        )

        items = [self._to_photo_item(record) for record in records]
        has_next = offset + len(records) < total

        logger.info(
            "Listed photos for user {} page {} limit {} (total={}, returned={})",
            user_id,
            page,
            limit,
            total,
            len(items),
        )
        return PhotosPage(items=items, total=total, has_next=has_next)

    async def get_preview(self, user_id: UUID, file_id: UUID) -> tuple[bytes, str]:
        record = await self._get_committed_record(user_id, file_id)
        preview_path = self._preview_path(file_id)

        if await self._adapter.exists(preview_path):
            logger.info("Serving preview for photo {} user {}", file_id, user_id)
            content = await self._read_bytes(preview_path)
            return content, "image/jpeg"

        original_path = self._to_section_path(record)
        logger.warning(
            "Preview missing for photo {}, serving original for user {}",
            file_id,
            user_id,
        )
        content = await self._read_bytes(original_path)
        return content, record.mime_type

    async def get_original(self, user_id: UUID, file_id: UUID) -> AsyncIterator[bytes]:
        record = await self._get_committed_record(user_id, file_id)
        section_path = self._to_section_path(record)
        logger.info("Streaming original for photo {} user {}", file_id, user_id)

        async for chunk in self._adapter.read(section_path):
            yield chunk

    async def stream_original(
        self,
        user_id: UUID,
        file_id: UUID,
    ) -> tuple[str, str, AsyncIterator[bytes]]:
        record = await self._get_committed_record(user_id, file_id)
        section_path = self._to_section_path(record)
        logger.info("Opening original stream for photo {} user {}", file_id, user_id)
        return record.mime_type, record.original_name, self._adapter.read(section_path)

    async def delete_photo(self, user_id: UUID, file_id: UUID) -> None:
        record = await self._get_committed_record(user_id, file_id)
        original_path = self._to_section_path(record)
        preview_path = self._preview_path(file_id)

        if await self._adapter.exists(original_path):
            await self._adapter.delete(original_path)
        if await self._adapter.exists(preview_path):
            await self._adapter.delete(preview_path)

        await self._file_repo.delete(file_id)
        await self._quota_repo.decrement(user_id, record.size_bytes, FileSection.PHOTOS)
        logger.info("Deleted photo {} for user {}", file_id, user_id)

    async def _generate_preview(self, source_section_path: str, preview_section_path: str) -> None:
        try:
            await self._adapter.mkdir(PREVIEWS_DIR)
            source_path = self._adapter.base_path / source_section_path
            output_path = self._adapter.base_path / preview_section_path
            await asyncio.to_thread(
                self._thumbnail_service.generate,
                source_path,
                output_path,
                self._thumbnail_max_px,
            )
            logger.info("Preview generated at {}", preview_section_path)
        except Exception:
            logger.exception(
                "Failed to generate preview for source {}",
                source_section_path,
            )

    async def _get_committed_record(self, user_id: UUID, file_id: UUID) -> FileRecord:
        record = await self._file_repo.get_by_id(file_id)
        if record is None or record.status != FileStatus.COMMITTED:
            raise FileNotFoundError(f"Photo {file_id} not found")
        if record.user_id != user_id:
            raise AccessDeniedError(f"User {user_id} cannot access photo {file_id}")
        if record.section != FileSection.PHOTOS:
            raise FileNotFoundError(f"Photo {file_id} not found")
        return record

    async def _read_bytes(self, section_path: str) -> bytes:
        target = self._adapter.base_path / section_path
        async with aiofiles.open(target, mode="rb") as file_handle:
            return await file_handle.read()

    def _to_section_path(self, record: FileRecord) -> str:
        prefix = f"{self._adapter.disk_relative_prefix}/"
        if record.relative_path.startswith(prefix):
            return record.relative_path[len(prefix) :]
        return PurePosixPath(record.relative_path).name

    @staticmethod
    def _original_path(file_id: UUID, extension: str) -> str:
        suffix = extension if extension else ".jpg"
        return f"{ORIGINALS_DIR}/{file_id}{suffix}"

    @staticmethod
    def _preview_path(file_id: UUID) -> str:
        return f"{PREVIEWS_DIR}/{file_id}_thumb.jpg"

    @staticmethod
    def _to_photo_item(record: FileRecord) -> PhotoItem:
        return PhotoItem(
            id=record.id,
            preview_url=f"/api/photos/{record.id}/preview",
            original_url=f"/api/photos/{record.id}/original",
            created_at=record.created_at,
            size=record.size_bytes,
        )

    @staticmethod
    def _validate_photo_filename(filename: str) -> None:
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"Unsupported photo format {extension!r}. "
                f"Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
