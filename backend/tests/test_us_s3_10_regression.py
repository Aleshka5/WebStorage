"""US-S3-10: regression coverage for core paths on S3.

Service-level / adapter-level integration against moto (no Docker MinIO, no
full HTTP ASGI client). Public API contracts are unchanged — these tests
exercise the same StorageAdapter / FileService / PhotoService / DiskRouter
surfaces that the routers call.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import aioboto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from moto.server import ThreadedMotoServer
from PIL import Image

from app.application.archive_service import ARCHIVE_EXTENSION, ArchiveService
from app.application.archived_file_reader import stream_decompressed_archived
from app.application.file_service import FileService
from app.application.photo_service import PREVIEWS_DIR, PhotoService
from app.domain.entities.file_record import FileRecord, FileSection, FileStatus
from app.infrastructure.archive_manager import ArchiveManager
from app.infrastructure.disk_router import (
    DISK_STATUS_HEALTHY,
    DISK_STATUS_LOW_SPACE,
    DISK_STATUS_UNAVAILABLE,
    DiskRouter,
)
from app.infrastructure.storage.encrypted_adapter import (
    EncryptedStorageAdapter,
    derive_encryption_key,
)
from app.infrastructure.storage.s3_adapter import (
    S3StorageAdapter,
    build_disk_root_adapter,
    create_storage_adapter,
)
from app.infrastructure.thumbnail_service import ThumbnailService
from config import get_settings

DISK_ID = "storage"
BUCKET = "storage"
USER_ID = uuid4()


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _png_bytes(color: tuple[int, int, int] = (32, 160, 64), size: tuple[int, int] = (64, 48)) -> bytes:
    image = Image.new("RGB", size, color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _make_record(
    *,
    relative_path: str,
    status: FileStatus = FileStatus.COMMITTED,
    archive_path: str | None = None,
    section: FileSection = FileSection.FILES,
    original_name: str = "doc.txt",
    size_bytes: int = 12,
    mime_type: str = "text/plain",
    is_encrypted: bool = False,
    file_id: UUID | None = None,
    checksum_sha256: str | None = None,
) -> FileRecord:
    now = datetime.now(tz=UTC)
    return FileRecord(
        id=file_id or uuid4(),
        user_id=USER_ID,
        disk_id=DISK_ID,
        relative_path=relative_path,
        original_name=original_name,
        size_bytes=size_bytes,
        mime_type=mime_type,
        is_encrypted=is_encrypted,
        section=section,
        status=status,
        checksum_sha256=checksum_sha256,
        created_at=now - timedelta(days=40),
        last_accessed_at=now - timedelta(days=40),
        is_archived=status == FileStatus.ARCHIVED,
        archive_path=archive_path,
    )


@pytest.fixture(scope="module")
def moto_endpoint() -> str:
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    endpoint = f"http://{host}:{port}"
    yield endpoint
    server.stop()


@pytest.fixture
async def s3_env(moto_endpoint: str, monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("S3_ENDPOINT_URL", moto_endpoint)
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_PATH_STYLE", "true")
    monkeypatch.setenv("MIN_FREE_SPACE_MB", "500")
    monkeypatch.setenv("DISK_SPACE_CACHE_TTL", "30")
    get_settings.cache_clear()

    bucket = BUCKET
    session = aioboto3.Session()
    config = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=moto_endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
        config=config,
    ) as client:
        await client.create_bucket(Bucket=bucket)

    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Files: upload → list → download → delete (FileService + S3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_files_upload_list_download_delete_on_s3(s3_env: None) -> None:
    root_prefix = f"users/{USER_ID}/files"
    adapter = create_storage_adapter(disk_id=DISK_ID, root_prefix=root_prefix)
    assert isinstance(adapter, S3StorageAdapter)

    await adapter.mkdir(".tmp")
    await adapter.mkdir("docs")

    file_id = uuid4()
    payload = b"us-s3-10-files-payload"
    pending = _make_record(
        relative_path=f"{root_prefix}/docs/note.txt",
        status=FileStatus.PENDING,
        original_name="note.txt",
        size_bytes=len(payload),
        file_id=file_id,
    )
    committed = _make_record(
        relative_path=f"{root_prefix}/docs/note.txt",
        status=FileStatus.COMMITTED,
        original_name="note.txt",
        size_bytes=len(payload),
        file_id=file_id,
        checksum_sha256="a" * 64,
    )

    file_repo = AsyncMock()
    file_repo.create.return_value = pending
    file_repo.update_status.return_value = committed
    file_repo.get_by_id.return_value = committed
    file_repo.touch_last_accessed.return_value = None
    file_repo.delete.return_value = True
    file_repo.list_archived_records.return_value = []

    quota_repo = AsyncMock()
    service = FileService(
        adapter=adapter,
        quota_repo=quota_repo,
        file_repo=file_repo,
        section=FileSection.FILES,
    )

    uploaded = await service.upload_file(
        USER_ID,
        "docs",
        "note.txt",
        _chunks(payload),
        len(payload),
        FileSection.FILES,
    )
    assert uploaded.id == file_id
    assert await adapter.exists("docs/note.txt")

    listed = await service.list_directory(USER_ID, "docs")
    names = {node.name for node in listed}
    assert "note.txt" in names

    downloaded = b"".join([chunk async for chunk in service.download_file(USER_ID, file_id)])
    assert downloaded == payload

    await service.delete_file(USER_ID, file_id)
    assert not await adapter.exists("docs/note.txt")
    file_repo.delete.assert_awaited_with(file_id)
    quota_repo.decrement.assert_awaited()


# ---------------------------------------------------------------------------
# Private: EncryptedStorageAdapter over S3 + marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_encrypted_roundtrip_and_marker_on_s3(s3_env: None) -> None:
    root_prefix = f"users/{USER_ID}/private"
    inner = create_storage_adapter(disk_id=DISK_ID, root_prefix=root_prefix)
    key = derive_encryption_key("us-s3-10-passphrase", USER_ID)
    adapter = EncryptedStorageAdapter(inner=inner, key=key)

    assert not await adapter.marker_exists()
    await adapter.write_marker()
    assert await adapter.marker_exists()
    assert await adapter.validate_marker()

    wrong = EncryptedStorageAdapter(
        inner=inner,
        key=derive_encryption_key("wrong-passphrase", USER_ID),
    )
    assert await wrong.marker_exists()
    assert not await wrong.validate_marker()

    await adapter.mkdir("vault")
    payload = b"secret-private-bytes"
    checksum = await adapter.write("vault/secret.bin", _chunks(payload), len(payload))
    assert len(checksum) == 64

    nodes = await adapter.list("vault")
    assert {n.name for n in nodes} == {"secret.bin"}

    read_back = b"".join([chunk async for chunk in adapter.read("vault/secret.bin")])
    assert read_back == payload

    # Ciphertext on inner must differ from plaintext logical payload.
    enc_nodes = await inner.list("")
    assert any(n.is_dir for n in enc_nodes)
    assert not await inner.exists("vault/secret.bin")

    await adapter.delete("vault/secret.bin")
    assert not await adapter.exists("vault/secret.bin")


# ---------------------------------------------------------------------------
# Photos: original + preview via adapter / ThumbnailService path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_photos_original_and_preview_on_s3(s3_env: None) -> None:
    root_prefix = f"users/{USER_ID}/photos"
    adapter = create_storage_adapter(disk_id=DISK_ID, root_prefix=root_prefix)
    await adapter.mkdir("originals")
    await adapter.mkdir("previews")

    png = _png_bytes()
    file_id = uuid4()
    relative_path = f"{root_prefix}/originals/{file_id}.png"
    pending = _make_record(
        relative_path="",
        status=FileStatus.PENDING,
        section=FileSection.PHOTOS,
        original_name="shot.png",
        size_bytes=len(png),
        mime_type="image/png",
        file_id=file_id,
    )
    committed = _make_record(
        relative_path=relative_path,
        status=FileStatus.COMMITTED,
        section=FileSection.PHOTOS,
        original_name="shot.png",
        size_bytes=len(png),
        mime_type="image/png",
        file_id=file_id,
        checksum_sha256="b" * 64,
    )

    file_repo = AsyncMock()
    file_repo.create.return_value = pending
    file_repo.update_status.return_value = committed
    file_repo.get_by_id.return_value = committed
    file_repo.delete.return_value = True

    service = PhotoService(
        adapter=adapter,
        thumbnail_service=ThumbnailService(),
        file_repo=file_repo,
        quota_repo=AsyncMock(),
        disk_router=DiskRouter(get_settings()),
    )

    uploaded = await service.upload_photo(USER_ID, "shot.png", _chunks(png), len(png))
    assert uploaded.id == file_id
    assert await adapter.exists(f"originals/{file_id}.png")

    # Deterministic preview path (upload schedules a background task; call directly).
    preview_path = f"{PREVIEWS_DIR}/{file_id}_thumb.jpg"
    await service._generate_preview(f"originals/{file_id}.png", preview_path)  # noqa: SLF001
    assert await adapter.exists(preview_path)

    preview_bytes, content_type = await service.get_preview(USER_ID, file_id)
    assert content_type == "image/jpeg"
    assert preview_bytes[:3] == b"\xff\xd8\xff"

    original = b"".join([chunk async for chunk in service.get_original(USER_ID, file_id)])
    assert original == png


# ---------------------------------------------------------------------------
# Archived file: compress + transparent read (US-S3-09 pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archived_file_compress_and_transparent_read_on_s3(s3_env: None) -> None:
    payload = b"archive-regression-payload"
    relative_path = f"users/{USER_ID}/files/docs/old.txt"
    adapter = build_disk_root_adapter(DISK_ID)
    await adapter.write(relative_path, _chunks(payload), len(payload))

    record = _make_record(relative_path=relative_path)
    archived = _make_record(
        relative_path=relative_path,
        status=FileStatus.ARCHIVED,
        archive_path=f"{relative_path}{ARCHIVE_EXTENSION}",
    )

    file_repo = AsyncMock()
    file_repo.list_candidates_for_archive.return_value = [record]
    file_repo.mark_archived.return_value = archived
    file_repo.list_archived_records.return_value = [archived]

    service = ArchiveService(
        file_repo=file_repo,
        archive_manager=ArchiveManager(),
        disk_router=DiskRouter(get_settings()),
        settings=get_settings(),
    )

    report = await service.run_daily_archive()
    assert report.processed == 1
    assert report.errors == 0
    assert not await adapter.exists(relative_path)
    assert await adapter.exists(f"{relative_path}{ARCHIVE_EXTENSION}")

    restored = b"".join(
        [chunk async for chunk in stream_decompressed_archived(archived, ArchiveManager())]
    )
    assert restored == payload


# ---------------------------------------------------------------------------
# Admin health: DiskRouter statuses (mocked S3 client)
# ---------------------------------------------------------------------------


def _client_error(code: str = "404") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "Not Found"}},
        "HeadBucket",
    )


@pytest.mark.asyncio
async def test_admin_health_statuses_on_s3(s3_env: None) -> None:
    """health_check returns HEALTHY | LOW_SPACE | UNAVAILABLE (admin API surface)."""
    # UNAVAILABLE
    bad_client = MagicMock()
    bad_client.head_bucket.side_effect = _client_error("404")
    unavailable_router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: bad_client,
    )
    assert unavailable_router.health_check() == {DISK_ID: DISK_STATUS_UNAVAILABLE}

    # LOW_SPACE via injectable capacity probe
    low_client = MagicMock()
    low_client.head_bucket.return_value = {}
    low_client.list_objects_v2.return_value = {
        "Contents": [{"Size": 150 * 1024 * 1024}],
        "IsTruncated": False,
    }

    def low_capacity(used_bytes: int) -> tuple[int, int]:
        total = 200 * 1024 * 1024
        return total, max(0, total - used_bytes)

    low_router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: low_client,
        s3_capacity_probe=low_capacity,
    )
    assert low_router.health_check() == {DISK_ID: DISK_STATUS_LOW_SPACE}

    # HEALTHY
    ok_client = MagicMock()
    ok_client.head_bucket.return_value = {}
    ok_client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

    def ok_capacity(used_bytes: int) -> tuple[int, int]:
        total = 10 * 1024 * 1024 * 1024
        return total, total - used_bytes

    ok_router = DiskRouter(
        get_settings(),
        s3_client_factory=lambda: ok_client,
        s3_capacity_probe=ok_capacity,
    )
    assert ok_router.health_check() == {DISK_ID: DISK_STATUS_HEALTHY}


@pytest.mark.asyncio
async def test_s3_blob_store_settings(s3_env: None) -> None:
    settings = get_settings()
    assert not hasattr(settings.storage, "backend")
    assert not hasattr(settings.storage, "root")
    assert settings.s3.bucket == BUCKET
