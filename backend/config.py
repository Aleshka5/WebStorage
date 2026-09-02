from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    url: str = Field(
        default="postgresql+asyncpg://storage:changeme@postgres:5432/db_storage",
        validation_alias="DATABASE_URL",
    )


class CacheDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(default="redis://redis:6379", validation_alias="REDIS_URL")


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    disk_strategy: str = Field(default="most_free_space", validation_alias="DISK_STRATEGY")
    disk_space_cache_ttl: int = Field(default=30, validation_alias="DISK_SPACE_CACHE_TTL")
    min_free_space_mb: int = Field(default=500, validation_alias="MIN_FREE_SPACE_MB")


class S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    endpoint_url: str = Field(
        default="",
        validation_alias="S3_ENDPOINT_URL",
        description="S3-compatible API endpoint (e.g. http://minio:9000).",
    )
    access_key: str = Field(default="", validation_alias="S3_ACCESS_KEY")
    secret_key: str = Field(default="", validation_alias="S3_SECRET_KEY")
    region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    use_ssl: bool = Field(default=False, validation_alias="S3_USE_SSL")
    bucket: str = Field(
        default="storage",
        validation_alias="S3_BUCKET",
        description="Single MinIO bucket for all object keys (users/, shared/, _meta/backups/).",
    )
    path_style: bool = Field(
        default=True,
        validation_alias="S3_PATH_STYLE",
        description="Use path-style addressing (required for typical MinIO setups).",
    )


class AuthSettings(BaseSettings):
    """Vault cookie only. Identity comes from gateway headers / User-Service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cookie_name: str = Field(
        default="auth_session",
        validation_alias="AUTH_COOKIE_NAME",
        description="Companion cookie used as the private-vault Redis key (not identity).",
    )
    private_session_ttl_hours: int = Field(
        default=4,
        validation_alias="PRIVATE_SESSION_TTL_HOURS",
    )


class UserServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    url: str = Field(
        default="http://user_service:8000",
        validation_alias="USER_SERVICE_URL",
        description="User-Service base URL on user_network. No JWT.",
    )
    timeout_ms: int = Field(
        default=2000,
        validation_alias="USER_SERVICE_TIMEOUT_MS",
        description="HTTP timeout for User-Service calls.",
    )
    cache_ttl_seconds: int = Field(
        default=60,
        validation_alias="USER_SERVICE_CACHE_TTL_SECONDS",
        description="TTL for cached GET role / GET user reads.",
    )


class BusinessLogicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    photo_batch_size: int = Field(default=30, validation_alias="PHOTO_BATCH_SIZE")
    thumbnail_max_px: int = Field(default=400, validation_alias="THUMBNAIL_MAX_PX")
    default_user_quota_mb: int = Field(
        default=100,
        validation_alias=AliasChoices("DEFAULT_USER_QUOTA_MB", "STRANGER_QUOTA_MB"),
        description=(
            "Default total quota for a new user_quota_usage row (all roles). "
            "STRANGER_QUOTA_MB is a deprecated alias."
        ),
    )
    archive_days_threshold: int = Field(default=180, validation_alias="ARCHIVE_DAYS_THRESHOLD")

    @property
    def default_user_quota_bytes(self) -> int:
        return self.default_user_quota_mb * 1024 * 1024


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    file_enabled: bool = Field(default=False, validation_alias="LOG_FILE_ENABLED")
    file_path: str = Field(default="/var/log/homecloud/app.log", validation_alias="LOG_FILE_PATH")
    file_rotation: str = Field(default="100 MB", validation_alias="LOG_FILE_ROTATION")
    file_retention: str = Field(default="30 days", validation_alias="LOG_FILE_RETENTION")


class SpaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    static_dir: str = Field(
        default="/opt/spa",
        validation_alias="SPA_STATIC_DIR",
        description="Built Vite dist served by FastAPI when the directory exists.",
    )


class Settings:
    def __init__(self) -> None:
        self.database = DatabaseSettings()
        self.cache_db = CacheDBSettings()
        self.storage = StorageSettings()
        self.s3 = S3Settings()
        self.auth = AuthSettings()
        self.user_service = UserServiceSettings()
        self.business_logic = BusinessLogicSettings()
        self.logging = LoggingSettings()
        self.spa = SpaSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
