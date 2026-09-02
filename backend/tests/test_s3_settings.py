import pytest

from config import S3Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_storage_settings_have_no_backend_or_root() -> None:
    settings = get_settings()

    assert not hasattr(settings.storage, "backend")
    assert not hasattr(settings.storage, "root")
    assert not hasattr(settings.storage, "disks")
    assert settings.s3.bucket == "storage"


def test_s3_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_REGION",
        "S3_USE_SSL",
        "S3_BUCKET",
        "S3_PATH_STYLE",
    ):
        monkeypatch.delenv(key, raising=False)

    s3 = S3Settings(_env_file=None)

    assert s3.endpoint_url == ""
    assert s3.access_key == ""
    assert s3.secret_key == ""
    assert s3.region == "us-east-1"
    assert s3.use_ssl is False
    assert s3.bucket == "storage"
    assert s3.path_style is True


def test_s3_settings_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_REGION", "eu-central-1")
    monkeypatch.setenv("S3_USE_SSL", "true")
    monkeypatch.setenv("S3_BUCKET", "storage")
    monkeypatch.setenv("S3_PATH_STYLE", "false")

    settings = get_settings()

    assert settings.s3.endpoint_url == "http://minio:9000"
    assert settings.s3.access_key == "minioadmin"
    assert settings.s3.secret_key == "minioadmin"
    assert settings.s3.region == "eu-central-1"
    assert settings.s3.use_ssl is True
    assert settings.s3.bucket == "storage"
    assert settings.s3.path_style is False


def test_get_settings_returns_cached_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")

    first = get_settings()
    second = get_settings()

    assert first is second
