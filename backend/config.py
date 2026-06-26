from functools import lru_cache

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

    disks: str = Field(default="disk1", validation_alias="STORAGE_DISKS")
    root: str = Field(default="/storage", validation_alias="STORAGE_ROOT")
    disk_strategy: str = Field(default="most_free_space", validation_alias="DISK_STRATEGY")
    disk_space_cache_ttl: int = Field(default=30, validation_alias="DISK_SPACE_CACHE_TTL")
    min_free_space_mb: int = Field(default=500, validation_alias="MIN_FREE_SPACE_MB")


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


class Settings:
    def __init__(self) -> None:
        self.database = DatabaseSettings()
        self.cache_db = CacheDBSettings()
        self.storage = StorageSettings()
        self.auth = AuthSettings()
        self.business_logic = BusinessLogicSettings()
        self.admin = AdminSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
