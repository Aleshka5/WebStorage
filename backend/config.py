from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
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


class RagSettings(BaseSettings):
    """RAG edge used to index Files and Photos. Off until the app joins rag_edge."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    enabled: bool = Field(
        default=False,
        validation_alias="RAG_ENABLED",
        description=(
            "Master switch. Off: no rows are created, no calls leave the app, "
            "and /api/photos/search answers 503 INDEXING_UNAVAILABLE."
        ),
    )
    base_url: str = Field(
        default="http://rag-edge:8080",
        validation_alias="RAG_BASE_URL",
        description="RAG edge base URL. One trailing slash is stripped.",
    )
    timeout_seconds: int = Field(
        default=10,
        validation_alias="RAG_TIMEOUT_SECONDS",
        description="Per-call timeout for RAG HTTP requests, in seconds.",
    )
    sync_interval_seconds: int = Field(
        default=60,
        validation_alias="RAG_SYNC_INTERVAL_SECONDS",
        description="index_sync job interval, in seconds.",
    )
    index_batch_size: int = Field(
        default=20,
        validation_alias="RAG_INDEX_BATCH_SIZE",
        description="Maximum index rows processed per index_sync tick.",
    )
    max_attempts: int = Field(
        default=8,
        validation_alias="RAG_MAX_ATTEMPTS",
        description="Failed attempts before a row becomes FAILED.",
    )
    index_max_wait_seconds: int = Field(
        default=3600,
        validation_alias="RAG_INDEX_MAX_WAIT_SECONDS",
        description="How long a SUBMITTED row may wait for ready, in seconds.",
    )
    max_image_bytes: int = Field(
        default=10_485_760,
        validation_alias="RAG_MAX_IMAGE_BYTES",
        description=(
            "Mirrors RAG EMBEDDING_MAX_IMAGE_BYTES. Above it, Photos uses the "
            "JPEG preview and Files images are skipped."
        ),
    )
    backfill_enabled: bool = Field(
        default=False,
        validation_alias="RAG_BACKFILL_ENABLED",
        description="Run one backfill pass at startup.",
    )

    @field_validator("base_url")
    @classmethod
    def strip_one_trailing_slash(cls, value: str) -> str:
        if value.endswith("/"):
            return value[:-1]
        return value


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
        self.rag = RagSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
