from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    url: str = Field(
        default="postgresql+asyncpg://homecloud:changeme@db:5432/homecloud",
        validation_alias="DATABASE_URL",
    )


class CacheDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(default="redis://redis:6379", validation_alias="REDIS_URL")


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    backend: Literal["fs", "s3"] = Field(
        default="fs",
        validation_alias="STORAGE_BACKEND",
        description="Blob storage backend selector: 'fs' (local filesystem) or 's3' (MinIO/S3).",
    )
    disks: str = Field(default="disk1", validation_alias="STORAGE_DISKS")
    root: str = Field(default="/storage", validation_alias="STORAGE_ROOT")
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
    bucket_prefix: str = Field(
        default="",
        validation_alias="S3_BUCKET_PREFIX",
        description=(
            "Optional prefix for per-disk MinIO buckets, aligned 1:1 with STORAGE_DISKS. "
            "Bucket for a disk_id is `{prefix}{disk_id}` when prefix is set "
            "(e.g. prefix 'hc-' + disk 'disk1' → bucket 'hc-disk1'); "
            "when empty, each STORAGE_DISKS entry is used as the bucket name directly. "
            "Sticky FileRecord.disk_id placement is preserved — no rebalance across buckets."
        ),
    )
    path_style: bool = Field(
        default=True,
        validation_alias="S3_PATH_STYLE",
        description="Use path-style addressing (required for typical MinIO setups).",
    )


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:8000/api/auth/google/callback",
        validation_alias="GOOGLE_REDIRECT_URI",
    )
    frontend_url: str = Field(default="http://localhost:5173", validation_alias="FRONTEND_URL")
    jwt_secret: str = Field(default="change-me", validation_alias="JWT_SECRET")
    session_ttl_seconds: int = Field(default=86400, validation_alias="SESSION_TTL_SECONDS")
    private_session_ttl_hours: int = Field(
        default=4,
        validation_alias="PRIVATE_SESSION_TTL_HOURS",
    )


class BusinessLogicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    photo_batch_size: int = Field(default=30, validation_alias="PHOTO_BATCH_SIZE")
    thumbnail_max_px: int = Field(default=400, validation_alias="THUMBNAIL_MAX_PX")
    stranger_quota_mb: int = Field(default=100, validation_alias="STRANGER_QUOTA_MB")
    archive_days_threshold: int = Field(default=180, validation_alias="ARCHIVE_DAYS_THRESHOLD")


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    email: str = Field(default="", validation_alias="ADMIN_EMAIL")
    password: str = Field(default="", validation_alias="ADMIN_PASSWORD")


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    file_enabled: bool = Field(default=False, validation_alias="LOG_FILE_ENABLED")
    file_path: str = Field(default="/var/log/homecloud/app.log", validation_alias="LOG_FILE_PATH")
    file_rotation: str = Field(default="100 MB", validation_alias="LOG_FILE_ROTATION")
    file_retention: str = Field(default="30 days", validation_alias="LOG_FILE_RETENTION")


class Settings:
    def __init__(self) -> None:
        self.database = DatabaseSettings()
        self.cache_db = CacheDBSettings()
        self.storage = StorageSettings()
        self.s3 = S3Settings()
        self.auth = AuthSettings()
        self.business_logic = BusinessLogicSettings()
        self.admin = AdminSettings()
        self.logging = LoggingSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
