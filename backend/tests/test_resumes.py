"""Resumes: role gate, bootstrap, tree CRUD, YAML metadata, recursive delete."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.file_service import FileService
from app.application.resume_service import (
    STATUSES_FILENAME,
    VACANCY_META_FILENAME,
    ResumeService,
    ResumeStatus,
    VacancyField,
)
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.domain.entities.user import User
from app.domain.exceptions import ResumeMetaInvalidError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user, get_quota_repository
from app.presentation.dependencies.resumes import get_resumes_file_service
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers.resume_router import files_router, router as resume_router
from tests.test_encrypted_storage_adapter import InMemoryStorageAdapter

USER_ID = UUID("22222222-2222-2222-2222-222222222222")
ROOT_PREFIX = f"users/{USER_ID}/resumes"


class ResumesInMemoryAdapter(InMemoryStorageAdapter):
    def __init__(self) -> None:
        super().__init__(root_prefix=ROOT_PREFIX)
        self._dirs.add(".tmp")

    async def list(self, path: str):
        nodes = await super().list(path)
        return [node for node in nodes if not node.name.startswith(".")]

    async def raw_bytes(self, path: str) -> bytes:
        payload = b""
        async for chunk in self.read(path):
            payload += chunk
        return payload


def _user(role: Role = Role.FAMILY) -> User:
    return User(
        id=USER_ID,
        email="family@example.test",
        role=role,
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

    def committed(self) -> list[FileRecord]:
        return [record for record in self.records if record.status == FileStatus.COMMITTED]

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

    async def update_relative_path_prefix(
        self,
        user_id,
        section: FileSection,
        old_prefix: str,
        new_prefix: str,
    ) -> int:
        prefix_with_slash = f"{old_prefix}/"
        moved = 0
        for record in list(self.records):
            if (
                record.user_id == user_id
                and record.section == section
                and record.status == FileStatus.COMMITTED
                and record.relative_path.startswith(prefix_with_slash)
            ):
                self._replace(
                    FileRecord(
                        **{
                            **record.__dict__,
                            "relative_path": (
                                f"{new_prefix}{record.relative_path[len(old_prefix):]}"
                            ),
                        }
                    )
                )
                moved += 1
        return moved

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
        self.limit_bytes = 100 * 1024 * 1024
        self.private_bytes = 0
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
        return await self.get_by_user_id(user_id)

    async def decrement(self, user_id, size_bytes: int, section: FileSection):
        self.total_bytes = max(0, self.total_bytes - size_bytes)
        return await self.get_by_user_id(user_id)


class FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _file_service() -> tuple[FileService, FakeFileRepo, FakeQuotaRepo, ResumesInMemoryAdapter]:
    storage = ResumesInMemoryAdapter()
    file_repo = FakeFileRepo()
    quota_repo = FakeQuotaRepo()
    service = FileService(storage, quota_repo, file_repo, section=FileSection.RESUMES)
    return service, file_repo, quota_repo, storage


def _client(
    file_service: FileService,
    quota_repo: FakeQuotaRepo,
    *,
    role: Role = Role.FAMILY,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resume_router)
    app.include_router(files_router)

    async def _current_user() -> User:
        return _user(role)

    async def _resumes_file_service() -> FileService:
        return file_service

    async def _session():
        yield FakeSession()

    def _quota_repo() -> FakeQuotaRepo:
        return quota_repo

    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_resumes_file_service] = _resumes_file_service
    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_quota_repository] = _quota_repo
    return TestClient(app)


def _seed_tree(client: TestClient) -> None:
    assert client.post("/api/resumes/tree", json={"path": "", "name": "Germany"}).status_code == 201
    assert (
        client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"}).status_code
        == 201
    )
    assert (
        client.post(
            "/api/resumes/tree",
            json={"path": "Germany/Acme", "name": "Backend Engineer"},
        ).status_code
        == 201
    )


async def _chunks(data: bytes):
    yield data


def test_stranger_is_denied_on_every_resumes_route() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo, role=Role.STRANGER)

    responses = [
        client.get("/api/resumes/tree"),
        client.post("/api/resumes/tree", json={"path": "", "name": "Germany"}),
        client.patch("/api/resumes/tree", json={"path": "Germany", "new_name": "Poland"}),
        client.delete("/api/resumes/tree", params={"path": "Germany"}),
        client.get("/api/resumes/vacancy", params={"path": "a/b/c"}),
        client.put(
            "/api/resumes/vacancy",
            params={"path": "a/b/c"},
            json={"website_url": "", "status_id": None, "fields": []},
        ),
        client.get("/api/resumes/vacancies"),
        client.get("/api/resumes/statuses"),
        client.put("/api/resumes/statuses", json={"statuses": []}),
        client.get("/api/resumes/files", params={"path": "/"}),
        client.post("/api/resumes/files/mkdir", json={"path": "", "name": "x"}),
    ]

    for response in responses:
        assert response.status_code == 403, response.request.url
        assert response.json()["detail"]["error_code"] == ErrorCode.ACCESS_DENIED


@pytest.mark.parametrize("role", [Role.FAMILY, Role.ADMIN])
def test_family_and_admin_reach_the_resumes_routes(role: Role) -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo, role=role)

    assert client.get("/api/resumes/tree").status_code == 200
    assert client.get("/api/resumes/vacancies").status_code == 200
    assert client.get("/api/resumes/statuses").status_code == 200
    assert client.get("/api/resumes/files", params={"path": "/"}).status_code == 200


def test_first_statuses_get_seeds_defaults_and_second_get_keeps_them() -> None:
    file_service, file_repo, quota_repo, storage = _file_service()
    client = _client(file_service, quota_repo)

    first = client.get("/api/resumes/statuses")
    assert first.status_code == 200
    seeded = first.json()["statuses"]
    assert [item["name"] for item in seeded] == ["Applied", "Interview", "Offer", "Rejected"]
    assert [item["color"] for item in seeded] == ["#38BDF8", "#A78BFA", "#34D399", "#F87171"]
    assert all(len(item["id"]) == 32 for item in seeded)

    second = client.get("/api/resumes/statuses")
    assert second.json()["statuses"] == seeded

    disk_path = storage.to_disk_relative_path(STATUSES_FILENAME)
    assert len(file_repo.committed_for_path(disk_path)) == 1


def test_status_ids_survive_rename_and_recolor() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    seeded = client.get("/api/resumes/statuses").json()["statuses"]
    applied = seeded[0]

    updated = client.put(
        "/api/resumes/statuses",
        json={
            "statuses": [
                {"id": applied["id"], "name": "Submitted", "color": "#123456"},
                {"name": "Screening", "color": "#ABCDEF"},
            ],
        },
    )
    assert updated.status_code == 200
    statuses = updated.json()["statuses"]
    assert statuses[0]["id"] == applied["id"]
    assert statuses[0]["name"] == "Submitted"
    assert statuses[0]["color"] == "#123456"
    assert len(statuses[1]["id"]) == 32

    assert client.get("/api/resumes/statuses").json()["statuses"] == statuses


def test_status_validation_rejects_empty_duplicate_and_bad_color() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    empty = client.put(
        "/api/resumes/statuses",
        json={"statuses": [{"name": "  ", "color": "#123456"}]},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["error_code"] == ErrorCode.RESUME_STATUS_INVALID

    duplicate = client.put(
        "/api/resumes/statuses",
        json={
            "statuses": [
                {"name": "Applied", "color": "#123456"},
                {"name": "applied", "color": "#654321"},
            ],
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"]["error_code"] == ErrorCode.RESUME_STATUS_INVALID

    bad_color = client.put(
        "/api/resumes/statuses",
        json={"statuses": [{"name": "Applied", "color": "blue"}]},
    )
    assert bad_color.status_code == 400
    assert bad_color.json()["detail"]["error_code"] == ErrorCode.RESUME_STATUS_INVALID


def test_create_rename_and_delete_at_every_level() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    country = client.post("/api/resumes/tree", json={"path": "", "name": " Germany "})
    assert country.status_code == 201
    assert country.json()["name"] == "Germany"
    assert country.json()["level"] == "COUNTRY"
    assert country.json()["path"] == "Germany"

    company = client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"})
    assert company.status_code == 201
    assert company.json()["level"] == "COMPANY"
    assert company.json()["path"] == "Germany/Acme"

    vacancy = client.post(
        "/api/resumes/tree",
        json={"path": "Germany/Acme", "name": "Backend Engineer"},
    )
    assert vacancy.status_code == 201
    assert vacancy.json()["level"] == "VACANCY"
    assert vacancy.json()["status_id"] is None
    assert vacancy.json()["website_url"] == ""

    root = client.get("/api/resumes/tree")
    assert root.json()["level"] == "COUNTRY"
    assert [item["name"] for item in root.json()["items"]] == ["Germany"]
    assert root.json()["items"][0]["child_count"] == 1

    vacancies = client.get("/api/resumes/tree", params={"path": "Germany/Acme"})
    assert vacancies.json()["level"] == "VACANCY"
    assert [item["name"] for item in vacancies.json()["items"]] == ["Backend Engineer"]

    renamed = client.patch(
        "/api/resumes/tree",
        json={"path": "Germany/Acme/Backend Engineer", "new_name": "Senior Backend"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["path"] == "Germany/Acme/Senior Backend"

    renamed_country = client.patch(
        "/api/resumes/tree",
        json={"path": "Germany", "new_name": "Poland"},
    )
    assert renamed_country.status_code == 200
    assert renamed_country.json()["path"] == "Poland"

    assert client.get(
        "/api/resumes/vacancy",
        params={"path": "Poland/Acme/Senior Backend"},
    ).status_code == 200

    deleted = client.delete(
        "/api/resumes/tree",
        params={"path": "Poland/Acme/Senior Backend"},
    )
    assert deleted.status_code == 204
    assert client.get("/api/resumes/tree", params={"path": "Poland/Acme"}).json()["items"] == []

    assert client.delete("/api/resumes/tree", params={"path": "Poland"}).status_code == 204
    assert client.get("/api/resumes/tree").json()["items"] == []


def test_duplicate_sibling_name_is_conflict() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    assert client.post("/api/resumes/tree", json={"path": "", "name": "Germany"}).status_code == 201
    duplicate = client.post("/api/resumes/tree", json={"path": "", "name": "germany"})

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error_code"] == ErrorCode.RESUME_NODE_EXISTS


@pytest.mark.parametrize(
    "name",
    ["", "   ", ".", "..", "a/b", "a\\b", "meta.yaml", "statuses.yaml", "x" * 129],
)
def test_invalid_and_reserved_names_are_rejected(name: str) -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    response = client.post("/api/resumes/tree", json={"path": "", "name": name})

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_NAME_INVALID


def test_create_below_a_vacancy_is_depth_invalid() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)

    response = client.post(
        "/api/resumes/tree",
        json={"path": "Germany/Acme/Backend Engineer", "name": "Attachments"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_DEPTH_INVALID


def test_vacancy_create_with_unknown_status_is_rejected() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    assert client.post("/api/resumes/tree", json={"path": "", "name": "Germany"}).status_code == 201
    assert (
        client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"}).status_code
        == 201
    )

    response = client.post(
        "/api/resumes/tree",
        json={"path": "Germany/Acme", "name": "Backend", "status_id": "deadbeef"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_STATUS_INVALID


def test_vacancy_create_stores_the_chosen_status() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    status_id = client.get("/api/resumes/statuses").json()["statuses"][0]["id"]
    assert client.post("/api/resumes/tree", json={"path": "", "name": "Germany"}).status_code == 201
    assert (
        client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"}).status_code
        == 201
    )

    created = client.post(
        "/api/resumes/tree",
        json={"path": "Germany/Acme", "name": "Backend", "status_id": status_id},
    )

    assert created.status_code == 201
    assert created.json()["status_id"] == status_id
    listed = client.get("/api/resumes/tree", params={"path": "Germany/Acme"}).json()["items"]
    assert listed[0]["status_id"] == status_id
    assert client.get(
        "/api/resumes/vacancy",
        params={"path": "Germany/Acme/Backend"},
    ).json()["status_id"] == status_id


def test_vacancy_meta_round_trip_normalizes_url_and_keeps_field_order() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)
    path = "Germany/Acme/Backend Engineer"

    saved = client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={
            "website_url": "example.com/jobs/42",
            "status_id": None,
            "fields": [
                {"name": "Salary", "value": "4000 EUR"},
                {"name": "Recruiter", "value": "jane@example.com"},
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["website_url"] == "https://example.com/jobs/42"

    loaded = client.get("/api/resumes/vacancy", params={"path": path})
    assert loaded.status_code == 200
    assert loaded.json()["name"] == "Backend Engineer"
    assert [item["name"] for item in loaded.json()["fields"]] == ["Salary", "Recruiter"]
    assert loaded.json()["fields"][0]["value"] == "4000 EUR"

    kept = client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={
            "website_url": "http://example.com",
            "status_id": None,
            "fields": [],
        },
    )
    assert kept.json()["website_url"] == "http://example.com"

    emptied = client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={"website_url": "  ", "status_id": None, "fields": []},
    )
    assert emptied.json()["website_url"] == ""
    assert client.get("/api/resumes/vacancy", params={"path": path}).json()["fields"] == []


def test_empty_and_duplicate_field_names_are_rejected() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)
    path = "Germany/Acme/Backend Engineer"

    empty = client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={"website_url": "", "status_id": None, "fields": [{"name": " ", "value": "x"}]},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["error_code"] == ErrorCode.RESUME_FIELD_INVALID

    duplicate = client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={
            "website_url": "",
            "status_id": None,
            "fields": [
                {"name": "Salary", "value": "1"},
                {"name": "salary", "value": "2"},
            ],
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"]["error_code"] == ErrorCode.RESUME_FIELD_INVALID


def test_vacancy_endpoints_reject_non_vacancy_paths() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)

    response = client.get("/api/resumes/vacancy", params={"path": "Germany/Acme"})

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_DEPTH_INVALID


def test_unknown_node_returns_not_found() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    response = client.get("/api/resumes/tree", params={"path": "Nowhere"})

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == ErrorCode.FILE_NOT_FOUND


def test_deleting_a_status_detaches_it_without_rewriting_the_vacancy() -> None:
    file_service, _, quota_repo, storage = _file_service()
    client = _client(file_service, quota_repo)
    status_id = client.get("/api/resumes/statuses").json()["statuses"][0]["id"]
    _seed_tree(client)
    path = "Germany/Acme/Backend Engineer"
    client.put(
        "/api/resumes/vacancy",
        params={"path": path},
        json={"website_url": "", "status_id": status_id, "fields": []},
    )
    meta_path = f"{path}/{VACANCY_META_FILENAME}"
    before = storage._files[meta_path]

    remaining = client.get("/api/resumes/statuses").json()["statuses"][1:]
    assert client.put("/api/resumes/statuses", json={"statuses": remaining}).status_code == 200

    loaded = client.get("/api/resumes/vacancy", params={"path": path})
    assert loaded.status_code == 200
    assert loaded.json()["status_id"] == status_id
    assert status_id not in {
        item["id"] for item in client.get("/api/resumes/statuses").json()["statuses"]
    }

    assert storage._files[meta_path] == before


@pytest.mark.asyncio
async def test_invalid_statuses_yaml_returns_409_and_keeps_the_file() -> None:
    file_service, _, quota_repo, storage = _file_service()
    invalid = b"- not a mapping\n"
    await storage.write(STATUSES_FILENAME, _chunks(invalid), len(invalid))

    client = _client(file_service, quota_repo)
    response = client.get("/api/resumes/statuses")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_META_INVALID
    assert await storage.raw_bytes(STATUSES_FILENAME) == invalid


@pytest.mark.asyncio
async def test_invalid_meta_yaml_returns_409_and_keeps_the_file() -> None:
    file_service, _, quota_repo, storage = _file_service()
    path = "Germany/Acme/Backend Engineer"
    await storage.mkdir(path)
    invalid = b"website_url: [not, a, string]\n"
    await storage.write(f"{path}/{VACANCY_META_FILENAME}", _chunks(invalid), len(invalid))

    client = _client(file_service, quota_repo)
    response = client.get("/api/resumes/vacancy", params={"path": path})

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == ErrorCode.RESUME_META_INVALID
    assert await storage.raw_bytes(f"{path}/{VACANCY_META_FILENAME}") == invalid


@pytest.mark.asyncio
async def test_broken_yaml_is_reported_and_not_rewritten() -> None:
    file_service, _, _, storage = _file_service()
    broken = b"statuses: [\n"
    await storage.write(STATUSES_FILENAME, _chunks(broken), len(broken))
    service = ResumeService(file_service)

    with pytest.raises(ResumeMetaInvalidError):
        await service.list_statuses(USER_ID)

    assert await storage.raw_bytes(STATUSES_FILENAME) == broken


@pytest.mark.asyncio
async def test_repeated_meta_save_keeps_one_record_and_charges_the_delta() -> None:
    file_service, file_repo, quota_repo, storage = _file_service()
    service = ResumeService(file_service)
    await service.create_node(USER_ID, "", "Germany")
    await service.create_node(USER_ID, "Germany", "Acme")
    await service.create_node(USER_ID, "Germany/Acme", "Backend")
    path = "Germany/Acme/Backend"
    meta_disk_path = storage.to_disk_relative_path(f"{path}/{VACANCY_META_FILENAME}")

    await service.save_vacancy(USER_ID, path, "", None, [VacancyField(name="A", value="1")])
    after_first = quota_repo.total_bytes

    await service.save_vacancy(
        USER_ID,
        path,
        "https://example.com",
        None,
        [VacancyField(name="A", value="a much longer value than before")],
    )

    records = file_repo.committed_for_path(meta_disk_path)
    assert len(records) == 1
    assert quota_repo.total_bytes == records[0].size_bytes
    assert quota_repo.total_bytes > after_first


@pytest.mark.asyncio
async def test_recursive_delete_releases_records_and_quota() -> None:
    file_service, file_repo, quota_repo, storage = _file_service()
    service = ResumeService(file_service)
    await service.create_node(USER_ID, "", "Germany")
    await service.create_node(USER_ID, "Germany", "Acme")
    await service.create_node(USER_ID, "Germany/Acme", "Backend")
    payload = b"attachment-bytes"
    await file_service.upload_file(
        user_id=USER_ID,
        path="Germany/Acme/Backend",
        filename="cv.pdf",
        data=_chunks(payload),
        size=len(payload),
        section=FileSection.RESUMES,
    )
    assert quota_repo.total_bytes > 0
    assert len(file_repo.committed()) == 2

    await service.delete_node(USER_ID, "Germany")

    assert file_repo.committed() == []
    assert quota_repo.total_bytes == 0
    assert not await storage.exists("Germany")


async def test_recursive_delete_releases_archived_records_and_quota() -> None:
    file_service, file_repo, quota_repo, storage = _file_service()
    service = ResumeService(file_service)
    await service.create_node(USER_ID, "", "Germany")
    await service.create_node(USER_ID, "Germany", "Acme")
    await service.create_node(USER_ID, "Germany/Acme", "Backend")
    payload = b"attachment-bytes"
    record = await file_service.upload_file(
        user_id=USER_ID,
        path="Germany/Acme/Backend",
        filename="cv.pdf",
        data=_chunks(payload),
        size=len(payload),
        section=FileSection.RESUMES,
    )
    archived = FileRecord(
        **{
            **record.__dict__,
            "status": FileStatus.ARCHIVED,
            "is_archived": True,
            "archive_path": f"{record.relative_path}.zst",
        }
    )
    file_repo._replace(archived)
    deleted_archives: list[str] = []

    async def _fake_delete_archived_blob(target: FileRecord) -> None:
        deleted_archives.append(target.archive_path)

    file_service._delete_archived_file = _fake_delete_archived_blob
    quota_before = quota_repo.total_bytes
    assert quota_before >= len(payload)

    await service.delete_node(USER_ID, "Germany")

    assert deleted_archives == [f"{record.relative_path}.zst"]
    assert quota_repo.total_bytes == 0
    assert not await storage.exists("Germany")


def test_flat_vacancy_list_is_empty_on_a_fresh_tree() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)

    response = client.get("/api/resumes/vacancies")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_flat_vacancy_list_spans_countries_and_companies() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    statuses = client.get("/api/resumes/statuses").json()["statuses"]
    applied = statuses[0]["id"]

    client.post("/api/resumes/tree", json={"path": "", "name": "Germany"})
    client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"})
    client.post(
        "/api/resumes/tree",
        json={"path": "Germany/Acme", "name": "Backend Engineer", "status_id": applied},
    )
    client.post("/api/resumes/tree", json={"path": "Germany/Acme", "name": "Data Engineer"})
    client.post("/api/resumes/tree", json={"path": "", "name": "Poland"})
    client.post("/api/resumes/tree", json={"path": "Poland", "name": "Globex"})
    client.post("/api/resumes/tree", json={"path": "Poland/Globex", "name": "SRE"})

    items = client.get("/api/resumes/vacancies").json()["items"]

    assert [(item["country"], item["company"], item["name"]) for item in items] == [
        ("Germany", "Acme", "Backend Engineer"),
        ("Germany", "Acme", "Data Engineer"),
        ("Poland", "Globex", "SRE"),
    ]
    assert items[0]["path"] == "Germany/Acme/Backend Engineer"
    assert items[0]["status_id"] == applied
    assert items[1]["status_id"] is None


def test_flat_vacancy_list_skips_empty_countries_and_companies() -> None:
    file_service, _, quota_repo, _ = _file_service()
    client = _client(file_service, quota_repo)
    client.post("/api/resumes/tree", json={"path": "", "name": "Germany"})
    client.post("/api/resumes/tree", json={"path": "Germany", "name": "Acme"})
    client.post("/api/resumes/tree", json={"path": "", "name": "Spain"})

    assert client.get("/api/resumes/vacancies").json()["items"] == []


def test_flat_vacancy_list_reports_website_url_and_survives_invalid_meta() -> None:
    file_service, _, quota_repo, storage = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)
    client.put(
        "/api/resumes/vacancy",
        params={"path": "Germany/Acme/Backend Engineer"},
        json={"website_url": "example.com/jobs/1", "status_id": None, "fields": []},
    )

    items = client.get("/api/resumes/vacancies").json()["items"]
    assert items[0]["website_url"] == "https://example.com/jobs/1"

    storage._files["Germany/Acme/Backend Engineer/meta.yaml"] = b"- broken\n"

    degraded = client.get("/api/resumes/vacancies")
    assert degraded.status_code == 200
    assert degraded.json()["items"][0]["status_id"] is None
    assert degraded.json()["items"][0]["website_url"] == ""


def test_resumes_files_router_upload_list_and_delete_round_trip() -> None:
    file_service, file_repo, quota_repo, storage = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)
    path = "Germany/Acme/Backend Engineer"

    uploaded = client.post(
        f"/api/resumes/files/upload?path={path}",
        files={"file": ("cv.pdf", b"resume-bytes", "application/pdf")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["section"] == FileSection.RESUMES
    assert quota_repo.total_bytes >= len(b"resume-bytes")

    listed = client.get("/api/resumes/files", params={"path": path})
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert names == {VACANCY_META_FILENAME, "cv.pdf"}

    made = client.post("/api/resumes/files/mkdir", json={"path": path, "name": "Offers"})
    assert made.status_code == 201

    downloaded = client.get("/api/resumes/files/download", params={"path": f"{path}/cv.pdf"})
    assert downloaded.status_code == 200
    assert downloaded.content == b"resume-bytes"

    deleted = client.delete("/api/resumes/files", params={"path": f"{path}/cv.pdf"})
    assert deleted.status_code == 204
    assert not storage._files.get(f"{path}/cv.pdf")
    disk_path = storage.to_disk_relative_path(f"{path}/cv.pdf")
    assert file_repo.committed_for_path(disk_path) == []


def test_vacancy_listing_survives_an_invalid_meta_file() -> None:
    file_service, _, quota_repo, storage = _file_service()
    client = _client(file_service, quota_repo)
    _seed_tree(client)
    path = "Germany/Acme/Backend Engineer"
    storage._files[f"{path}/{VACANCY_META_FILENAME}"] = b"- broken\n"

    listed = client.get("/api/resumes/tree", params={"path": "Germany/Acme"})

    assert listed.status_code == 200
    assert listed.json()["items"][0]["status_id"] is None


@pytest.mark.asyncio
async def test_service_seeds_statuses_only_once() -> None:
    file_service, file_repo, _, storage = _file_service()
    service = ResumeService(file_service)

    first = await service.list_statuses(USER_ID)
    second = await service.list_statuses(USER_ID)

    assert first == second
    assert [item.name for item in first] == ["Applied", "Interview", "Offer", "Rejected"]
    disk_path = storage.to_disk_relative_path(STATUSES_FILENAME)
    assert len(file_repo.committed_for_path(disk_path)) == 1


@pytest.mark.asyncio
async def test_saved_statuses_generate_ids_when_missing() -> None:
    file_service, _, _, _ = _file_service()
    service = ResumeService(file_service)

    saved = await service.save_statuses(
        USER_ID,
        [ResumeStatus(id="", name="Applied", color="#38BDF8")],
    )

    assert len(saved) == 1
    assert len(saved[0].id) == 32
    assert await service.list_statuses(USER_ID) == saved
