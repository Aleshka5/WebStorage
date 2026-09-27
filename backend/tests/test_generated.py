"""Generated images: Qwen save, owner history, quota, and corrupt meta.yaml."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.application.archive_service import ARCHIVE_EXTENSION, ArchiveService
from app.application.file_service import FileService
from app.application.generated_service import GeneratedService
from app.application.photo_service import PhotoService
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.disk_router import DiskRouter
from app.infrastructure.thumbnail_service import ThumbnailService
from app.application.ports.user_directory import DirectoryUser
from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.entities.user import User
from app.domain.exceptions import UserServiceUnavailableError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import (
    get_current_user,
    get_user_directory,
    get_user_repository,
)
from app.presentation.dependencies.files import get_file_service
from app.presentation.dependencies.generated import get_generated_service
from app.presentation.dependencies.photos import get_photo_service
from app.presentation.dependencies.private import get_private_file_service
from app.presentation.dependencies.shared import get_shared_file_service
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.file_router import router as file_router
from app.presentation.routers.file_router import shared_router
from app.presentation.routers.generated_router import router as generated_router
from app.presentation.routers.photo_router import router as photo_router
from app.presentation.routers.private_router import router as private_router
from config import get_settings
from tests.test_encrypted_storage_adapter import InMemoryStorageAdapter

USER_ID = UUID("33333333-3333-3333-3333-333333333333")
OTHER_USER_ID = UUID("44444444-4444-4444-4444-444444444444")
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
PROMPT = 'A cinematic portrait: "soft" light'
NEGATIVE_PROMPT = ""
RESULT_BYTES = b"\x89PNG\r\nresult"
REF_ONE = b"\x89PNG\r\nref-1"
REF_TWO = b"\x89PNG\r\nref-2"


class GeneratedInMemoryAdapter(InMemoryStorageAdapter):
    def __init__(self, user_id: UUID) -> None:
        super().__init__(root_prefix=f"users/{user_id}/generated")
        self._dirs.add(".tmp")


class FakeFileRepo:
    def __init__(self, timeline: list[tuple] | None = None) -> None:
        self.records: list[FileRecord] = []
        self.timeline = timeline if timeline is not None else []

    def committed(self) -> list[FileRecord]:
        return [record for record in self.records if record.status == FileStatus.COMMITTED]

    def active(self) -> list[FileRecord]:
        return [
            record
            for record in self.records
            if record.status in (FileStatus.COMMITTED, FileStatus.ARCHIVED)
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

    async def get_by_id(self, file_id) -> FileRecord | None:
        return next((item for item in self.records if item.id == file_id), None)

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

    async def update_size(self, file_id, size_bytes: int, *, checksum_sha256=None):
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

    async def list_active_under_prefix(
        self,
        user_id,
        section: FileSection,
        prefix: str,
    ) -> list[FileRecord]:
        prefix_with_slash = f"{prefix.rstrip('/')}/"
        return [
            record
            for record in self.records
            if record.user_id == user_id
            and record.section == section
            and record.status in (FileStatus.COMMITTED, FileStatus.ARCHIVED)
            and record.relative_path.startswith(prefix_with_slash)
        ]

    async def delete(self, file_id) -> FileRecord | None:
        record = next((item for item in self.records if item.id == file_id), None)
        if record is None:
            return None
        return self._replace(FileRecord(**{**record.__dict__, "status": FileStatus.DELETED}))

    async def touch_last_accessed(self, file_id) -> None:
        self.timeline.append(("touch", file_id))

    async def mark_archived(self, file_id, archive_path: str) -> FileRecord | None:
        record = next((item for item in self.records if item.id == file_id), None)
        if record is None:
            return None
        return self._replace(
            FileRecord(
                **{
                    **record.__dict__,
                    "status": FileStatus.ARCHIVED,
                    "is_archived": True,
                    "archive_path": archive_path,
                }
            )
        )

    def _committed_in_section(self, user_id, section: FileSection) -> list[FileRecord]:
        return [
            record
            for record in self.records
            if record.user_id == user_id
            and record.section == section
            and record.status == FileStatus.COMMITTED
        ]

    async def count_by_user_section(self, user_id, section: FileSection) -> int:
        return len(self._committed_in_section(user_id, section))

    async def list_by_user_section_paginated(
        self,
        user_id,
        section: FileSection,
        *,
        offset: int,
        limit: int,
    ) -> list[FileRecord]:
        rows = self._committed_in_section(user_id, section)
        return rows[offset : offset + limit]

    async def list_archived_direct_children(self, *args, **kwargs) -> list[FileRecord]:
        return []


class FakeQuotaRepo:
    def __init__(self, *, limit_bytes: int = 100 * 1024 * 1024) -> None:
        self.total_bytes = 0
        self.limit_bytes = limit_bytes
        self.private_bytes = 0
        self.photos_bytes = 0

    async def get_by_user_id(self, user_id):
        return SimpleNamespace(
            user_id=user_id,
            total_bytes=self.total_bytes,
            limit_bytes=self.limit_bytes,
            private_bytes=self.private_bytes,
            private_limit_bytes=0,
            photos_bytes=self.photos_bytes,
        )

    async def increment(self, user_id, size_bytes: int, section: FileSection):
        self.total_bytes += size_bytes
        if section == FileSection.PHOTOS:
            self.photos_bytes += size_bytes
        if section == FileSection.PRIVATE:
            self.private_bytes += size_bytes
        return await self.get_by_user_id(user_id)

    async def decrement(self, user_id, size_bytes: int, section: FileSection):
        self.total_bytes = max(0, self.total_bytes - size_bytes)
        if section == FileSection.PHOTOS:
            self.photos_bytes = max(0, self.photos_bytes - size_bytes)
        if section == FileSection.PRIVATE:
            self.private_bytes = max(0, self.private_bytes - size_bytes)
        return await self.get_by_user_id(user_id)


class FakeUserDirectory:
    def __init__(
        self,
        *,
        role: Role = Role.STRANGER,
        role_error: Exception | None = None,
    ) -> None:
        self.role = role
        self.role_error = role_error
        self.role_calls: list[UUID] = []
        self.user_calls: list[UUID] = []

    async def get_storage_role(self, user_id: UUID) -> Role:
        self.role_calls.append(user_id)
        if self.role_error is not None:
            raise self.role_error
        return self.role

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        self.user_calls.append(user_id)
        return DirectoryUser(
            id=user_id,
            email="owner@example.test",
            username="owner",
            storage_role=self.role,
        )

    async def list_users(self) -> list[DirectoryUser]:
        return []


class FakeUserRepository:
    def __init__(self) -> None:
        self.principals: list[AuthPrincipal] = []

    async def upsert_from_principal(self, principal: AuthPrincipal) -> User:
        self.principals.append(principal)
        return User(
            id=principal.id,
            email=principal.email,
            role=Role.STRANGER,
            is_active=True,
            created_at=datetime.now(UTC),
        )


class FakeSession:
    def __init__(self, timeline: list[tuple]) -> None:
        self._timeline = timeline

    async def commit(self) -> None:
        self._timeline.append(("commit", None))

    async def rollback(self) -> None:
        self._timeline.append(("rollback", None))


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 27, 11, 50, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current = value + timedelta(seconds=1)
        return value


class GeneratedWorld:
    def __init__(self, directory: FakeUserDirectory) -> None:
        self.directory = directory
        self.clock = AdvancingClock()
        self.services: dict[UUID, GeneratedService] = {}
        self.storages: dict[UUID, GeneratedInMemoryAdapter] = {}
        self.repos: dict[UUID, FakeFileRepo] = {}
        self.quotas: dict[UUID, FakeQuotaRepo] = {}
        self.opened_users: list[UUID] = []
        self.timeline: list[tuple] = []

    def service_for(self, user_id: UUID, *, limit_bytes: int = 100 * 1024 * 1024) -> GeneratedService:
        self.opened_users.append(user_id)
        existing = self.services.get(user_id)
        if existing is not None:
            return existing

        storage = GeneratedInMemoryAdapter(user_id)
        repo = FakeFileRepo(self.timeline)
        quota = FakeQuotaRepo(limit_bytes=limit_bytes)
        file_service = FileService(
            storage,
            quota,
            repo,
            section=FileSection.GENERATED,
            archive_manager=ArchiveManager(),
        )
        service = GeneratedService(file_service, quota, clock=self.clock)
        self.services[user_id] = service
        self.storages[user_id] = storage
        self.repos[user_id] = repo
        self.quotas[user_id] = quota
        return service


def _error_code(response) -> str:
    return response.json()["detail"]["error_code"]


def _run_directories(storage: GeneratedInMemoryAdapter) -> set[str]:
    return {item for item in storage._dirs if item and not item.startswith(".")}


def _auth_headers(
    user_id: UUID,
    *,
    storage_role: str | None = None,
    email: str | None = None,
) -> dict[str, str]:
    headers = {"X-Auth-User-Id": str(user_id)}
    if storage_role is not None:
        headers["X-Storage-Role"] = storage_role
    if email is not None:
        headers["X-Auth-Email"] = email
    return headers


def _history_headers(user_id: UUID, role: Role = Role.STRANGER) -> dict[str, str]:
    return {
        "X-User-Id": str(user_id),
        "X-Storage-Role": role.value,
        "X-Auth-Email": "owner@example.test",
    }


def _form(
    *,
    prompt: str | None = PROMPT,
    negative_prompt: str | None = NEGATIVE_PROMPT,
    include_numbers: bool = True,
) -> dict[str, str]:
    data: dict[str, str] = {}
    if prompt is not None:
        data["prompt"] = prompt
    if negative_prompt is not None:
        data["negative_prompt"] = negative_prompt
    if include_numbers:
        data.update(
            {
                "seed": "1823486689",
                "steps": "40",
                "true_cfg_scale": "1.0",
                "width": "2048",
                "height": "2048",
                "duration": "96.4",
            }
        )
    return data


def _files(*, references: list[bytes] | None = None, result: bytes | None = RESULT_BYTES) -> list:
    parts: list = []
    for index, payload in enumerate(references if references is not None else [REF_ONE, REF_TWO], start=1):
        parts.append(("images", (f"source-{index}.png", payload, "image/png")))
    if result is not None:
        parts.append(("result", ("out.png", result, "image/png")))
    return parts


def _client(
    world: GeneratedWorld,
    *,
    limit_bytes: int = 100 * 1024 * 1024,
    include_other_sections: bool = False,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(generated_router)

    async def _session():
        yield FakeSession(world.timeline)

    async def _generated_service(current_user: User = Depends(get_current_user)) -> GeneratedService:
        return world.service_for(current_user.id, limit_bytes=limit_bytes)

    app.dependency_overrides[get_user_directory] = lambda: world.directory
    app.dependency_overrides[get_user_repository] = lambda: FakeUserRepository()
    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_generated_service] = _generated_service
    if include_other_sections:
        _mount_other_sections(app, world)
    return TestClient(app)


def _section_file_service(world: GeneratedWorld, user_id: UUID, section: FileSection) -> FileService:
    root = "shared" if section == FileSection.SHARED else f"users/{user_id}/{section.value.lower()}"
    return FileService(
        InMemoryStorageAdapter(root_prefix=root),
        world.quotas[user_id],
        world.repos[user_id],
        section=section,
    )


def _mount_other_sections(app: FastAPI, world: GeneratedWorld) -> None:
    app.include_router(file_router)
    app.include_router(photo_router)
    app.include_router(private_router)
    app.include_router(shared_router)

    async def _files(current_user: User = Depends(get_current_user)) -> FileService:
        return _section_file_service(world, current_user.id, FileSection.FILES)

    async def _private(current_user: User = Depends(get_current_user)) -> FileService:
        return _section_file_service(world, current_user.id, FileSection.PRIVATE)

    async def _shared(current_user: User = Depends(get_current_user)) -> FileService:
        return _section_file_service(world, current_user.id, FileSection.SHARED)

    async def _photos(current_user: User = Depends(get_current_user)) -> PhotoService:
        return PhotoService(
            InMemoryStorageAdapter(root_prefix=f"users/{current_user.id}/photos"),
            ThumbnailService(),
            world.repos[current_user.id],
            world.quotas[current_user.id],
            DiskRouter(get_settings()),
        )

    app.dependency_overrides[get_file_service] = _files
    app.dependency_overrides[get_private_file_service] = _private
    app.dependency_overrides[get_shared_file_service] = _shared
    app.dependency_overrides[get_photo_service] = _photos


def _created_at_from_id(run_id: str) -> str:
    stamp = run_id.rsplit("-", 1)[0]
    parsed = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_save_under_header_user_id_writes_directory_layout() -> None:
    directory = FakeUserDirectory(role=Role.STRANGER)
    world = GeneratedWorld(directory)
    client = _client(world)

    response = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID),
        data=_form(),
        files=_files(),
    )

    assert response.status_code == 201
    body = response.json()
    run_id = body["id"]
    assert RUN_ID_PATTERN.fullmatch(run_id)
    assert body["created_at"] == _created_at_from_id(run_id)
    assert directory.role_calls == [USER_ID]
    assert directory.user_calls == [USER_ID]
    assert world.opened_users == [USER_ID]

    storage = world.storages[USER_ID]
    repo = world.repos[USER_ID]
    quota = world.quotas[USER_ID]
    prefix = f"users/{USER_ID}/generated/{run_id}/"
    assert _run_directories(storage) == {run_id}
    assert set(storage._files) == {
        f"{run_id}/meta.yaml",
        f"{run_id}/ref-1.png",
        f"{run_id}/ref-2.png",
        f"{run_id}/result.png",
    }
    assert storage._files[f"{run_id}/ref-1.png"] == REF_ONE
    assert storage._files[f"{run_id}/ref-2.png"] == REF_TWO
    assert storage._files[f"{run_id}/result.png"] == RESULT_BYTES

    meta = yaml.safe_load(storage._files[f"{run_id}/meta.yaml"])
    assert meta["prompt"] == PROMPT
    assert meta["negative_prompt"] == ""
    assert meta["seed"] == 1823486689
    assert meta["steps"] == 40
    assert meta["true_cfg_scale"] == 1.0
    assert meta["width"] == 2048
    assert meta["height"] == 2048
    assert meta["duration"] == 96.4
    assert meta["created_at"] == body["created_at"]
    assert meta["references"] == ["ref-1.png", "ref-2.png"]
    assert meta["result"] == "result.png"

    assert {record.original_name for record in repo.committed()} == {
        "meta.yaml",
        "ref-1.png",
        "ref-2.png",
        "result.png",
    }
    assert all(record.section is FileSection.GENERATED for record in repo.committed())
    assert all(record.relative_path.startswith(prefix) for record in repo.committed())
    assert all(record.is_encrypted is False for record in repo.committed())
    assert quota.total_bytes == sum(record.size_bytes for record in repo.committed())
    assert quota.photos_bytes == 0
    assert quota.private_bytes == 0


def test_family_and_admin_can_store() -> None:
    for role in (Role.FAMILY, Role.ADMIN):
        world = GeneratedWorld(FakeUserDirectory(role=role))
        client = _client(world)

        response = client.post(
            "/api/generated",
            headers=_auth_headers(USER_ID),
            data=_form(),
            files=_files(references=[]),
        )

        assert response.status_code == 201
        run_id = response.json()["id"]
        meta = yaml.safe_load(world.storages[USER_ID]._files[f"{run_id}/meta.yaml"])
        assert meta["references"] == []
        assert world.storages[USER_ID]._files[f"{run_id}/result.png"] == RESULT_BYTES


def test_missing_or_non_uuid_user_id_is_401_and_writes_nothing() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    payload = {"data": _form(), "files": _files()}

    missing = client.post("/api/generated", **payload)
    invalid = client.post(
        "/api/generated",
        headers={"X-Auth-User-Id": "not-a-uuid"},
        **payload,
    )

    assert missing.status_code == 401
    assert _error_code(missing) == ErrorCode.UNAUTHORIZED
    assert invalid.status_code == 401
    assert _error_code(invalid) == ErrorCode.UNAUTHORIZED
    assert world.opened_users == []
    assert world.directory.role_calls == []


def test_blocked_role_is_403_and_writes_nothing() -> None:
    looked_up = FakeUserDirectory(role=Role.BLOCKED)
    looked_up_world = GeneratedWorld(looked_up)
    looked_up_client = _client(looked_up_world)
    looked_up_response = looked_up_client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="blocked@example.test"),
        data=_form(),
        files=_files(),
    )

    assert looked_up_response.status_code == 403
    assert _error_code(looked_up_response) == ErrorCode.ACCESS_DENIED
    assert looked_up.role_calls == [USER_ID]
    assert looked_up_world.opened_users == []

    headed = FakeUserDirectory(role=Role.ADMIN)
    headed_world = GeneratedWorld(headed)
    headed_client = _client(headed_world)
    headed_response = headed_client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, storage_role=Role.BLOCKED.value, email="blocked@example.test"),
        data=_form(),
        files=_files(),
    )

    assert headed_response.status_code == 403
    assert _error_code(headed_response) == ErrorCode.ACCESS_DENIED
    assert headed.role_calls == []
    assert headed_world.opened_users == []


def test_user_service_down_while_resolving_role_is_503() -> None:
    directory = FakeUserDirectory(role_error=UserServiceUnavailableError("user directory down"))
    world = GeneratedWorld(directory)
    client = _client(world)

    response = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID),
        data=_form(),
        files=_files(),
    )

    assert response.status_code == 503
    assert _error_code(response) == ErrorCode.USER_SERVICE_UNAVAILABLE
    assert world.opened_users == []


def test_invalid_payload_writes_nothing() -> None:
    world = GeneratedWorld(FakeUserDirectory(role=Role.FAMILY))
    client = _client(world)
    headers = _auth_headers(USER_ID, email="owner@example.test")

    missing_prompt = client.post(
        "/api/generated",
        headers=headers,
        data=_form(prompt=None),
        files=_files(),
    )
    blank_prompt = client.post(
        "/api/generated",
        headers=headers,
        data=_form(prompt="   "),
        files=_files(),
    )
    missing_result = client.post(
        "/api/generated",
        headers=headers,
        data=_form(),
        files=_files(result=None),
    )
    too_many_refs = client.post(
        "/api/generated",
        headers=headers,
        data=_form(),
        files=_files(references=[b"ref"] * 11),
    )

    for response in (missing_prompt, blank_prompt, missing_result, too_many_refs):
        assert response.status_code == 400
        assert _error_code(response) == ErrorCode.GENERATED_INVALID

    storage = world.storages[USER_ID]
    assert storage._files == {}
    assert _run_directories(storage) == set()
    assert world.quotas[USER_ID].total_bytes == 0
    assert world.repos[USER_ID].committed() == []


def test_quota_miss_writes_nothing() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world, limit_bytes=1)

    response = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(),
    )

    assert response.status_code == 413
    assert _error_code(response) == ErrorCode.QUOTA_EXCEEDED
    storage = world.storages[USER_ID]
    assert storage._files == {}
    assert _run_directories(storage) == set()
    assert world.quotas[USER_ID].total_bytes == 0
    assert world.repos[USER_ID].records == []


def test_list_is_owner_scoped_and_skips_corrupt_meta() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    headers = _history_headers(USER_ID)

    first = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(prompt="older prompt"),
        files=_files(references=[REF_ONE]),
    )
    second = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(prompt="newer prompt"),
        files=_files(references=[]),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    older_id = first.json()["id"]
    newer_id = second.json()["id"]

    storage = world.storages[USER_ID]
    storage._files[f"{older_id}/meta.yaml"] = b"- broken: [\n"
    storage._dirs.add("20260901T000000Z-aaaaaaaa")

    listed = client.get("/api/generated", headers=headers)

    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "id": newer_id,
                "created_at": second.json()["created_at"],
                "prompt": "newer prompt",
            }
        ]
    }
    assert storage._files[f"{older_id}/meta.yaml"] == b"- broken: [\n"

    other = client.get("/api/generated", headers=_history_headers(OTHER_USER_ID))
    assert other.status_code == 200
    assert other.json() == {"items": []}

    hidden = client.get(f"/api/generated/{newer_id}", headers=_history_headers(OTHER_USER_ID))
    assert hidden.status_code == 404
    assert _error_code(hidden) == ErrorCode.FILE_NOT_FOUND


def test_get_one_and_streams_only_listed_files() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    headers = _history_headers(USER_ID, Role.FAMILY)
    storage = world.storages[USER_ID]
    storage._files[f"{run_id}/ref-9.png"] = b"not-listed"

    detail = client.get(f"/api/generated/{run_id}", headers=headers)
    first_ref = client.get(f"/api/generated/{run_id}/files/ref-1.png", headers=headers)
    second_ref = client.get(f"/api/generated/{run_id}/files/ref-2.png", headers=headers)
    extra = client.get(f"/api/generated/{run_id}/files/ref-9.png", headers=headers)
    meta_file = client.get(f"/api/generated/{run_id}/files/meta.yaml", headers=headers)
    unknown = client.get(
        "/api/generated/20260927T115012Z-deadbeef",
        headers=headers,
    )

    assert detail.status_code == 200
    assert detail.json() == {
        "prompt": PROMPT,
        "negative_prompt": "",
        "seed": 1823486689,
        "steps": 40,
        "true_cfg_scale": 1.0,
        "width": 2048,
        "height": 2048,
        "duration": 96.4,
        "references": [
            f"/api/generated/{run_id}/files/ref-1.png",
            f"/api/generated/{run_id}/files/ref-2.png",
        ],
        "result": f"/api/generated/{run_id}/files/result.png",
    }
    result_record = next(
        record for record in world.repos[USER_ID].committed() if record.original_name == "result.png"
    )
    world.timeline.clear()
    result = client.get(f"/api/generated/{run_id}/files/result.png", headers=headers)

    assert result.status_code == 200
    assert result.content == RESULT_BYTES
    assert result.headers["content-type"].startswith("image/png")
    assert world.timeline[-2:] == [("touch", result_record.id), ("commit", None)]
    assert first_ref.status_code == 200
    assert first_ref.content == REF_ONE
    assert second_ref.status_code == 200
    assert second_ref.content == REF_TWO
    assert extra.status_code == 404
    assert meta_file.status_code == 404
    assert unknown.status_code == 404


def test_invalid_meta_returns_409_and_does_not_rewrite() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(references=[REF_ONE]),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    storage = world.storages[USER_ID]
    broken = b"prompt: [\n"
    storage._files[f"{run_id}/meta.yaml"] = broken

    response = client.get(f"/api/generated/{run_id}", headers=_history_headers(USER_ID, Role.ADMIN))

    assert response.status_code == 409
    assert _error_code(response) == ErrorCode.GENERATED_META_INVALID
    assert storage._files[f"{run_id}/meta.yaml"] == broken


def test_delete_releases_quota_and_records_and_second_delete_is_404() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    repo = world.repos[USER_ID]
    quota = world.quotas[USER_ID]
    result_record = next(record for record in repo.committed() if record.original_name == "result.png")
    repo._replace(
        FileRecord(
            **{
                **result_record.__dict__,
                "status": FileStatus.ARCHIVED,
                "is_archived": True,
                "archive_path": None,
            }
        )
    )
    assert quota.total_bytes > 0

    headers = _history_headers(USER_ID)
    deleted = client.delete(f"/api/generated/{run_id}", headers=headers)
    again = client.delete(f"/api/generated/{run_id}", headers=headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert quota.total_bytes == 0
    assert repo.active() == []
    assert all(record.status is FileStatus.DELETED for record in repo.records)
    assert run_id not in world.storages[USER_ID]._dirs
    assert not any(path.startswith(f"{run_id}/") for path in world.storages[USER_ID]._files)
    assert again.status_code == 404
    assert _error_code(again) == ErrorCode.FILE_NOT_FOUND
    assert quota.total_bytes == 0


def test_generated_objects_are_absent_from_other_sections() -> None:
    world = GeneratedWorld(FakeUserDirectory(role=Role.FAMILY))
    client = _client(world, include_other_sections=True)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(references=[REF_ONE]),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    headers = _history_headers(USER_ID, Role.FAMILY)
    repo = world.repos[USER_ID]
    prefix = f"users/{USER_ID}/generated/"

    files = client.get("/api/files", headers=headers)
    photos = client.get("/api/photos", headers=headers)
    private = client.get("/api/private", headers=headers)
    shared = client.get("/api/shared", headers=headers)

    assert files.status_code == 200
    assert files.json() == []
    assert photos.status_code == 200
    assert photos.json()["items"] == []
    assert photos.json()["total"] == 0
    assert private.status_code == 200
    assert private.json() == []
    assert shared.status_code == 200
    assert shared.json() == []
    for response in (files, photos, private, shared):
        assert run_id not in response.text
        assert "result.png" not in response.text
    assert repo.committed()
    assert {record.section for record in repo.committed()} == {FileSection.GENERATED}
    assert all(record.relative_path.startswith(prefix) for record in repo.committed())


def test_failed_write_removes_partial_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    real_upload = FileService.upload_file

    async def fail_result(self, *args, **kwargs):
        filename = kwargs.get("filename")
        if filename == "result.png":
            raise OSError("injected write failure")
        return await real_upload(self, *args, **kwargs)

    monkeypatch.setattr(FileService, "upload_file", fail_result)

    with pytest.raises(OSError, match="injected write failure"):
        client.post(
            "/api/generated",
            headers=_auth_headers(USER_ID, email="owner@example.test"),
            data=_form(),
            files=_files(),
        )

    storage = world.storages[USER_ID]
    assert _run_directories(storage) == set()
    assert storage._files == {}
    assert world.quotas[USER_ID].total_bytes == 0
    assert world.repos[USER_ID].active() == []


def test_missing_meta_returns_409_and_does_not_write_a_file() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(references=[REF_ONE]),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    storage = world.storages[USER_ID]
    del storage._files[f"{run_id}/meta.yaml"]
    snapshot = dict(storage._files)

    response = client.get(f"/api/generated/{run_id}", headers=_history_headers(USER_ID))

    assert response.status_code == 409
    assert _error_code(response) == ErrorCode.GENERATED_META_INVALID
    assert storage._files == snapshot
    assert f"{run_id}/meta.yaml" not in storage._files


def test_delete_of_another_users_run_is_404() -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(references=[REF_ONE]),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    deleted = client.delete(
        f"/api/generated/{run_id}",
        headers=_history_headers(OTHER_USER_ID),
    )

    assert deleted.status_code == 404
    assert _error_code(deleted) == ErrorCode.FILE_NOT_FOUND
    assert f"{run_id}/result.png" in world.storages[USER_ID]._files
    still_there = client.get(f"/api/generated/{run_id}", headers=_history_headers(USER_ID))
    assert still_there.status_code == 200


async def _archive_hot_objects(
    world: GeneratedWorld,
    user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = world.storages[user_id]
    repo = world.repos[user_id]
    disk = InMemoryStorageAdapter(disk_id=storage.disk_id, root_prefix="")

    def _disk_adapter(_disk_id: str) -> InMemoryStorageAdapter:
        return disk

    monkeypatch.setattr("app.application.archive_service.build_disk_root_adapter", _disk_adapter)
    monkeypatch.setattr("app.application.archived_file_reader.build_disk_root_adapter", _disk_adapter)

    archive_service = ArchiveService(
        file_repo=repo,
        archive_manager=ArchiveManager(),
        disk_router=DiskRouter(get_settings()),
    )
    prefix = f"{storage.root_prefix.strip('/')}/"
    for record in list(repo.committed()):
        section_path = record.relative_path[len(prefix) :]
        payload = storage._files[section_path]
        await disk.mkdir(str(PurePosixPath(record.relative_path).parent))

        async def _chunks(data: bytes = payload) -> AsyncIterator[bytes]:
            yield data

        await disk.write(record.relative_path, _chunks(), len(payload))
        archived = await archive_service._archive_record(record)
        assert archived is True
        del storage._files[section_path]
        assert not await disk.exists(record.relative_path)
        assert await disk.exists(f"{record.relative_path}{ARCHIVE_EXTENSION}")


async def test_archived_run_lists_opens_and_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    world = GeneratedWorld(FakeUserDirectory())
    client = _client(world)
    created = client.post(
        "/api/generated",
        headers=_auth_headers(USER_ID, email="owner@example.test"),
        data=_form(),
        files=_files(references=[REF_ONE]),
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    await _archive_hot_objects(world, USER_ID, monkeypatch)
    storage = world.storages[USER_ID]
    assert f"{run_id}/meta.yaml" not in storage._files
    assert f"{run_id}/result.png" not in storage._files
    assert f"{run_id}/ref-1.png" not in storage._files
    headers = _history_headers(USER_ID)

    listed = client.get("/api/generated", headers=headers)
    detail = client.get(f"/api/generated/{run_id}", headers=headers)
    result = client.get(f"/api/generated/{run_id}/files/result.png", headers=headers)
    reference = client.get(f"/api/generated/{run_id}/files/ref-1.png", headers=headers)

    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "id": run_id,
                "created_at": created.json()["created_at"],
                "prompt": PROMPT,
            }
        ]
    }
    assert detail.status_code == 200
    assert detail.json()["result"] == f"/api/generated/{run_id}/files/result.png"
    assert result.status_code == 200
    assert result.content == RESULT_BYTES
    assert reference.status_code == 200
    assert reference.content == REF_ONE
