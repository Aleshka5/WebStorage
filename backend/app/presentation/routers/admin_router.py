from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin_service import AdminService, DiskStat, UserAdminView
from app.application.archive_service import ArchiveService
from app.application.maintenance_service import MaintenanceService
from app.domain.entities.user import User
from app.domain.exceptions import SelfRoleChangeError, SelfUserDeletionError, UserNotFoundError
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.infrastructure.disk_router import DiskRouter
from app.presentation.dependencies.admin import get_admin_service, get_disk_router
from app.presentation.dependencies.archive import get_archive_service
from app.presentation.dependencies.maintenance import get_maintenance_service
from app.presentation.middleware.check_role import check_role
from app.presentation.schemas.admin import (
    ArchiveReportResponse,
    ArchiveStatsResponse,
    DiskStatResponse,
    MaintenanceRunResponse,
    MaintenanceStatsResponse,
    ReconcileReportResponse,
    StorageHealthResponse,
    StorageStatsResponse,
    UpdatePrivateQuotaRequest,
    UpdateRoleRequest,
    UpdateRoleResponse,
    UserAdminViewResponse,
    UserListResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    role: Role | None = Query(default=None),
    email: str | None = Query(default=None, min_length=1, max_length=255),
    _admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
) -> UserListResponse:
    result = await admin_service.list_users(
        page=page,
        limit=limit,
        role_filter=role,
        email_search=email.strip() if email else None,
    )
    items = [
        UserAdminViewResponse.from_view(view)
        for view in result["items"]
        if isinstance(view, UserAdminView)
    ]
    total = result["total"]
    assert isinstance(total, int)
    logger.info("Admin user list returned {} items (total={})", len(items), total)
    return UserListResponse(items=items, total=total)


@router.patch("/users/{user_id}/role", response_model=UpdateRoleResponse)
async def update_user_role(
    user_id: UUID,
    body: UpdateRoleRequest,
    admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
    session: AsyncSession = Depends(get_async_session),
) -> UpdateRoleResponse:
    try:
        user = await admin_service.update_role(admin.id, user_id, body.role)
        await session.commit()
        return UpdateRoleResponse.from_user(user)
    except SelfRoleChangeError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc),
            },
        ) from exc
    except UserNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": ErrorCode.USER_NOT_FOUND,
                "message": str(exc),
            },
        ) from exc


@router.patch("/users/{user_id}/quota", status_code=status.HTTP_204_NO_CONTENT)
async def update_user_private_quota(
    user_id: UUID,
    body: UpdatePrivateQuotaRequest,
    admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        await admin_service.update_private_quota(admin.id, user_id, body.private_limit_gb)
        await session.commit()
    except UserNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": ErrorCode.USER_NOT_FOUND,
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc),
            },
        ) from exc


@router.post("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_user(
    user_id: UUID,
    admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        await admin_service.block_user(admin.id, user_id)
        await session.commit()
    except UserNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": ErrorCode.USER_NOT_FOUND,
                "message": str(exc),
            },
        ) from exc


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    try:
        await admin_service.delete_user(admin.id, user_id)
        await session.commit()
    except SelfUserDeletionError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.ACCESS_DENIED,
                "message": str(exc),
            },
        ) from exc
    except UserNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": ErrorCode.USER_NOT_FOUND,
                "message": str(exc),
            },
        ) from exc


@router.get("/storage", response_model=StorageStatsResponse)
async def get_storage_stats(
    _admin: User = Depends(check_role(Role.ADMIN)),
    admin_service: AdminService = Depends(get_admin_service),
) -> StorageStatsResponse:
    result = admin_service.get_storage_stats()
    disk_responses = [
        DiskStatResponse.from_stat(stat)
        for stat in result["disks"]
        if isinstance(stat, DiskStat)
    ]
    return StorageStatsResponse(disks=disk_responses)


@router.get("/storage/health", response_model=StorageHealthResponse)
async def get_storage_health(
    _admin: User = Depends(check_role(Role.ADMIN)),
    disk_router: DiskRouter = Depends(get_disk_router),
) -> StorageHealthResponse:
    health = disk_router.health_check()
    logger.info("Admin storage health check completed")
    return StorageHealthResponse(disks=health)


@router.get("/archive/run", response_model=ArchiveReportResponse)
async def run_archive(
    _admin: User = Depends(check_role(Role.ADMIN)),
    archive_service: ArchiveService = Depends(get_archive_service),
    session: AsyncSession = Depends(get_async_session),
) -> ArchiveReportResponse:
    report = await archive_service.run_daily_archive()
    await session.commit()
    logger.info(
        "Manual archive run completed: processed={}, skipped={}, errors={}",
        report.processed,
        report.skipped,
        report.errors,
    )
    return ArchiveReportResponse.from_report(report)


@router.get("/archive/stats", response_model=ArchiveStatsResponse)
async def get_archive_stats(
    _admin: User = Depends(check_role(Role.ADMIN)),
    archive_service: ArchiveService = Depends(get_archive_service),
) -> ArchiveStatsResponse:
    stats = await archive_service.get_stats()
    logger.info("Admin archive stats requested")
    return ArchiveStatsResponse.from_stats(stats)


@router.get("/maintenance/run", response_model=MaintenanceRunResponse)
async def run_maintenance(
    _admin: User = Depends(check_role(Role.ADMIN)),
    maintenance_service: MaintenanceService = Depends(get_maintenance_service),
    archive_service: ArchiveService = Depends(get_archive_service),
    session: AsyncSession = Depends(get_async_session),
) -> MaintenanceRunResponse:
    pending_deleted = await maintenance_service.cleanup_pending_records()
    tmp_deleted = await maintenance_service.cleanup_tmp_dirs()
    reconcile = await maintenance_service.reconcile_quotas()
    archive = await archive_service.run_daily_archive()
    await session.commit()

    logger.info(
        "Manual maintenance run completed: pending_deleted={}, tmp_deleted={}, "
        "quota_fixed={}, archive_processed={}",
        pending_deleted,
        tmp_deleted,
        reconcile.fixed,
        archive.processed,
    )
    return MaintenanceRunResponse(
        pending_deleted=pending_deleted,
        tmp_deleted=tmp_deleted,
        reconcile=ReconcileReportResponse.from_report(reconcile),
        archive=ArchiveReportResponse.from_report(archive),
    )


@router.get("/maintenance/stats", response_model=MaintenanceStatsResponse)
async def get_maintenance_stats(
    _admin: User = Depends(check_role(Role.ADMIN)),
    maintenance_service: MaintenanceService = Depends(get_maintenance_service),
) -> MaintenanceStatsResponse:
    stats = await maintenance_service.get_stats()
    logger.info("Admin maintenance stats requested")
    return MaintenanceStatsResponse.from_stats(stats)
