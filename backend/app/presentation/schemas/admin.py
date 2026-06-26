from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.admin_service import DiskStat, UserAdminView
from app.application.archive_service import ArchiveReport, ArchiveStats
from app.domain.entities.user import User
from app.domain.value_objects.role import Role


class UserAdminViewResponse(BaseModel):
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    quota_used_bytes: int = Field(ge=0)
    private_limit_bytes: int = Field(ge=0)

    @classmethod
    def from_view(cls, view: UserAdminView) -> "UserAdminViewResponse":
        return cls(
            id=view.id,
            email=view.email,
            role=view.role,
            is_active=view.is_active,
            created_at=view.created_at,
            quota_used_bytes=view.quota_used_bytes,
            private_limit_bytes=view.private_limit_bytes,
        )


class UserListResponse(BaseModel):
    items: list[UserAdminViewResponse]
    total: int = Field(ge=0)


class UpdateRoleRequest(BaseModel):
    role: Role


class UpdateRoleResponse(BaseModel):
    user_id: UUID
    email: str
    role: Role

    @classmethod
    def from_user(cls, user: User) -> "UpdateRoleResponse":
        return cls(user_id=user.id, email=user.email, role=user.role)


class UpdatePrivateQuotaRequest(BaseModel):
    private_limit_gb: float = Field(ge=0)


class DiskStatResponse(BaseModel):
    id: str
    mount_path: str
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    status: str

    @classmethod
    def from_stat(cls, stat: DiskStat) -> "DiskStatResponse":
        return cls(
            id=stat.id,
            mount_path=stat.mount_path,
            total_bytes=stat.total_bytes,
            used_bytes=stat.used_bytes,
            free_bytes=stat.free_bytes,
            status=stat.status,
        )


class StorageStatsResponse(BaseModel):
    disks: list[DiskStatResponse]


class StorageHealthResponse(BaseModel):
    disks: dict[str, str]


class ArchiveReportResponse(BaseModel):
    processed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    errors: int = Field(ge=0)

    @classmethod
    def from_report(cls, report: ArchiveReport) -> "ArchiveReportResponse":
        return cls(
            processed=report.processed,
            skipped=report.skipped,
            errors=report.errors,
        )


class ArchiveStatsResponse(BaseModel):
    last_run: datetime | None
    processed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    errors: int = Field(ge=0)
    total_archived_bytes: int = Field(ge=0)

    @classmethod
    def from_stats(cls, stats: ArchiveStats) -> "ArchiveStatsResponse":
        return cls(
            last_run=stats.last_run,
            processed=stats.processed,
            skipped=stats.skipped,
            errors=stats.errors,
            total_archived_bytes=stats.total_archived_bytes,
        )
