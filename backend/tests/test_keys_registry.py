"""Keys Registry: private-session gate, bootstrap, YAML, overwrite-in-place."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.file_service import FileService
from app.infrastructure.storage.encrypted_adapter import (
    EncryptedStorageAdapter,
    derive_encryption_key,
)
from app.application.keys_registry_service import (
    KEYS_FOLDER_NAME,
    KEYS_RELATIVE_PATH,
    KeyEntry,
    KeysRegistryService,
)
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.entities.user import User
from app.domain.exceptions import KeysValidationError, KeysYamlInvalidError, PrivateSessionExpiredError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.private import get_private_file_service
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.private_router import router as private_router
from tests.test_encrypted_storage_adapter import InMemoryStorageAdapter

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


class KeysInMemoryAdapter(InMemoryStorageAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._dirs.add(".tmp")


def _family_user() -> User:
    return User(
        id=USER_ID,
        email="family@example.test",
        role=Role.FAMILY,
        is_active=True,
        created_at=datetime.now(UTC),
    )


class FakeFileRepo:
    def __init__(self) -> None:
        self.records: list[FileRecord] = []

    def committed_for_path(self, relative_path: str) -> list[FileRecord]:
        return [
            record
            for record in self.records
            if record.relative_path == relative_path and record.status == FileStatus.COMMITTED
        ]

    async def create(self, **kwargs) -> FileRecord:
        now = datetime.now(UTC)
        record = FileRecord(
            id=uuid4(),
            user_id=kwargs["user_id"],
            disk_id=kwargs["disk_id"],
            relative_path=kwargs["relative_path"],
            original_name=kwargs["original_name"],
            size_bytes=kwargs["size_bytes"],
            mime_type=kwargs["mime_type"],
            is_encrypted=kwargs["is_encrypted"],
            section=kwargs["section"],
            status=kwargs.get("status", FileStatus.PENDING),
            checksum_sha256=None,
            created_at=now,
            last_accessed_at=now,
            is_archived=False,
            archive_path=None,
        )
        self.records.append(record)
        return record

    def _replace(self, updated: FileRecord) -> FileRecord:
        self.records = [updated if item.id == updated.id else item for item in self.records]
        return updated

    async def update_status(
        self,
        file_id,
        status,
        *,
        checksum_sha256=None,
        relative_path=None,
        original_name=None,
    ) -> FileRecord | None:
        record = next((item for item in self.records if item.id == file_id), None)
        if record is None:
            return None
        return self._replace(
            FileRecord(
                **{
                    **record.__dict__,
                    "status": status,
                    "checksum_sha256": checksum_sha256 or record.checksum_sha256,
                    "relative_path": relative_path or record.relative_path,
                    "original_name": original_name or record.original_name,
                }
            )
        )

    async def update_size(
        self,
        file_id,
        size_bytes: int,
        *,
        checksum_sha256=None,
    ) -> FileRecord | None:
        record = next((item for item in self.records if item.id == file_id), None)
        if record is None:
            return None
        return self._replace(
            FileRecord(
                **{
                    **record.__dict__,
                    "size_bytes": size_bytes,
                    "checksum_sha256": checksum_sha256 or record.checksum_sha256,
                }
            )
        )

    async def get_downloadable_by_relative_path(
        self,
        user_id,
        relative_path: str,
        section: FileSection,
    ) -> FileRecord | None:
        for record in self.records:
            if (
                record.user_id == user_id
                and record.relative_path == relative_path
                and record.section == section
                and record.status in (FileStatus.COMMITTED, FileStatus.ARCHIVED)
            ):
                return record
        return None

    async def heal_stale_relative_path(self, *args, **kwargs) -> FileRecord | None:
        return None

    async def delete(self, file_id) -> FileRecord | None:
        record = next((item for item in self.records if item.id == file_id), None)
        if record is None:
            return None
        return self._replace(FileRecord(**{**record.__dict__, "status": FileStatus.DELETED}))

    async def touch_last_accessed(self, file_id) -> None:
        return None

    async def list_archived_direct_children(self, *args, **kwargs) -> list[FileRecord]:
        return []


class FakeQuotaRepo:
    def __init__(self) -> None:
        self.total_bytes = 0
        self.private_bytes = 0
        self.limit_bytes = 100 * 1024 * 1024
        self.private_limit_bytes = 0

    async def get_by_user_id(self, user_id):
        return SimpleNamespace(
            user_id=user_id,
            total_bytes=self.total_bytes,
            limit_bytes=self.limit_bytes,
            private_bytes=self.private_bytes,
            private_limit_bytes=self.private_limit_bytes,
        )

    async def increment(self, user_id, size_bytes: int, section: FileSection):
        self.total_bytes += size_bytes
        if section == FileSection.PRIVATE:
            self.private_bytes += size_bytes
        return await self.get_by_user_id(user_id)

    async def decrement(self, user_id, size_bytes: int, section: FileSection):
        self.total_bytes = max(0, self.total_bytes - size_bytes)
        if section == FileSection.PRIVATE:
            self.private_bytes = max(0, self.private_bytes - size_bytes)
        return await self.get_by_user_id(user_id)


class FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _file_service(adapter: InMemoryStorageAdapter | None = None) -> tuple[FileService, FakeFileRepo, InMemoryStorageAdapter]:
    storage = adapter or KeysInMemoryAdapter()
    file_repo = FakeFileRepo()
    quota_repo = FakeQuotaRepo()
    service = FileService(
        storage,
        quota_repo,
        file_repo,
        section=FileSection.PRIVATE,
    )
    return service, file_repo, storage


def _keys_client(file_service: FileService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(private_router)
    app.dependency_overrides[get_current_user] = _family_user

    async def _unlocked_file_service() -> FileService:
        return file_service

    async def _session():
        yield FakeSession()

    app.dependency_overrides[get_private_file_service] = _unlocked_file_service
    app.dependency_overrides[get_async_session] = _session
    return TestClient(app)


def test_get_keys_without_unlock_is_private_session_expired() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(private_router)
    app.dependency_overrides[get_current_user] = _family_user

    async def _expired() -> FileService:
        raise PrivateSessionExpiredError("Private session expired")

    app.dependency_overrides[get_private_file_service] = _expired

    client = TestClient(app)
    response = client.get("/api/private/keys")

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == ErrorCode.PRIVATE_SESSION_EXPIRED


@pytest.mark.asyncio
async def test_get_bootstraps_keys_folder_and_empty_yaml() -> None:
    file_service, file_repo, storage = _file_service()
    service = KeysRegistryService(file_service)

    entries = await service.list_keys(USER_ID)

    assert entries == []
    assert await storage.exists(KEYS_FOLDER_NAME)
    assert await storage.exists(KEYS_RELATIVE_PATH)
    payload = b""
    async for chunk in storage.read(KEYS_RELATIVE_PATH):
        payload += chunk
    assert payload.decode("utf-8").strip() in {"{}", ""}
    disk_path = storage.to_disk_relative_path(KEYS_RELATIVE_PATH)
    assert len(file_repo.committed_for_path(disk_path)) == 1


@pytest.mark.asyncio
async def test_put_get_round_trip_preserves_order() -> None:
    file_service, _, _ = _file_service()
    service = KeysRegistryService(file_service)

    saved = await service.save_keys(
        USER_ID,
        [
            KeyEntry(name="openai", value="sk-openai-secret-e24a"),
            KeyEntry(name="github", value="ghp_abcdefghijklmnop"),
        ],
    )
    loaded = await service.list_keys(USER_ID)

    assert [entry.name for entry in saved] == ["openai", "github"]
    assert loaded == saved
    assert loaded[0].value == "sk-openai-secret-e24a"


@pytest.mark.asyncio
async def test_save_rejects_empty_and_duplicate_names() -> None:
    file_service, _, _ = _file_service()
    service = KeysRegistryService(file_service)

    with pytest.raises(KeysValidationError) as empty_name:
        await service.save_keys(USER_ID, [KeyEntry(name="  ", value="abc")])
    assert empty_name.value.error_code == ErrorCode.KEY_NAME_EMPTY

    with pytest.raises(KeysValidationError) as empty_value:
        await service.save_keys(USER_ID, [KeyEntry(name="k", value="   ")])
    assert empty_value.value.error_code == ErrorCode.KEY_VALUE_EMPTY

    with pytest.raises(KeysValidationError) as duplicate:
        await service.save_keys(
            USER_ID,
            [KeyEntry(name="k", value="a"), KeyEntry(name="k", value="b")],
        )
    assert duplicate.value.error_code == ErrorCode.KEY_NAME_DUPLICATE


@pytest.mark.asyncio
async def test_invalid_yaml_is_not_overwritten_on_get() -> None:
    file_service, _, storage = _file_service()
    await storage.mkdir(KEYS_FOLDER_NAME)
    await storage.write(KEYS_RELATIVE_PATH, _chunks(b"- not a mapping\n"), 16)
    before = b""
    async for chunk in storage.read(KEYS_RELATIVE_PATH):
        before += chunk

    service = KeysRegistryService(file_service)
    with pytest.raises(KeysYamlInvalidError):
        await service.list_keys(USER_ID)

    after = b""
    async for chunk in storage.read(KEYS_RELATIVE_PATH):
        after += chunk
    assert after == before


@pytest.mark.asyncio
async def test_overwrite_does_not_create_duplicate_file_records() -> None:
    file_service, file_repo, storage = _file_service()
    service = KeysRegistryService(file_service)

    await service.save_keys(USER_ID, [KeyEntry(name="a", value="value-one")])
    await service.save_keys(USER_ID, [KeyEntry(name="a", value="value-two-longer")])

    disk_path = storage.to_disk_relative_path(KEYS_RELATIVE_PATH)
    assert len(file_repo.committed_for_path(disk_path)) == 1
    loaded = await service.list_keys(USER_ID)
    assert loaded == [KeyEntry(name="a", value="value-two-longer")]


@pytest.mark.asyncio
async def test_encrypted_vault_round_trip() -> None:
    inner = KeysInMemoryAdapter()
    key = derive_encryption_key("passphrase", USER_ID)
    adapter = EncryptedStorageAdapter(inner=inner, key=key)
    file_service, file_repo, _ = _file_service(adapter)
    service = KeysRegistryService(file_service)

    await service.list_keys(USER_ID)
    saved = await service.save_keys(
        USER_ID,
        [KeyEntry(name="vault", value="sk-encrypted-secret-e24a")],
    )
    loaded = await service.list_keys(USER_ID)

    assert loaded == saved
    disk_path = adapter.to_disk_relative_path(KEYS_RELATIVE_PATH)
    assert len(file_repo.committed_for_path(disk_path)) == 1


@pytest.mark.asyncio
async def test_second_get_does_not_wipe_existing_keys() -> None:
    file_service, _, _ = _file_service()
    service = KeysRegistryService(file_service)
    await service.save_keys(USER_ID, [KeyEntry(name="keep", value="secret-value")])

    first = await service.list_keys(USER_ID)
    second = await service.list_keys(USER_ID)

    assert first == second == [KeyEntry(name="keep", value="secret-value")]


def test_api_put_get_and_validation() -> None:
    file_service, file_repo, storage = _file_service()
    client = _keys_client(file_service)

    created = client.get("/api/private/keys")
    assert created.status_code == 200
    assert created.json() == {"keys": []}

    saved = client.put(
        "/api/private/keys",
        json={"keys": [{"name": "api", "value": "sk-openai-secret-e24a"}]},
    )
    assert saved.status_code == 200
    assert saved.json()["keys"][0]["value"] == "sk-openai-secret-e24a"

    listed = client.get("/api/private/keys")
    assert listed.json()["keys"] == [{"name": "api", "value": "sk-openai-secret-e24a"}]

    empty_name = client.put("/api/private/keys", json={"keys": [{"name": " ", "value": "x"}]})
    assert empty_name.status_code == 400
    assert empty_name.json()["detail"]["error_code"] == ErrorCode.KEY_NAME_EMPTY

    empty_value = client.put("/api/private/keys", json={"keys": [{"name": "k", "value": " "}]})
    assert empty_value.status_code == 400
    assert empty_value.json()["detail"]["error_code"] == ErrorCode.KEY_VALUE_EMPTY

    duplicate = client.put(
        "/api/private/keys",
        json={
            "keys": [
                {"name": "a", "value": "one"},
                {"name": "a", "value": "two"},
            ]
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"]["error_code"] == ErrorCode.KEY_NAME_DUPLICATE

    disk_path = storage.to_disk_relative_path(KEYS_RELATIVE_PATH)
    assert len(file_repo.committed_for_path(disk_path)) == 1


@pytest.mark.asyncio
async def test_api_invalid_yaml_returns_409_and_keeps_file() -> None:
    file_service, _, storage = _file_service()
    await storage.mkdir(KEYS_FOLDER_NAME)
    invalid = b"- not a mapping\n"
    await storage.write(KEYS_RELATIVE_PATH, _chunks(invalid), len(invalid))

    client = _keys_client(file_service)
    response = client.get("/api/private/keys")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == ErrorCode.KEYS_YAML_INVALID
    after = b""
    async for chunk in storage.read(KEYS_RELATIVE_PATH):
        after += chunk
    assert after == invalid


async def _chunks(data: bytes):
    yield data
