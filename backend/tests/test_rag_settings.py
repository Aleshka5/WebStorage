import pytest

from config import RagSettings, get_settings

_RAG_ENV_KEYS = (
    "RAG_ENABLED",
    "RAG_BASE_URL",
    "RAG_TIMEOUT_SECONDS",
    "RAG_SYNC_INTERVAL_SECONDS",
    "RAG_INDEX_BATCH_SIZE",
    "RAG_MAX_ATTEMPTS",
    "RAG_INDEX_MAX_WAIT_SECONDS",
    "RAG_MAX_IMAGE_BYTES",
    "RAG_BACKFILL_ENABLED",
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _clear_rag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _RAG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_rag_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_rag_env(monkeypatch)

    rag = RagSettings(_env_file=None)

    assert rag.enabled is False
    assert rag.base_url == "http://rag-edge:8080"
    assert rag.timeout_seconds == 10
    assert rag.sync_interval_seconds == 60
    assert rag.index_batch_size == 20
    assert rag.max_attempts == 8
    assert rag.index_max_wait_seconds == 3600
    assert rag.max_image_bytes == 10_485_760
    assert rag.backfill_enabled is False


def test_rag_enabled_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_rag_env(monkeypatch)
    # The settings models read `.env` from the working directory; a developer
    # machine has RAG_ENABLED=true there. Assert the default, not the local file.
    monkeypatch.chdir(tmp_path)

    settings = get_settings()

    assert settings.rag.enabled is False
    assert settings.rag.backfill_enabled is False


def test_rag_settings_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_BASE_URL", "http://rag.example:8080")
    monkeypatch.setenv("RAG_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("RAG_SYNC_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("RAG_INDEX_BATCH_SIZE", "5")
    monkeypatch.setenv("RAG_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RAG_INDEX_MAX_WAIT_SECONDS", "120")
    monkeypatch.setenv("RAG_MAX_IMAGE_BYTES", "2048")
    monkeypatch.setenv("RAG_BACKFILL_ENABLED", "true")

    settings = get_settings()

    assert settings.rag.enabled is True
    assert settings.rag.base_url == "http://rag.example:8080"
    assert settings.rag.timeout_seconds == 15
    assert settings.rag.sync_interval_seconds == 30
    assert settings.rag.index_batch_size == 5
    assert settings.rag.max_attempts == 3
    assert settings.rag.index_max_wait_seconds == 120
    assert settings.rag.max_image_bytes == 2048
    assert settings.rag.backfill_enabled is True


def test_rag_base_url_strips_one_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_BASE_URL", "http://rag-edge:8080/")

    settings = get_settings()

    assert settings.rag.base_url == "http://rag-edge:8080"


def test_get_settings_returns_cached_rag_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENABLED", "false")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.rag is second.rag
