import pytest

from config import get_settings

AUTH_GRPC_ENV_KEYS = (
    "AUTH_GRPC_ADDR",
    "AUTH_CALLER_HOST",
    "AUTH_COOKIE_NAME",
    "AUTH_GRPC_TIMEOUT_MS",
    "AUTH_LOGIN_URL",
    "AUTH_LOGOUT_URL",
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auth_grpc_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in AUTH_GRPC_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()

    assert settings.auth_grpc.addr == "api:9090"
    assert settings.auth_grpc.caller_host == "storage.filenkov.store"
    assert settings.auth_grpc.cookie_name == "auth_session"
    assert settings.auth_grpc.timeout_ms == 2000
    assert (
        settings.auth_grpc.login_url
        == "https://filenkov.store/oauth/google?return_to=https://storage.filenkov.store/"
    )
    assert settings.auth_grpc.logout_url == "http://api:8080"


def test_auth_grpc_settings_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_GRPC_ADDR", "auth-api:9090")
    monkeypatch.setenv("AUTH_CALLER_HOST", "storage.example.test")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "session_id")
    monkeypatch.setenv("AUTH_GRPC_TIMEOUT_MS", "3500")
    monkeypatch.setenv(
        "AUTH_LOGIN_URL",
        "https://hub.example.test/oauth/google?return_to=https://storage.example.test/",
    )
    monkeypatch.setenv("AUTH_LOGOUT_URL", "http://auth-api:8080")

    settings = get_settings()

    assert settings.auth_grpc.addr == "auth-api:9090"
    assert settings.auth_grpc.caller_host == "storage.example.test"
    assert settings.auth_grpc.cookie_name == "session_id"
    assert settings.auth_grpc.timeout_ms == 3500
    assert (
        settings.auth_grpc.login_url
        == "https://hub.example.test/oauth/google?return_to=https://storage.example.test/"
    )
    assert settings.auth_grpc.logout_url == "http://auth-api:8080"


def test_auth_grpc_keeps_existing_jwt_auth_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("AUTH_GRPC_ADDR", "api:9090")

    settings = get_settings()

    assert settings.auth.jwt_secret == "test-jwt-secret"
    assert settings.auth_grpc.addr == "api:9090"


def test_get_settings_returns_cached_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_GRPC_ADDR", "api:9090")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.auth_grpc is second.auth_grpc
