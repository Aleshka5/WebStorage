import asyncio
import base64
import shutil
from pathlib import Path
from uuid import UUID

import aiofiles
from loguru import logger

from app.application.file_service import FileService
from app.domain.entities.file_record import FileSection
from app.domain.exceptions import PrivateSessionExpiredError, StorageUnavailableError
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.session_store import SessionStore
from app.infrastructure.storage.encrypted_adapter import (
    MARKER_FILENAME,
    MARKER_PLAINTEXT,
    decrypt_blob,
    derive_encryption_key,
    encrypt_blob,
    EncryptedStorageAdapter,
)
from app.infrastructure.storage.plain_adapter import PlainStorageAdapter
from config import Settings, get_settings

_KEY_ENCODING = "ascii"


class PrivateService:
    def __init__(
        self,
        session_store: SessionStore,
        quota_repo: QuotaRepository,
        file_repo: FileRepository,
        settings: Settings | None = None,
    ) -> None:
        self._session_store = session_store
        self._quota_repo = quota_repo
        self._file_repo = file_repo
        self._settings = settings or get_settings()

    @property
    def private_session_ttl_seconds(self) -> int:
        return self._settings.auth.private_session_ttl_hours * 3600

    async def unlock(self, user_id: UUID, session_id: str, passphrase: str) -> bool:
        key = derive_encryption_key(passphrase, user_id)
        logger.info("Private unlock attempt for user {}", user_id)

        try:
            disk_id = await self._resolve_user_disk_id(user_id)
            base_path = self._private_base_path(user_id, disk_id)
            base_path.mkdir(parents=True, exist_ok=True)
            inner = PlainStorageAdapter(base_path, disk_id=disk_id)
            marker_path = self._inner_marker_path(inner)

            if marker_path.is_file():
                async with aiofiles.open(marker_path, mode="rb") as marker_file:
                    encrypted_marker = await marker_file.read()
                try:
                    decrypted_marker = decrypt_blob(key, encrypted_marker).decode()
                except Exception:
                    logger.warning("Private unlock failed for user {}: invalid passphrase", user_id)
                    return False
                if decrypted_marker != MARKER_PLAINTEXT:
                    logger.warning("Private unlock failed for user {}: marker mismatch", user_id)
                    return False
            else:
                encrypted_marker = encrypt_blob(key, MARKER_PLAINTEXT.encode())
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(marker_path, mode="wb") as marker_file:
                    await marker_file.write(encrypted_marker)
                logger.info("Created private marker file for user {}", user_id)

            encoded_key = base64.urlsafe_b64encode(key).decode(_KEY_ENCODING)
            await self._session_store.set_private_key(
                session_id,
                encoded_key,
                self.private_session_ttl_seconds,
            )
            logger.info("Private section unlocked for user {}", user_id)
            return True
        except StorageUnavailableError:
            logger.error("Private unlock failed for user {}: storage unavailable", user_id)
            raise

    async def lock(self, session_id: str) -> None:
        await self._session_store.delete_private_key(session_id)
        logger.info("Private section locked for session {}", session_id)

    async def reset_storage(self, user_id: UUID, session_id: str) -> None:
        logger.info("Resetting private storage for user {}", user_id)

        await self._session_store.delete_private_key(session_id)

        disk_id = await self._resolve_user_disk_id(user_id)
        base_path = self._private_base_path(user_id, disk_id)

        if base_path.exists():
            await asyncio.to_thread(shutil.rmtree, base_path)

        base_path.mkdir(parents=True, exist_ok=True)

        deleted_records = await self._file_repo.delete_all_by_user_section(
            user_id,
            FileSection.PRIVATE,
        )
        await self._quota_repo.reset_private_usage(user_id)

        logger.info(
            "Private storage reset completed for user {} ({} file records removed)",
            user_id,
            deleted_records,
        )

    async def get_file_service(self, user_id: UUID, session_id: str) -> FileService:
        encoded_key = await self._session_store.get_private_key(session_id)
        if not encoded_key:
            logger.warning("Private session expired for user {}", user_id)
            raise PrivateSessionExpiredError("Private session expired")

        key = base64.urlsafe_b64decode(encoded_key.encode(_KEY_ENCODING))
        disk_id = await self._resolve_user_disk_id(user_id)
        base_path = self._private_base_path(user_id, disk_id)
        base_path.mkdir(parents=True, exist_ok=True)
        inner = PlainStorageAdapter(base_path, disk_id=disk_id)
        adapter = EncryptedStorageAdapter(inner=inner, key=key)
        logger.info("Private FileService initialized for user {} on disk {}", user_id, disk_id)
        return FileService(
            adapter,
            self._quota_repo,
            self._file_repo,
            section=FileSection.PRIVATE,
        )

    async def get_quota(self, user_id: UUID) -> dict[str, int]:
        usage = await self._quota_repo.get_by_user_id(user_id)
        return {
            "private_bytes": usage.private_bytes,
            "private_limit_bytes": 0,
        }

    async def get_session_status(self, session_id: str) -> dict[str, int | bool]:
        encoded_key = await self._session_store.get_private_key(session_id)
        active = encoded_key is not None
        expires_in_seconds = await self._session_store.get_private_key_ttl(session_id)
        return {
            "active": active,
            "expires_in_seconds": expires_in_seconds,
        }

    async def _resolve_user_disk_id(self, user_id: UUID) -> str:
        private_records = await self._file_repo.list_by_user_section(user_id, FileSection.PRIVATE)
        if private_records:
            return private_records[0].disk_id

        file_records = await self._file_repo.list_by_user_section(user_id, FileSection.FILES)
        if file_records:
            return file_records[0].disk_id

        disk_router = DiskRouter(self._settings)
        return disk_router.get_write_disk().id

    def _private_base_path(self, user_id: UUID, disk_id: str) -> Path:
        disk_router = DiskRouter(self._settings)
        disk = disk_router.get_disk_by_id(disk_id)
        return disk.mount_path / "users" / str(user_id) / "private"

    @staticmethod
    def _inner_marker_path(inner: PlainStorageAdapter) -> Path:
        return inner.base_path / MARKER_FILENAME
