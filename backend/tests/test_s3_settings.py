import pytest

from config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_storage_backend_default_is_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)

    settings = get_settings()

    assert settings.storage.backend == "fs"


def test_storage_backend_s3_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")

    settings = get_settings()

    assert settings.storage.backend == "s3"


def test_s3_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_REGION",
        "S3_USE_SSL",
        "S3_BUCKET_PREFIX",
        "S3_PATH_STYLE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()

    assert settings.s3.endpoint_url == ""
    assert settings.s3.access_key == ""
    assert settings.s3.secret_key == ""
    assert settings.s3.region == "us-east-1"
    assert settings.s3.use_ssl is False
    assert settings.s3.bucket_prefix == ""
    assert settings.s3.path_style is True


def test_s3_settings_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_REGION", "eu-central-1")
    monkeypatch.setenv("S3_USE_SSL", "true")
    monkeypatch.setenv("S3_BUCKET_PREFIX", "hc-")
    monkeypatch.setenv("S3_PATH_STYLE", "false")

    settings = get_settings()

    assert settings.storage.backend == "s3"
    assert settings.s3.endpoint_url == "http://minio:9000"
    assert settings.s3.access_key == "minioadmin"
    assert settings.s3.secret_key == "minioadmin"
    assert settings.s3.region == "eu-central-1"
    assert settings.s3.use_ssl is True
    assert settings.s3.bucket_prefix == "hc-"
    assert settings.s3.path_style is False


def test_get_settings_returns_cached_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")

    first = get_settings()
    second = get_settings()

    assert first is second
