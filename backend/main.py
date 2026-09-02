from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.application.archive_service import ArchiveService
from app.application.backup_service import BackupService
from app.application.maintenance_service import MaintenanceService
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.logging_setup import setup_logging
from app.infrastructure.session_store import get_session_store
from app.presentation.dependencies.auth import get_user_directory
from app.presentation.dependencies.archive_providers import (
    get_archive_disk_router,
    get_archive_manager,
)
from app.presentation.routers.admin_router import router as admin_router
from app.presentation.routers.auth_router import router as auth_router
from app.presentation.routers.file_router import router as file_router, shared_router
from app.presentation.routers.photo_router import router as photo_router
from app.presentation.routers.private_router import router as private_router
from app.presentation.routers.quota_router import router as quota_router
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.middleware.rate_limit import AuthRateLimitMiddleware
from config import get_settings

settings = get_settings()
setup_logging(settings)


async def _run_daily_archive_job() -> None:
    logger.info("Scheduled daily archive job started")
    async with async_session_factory() as session:
        archive_service = ArchiveService(
            file_repo=FileRepository(session),
            archive_manager=get_archive_manager(),
            disk_router=get_archive_disk_router(),
            settings=get_settings(),
        )
        try:
            report = await archive_service.run_daily_archive()
            await session.commit()
            logger.info(
                "Scheduled daily archive job completed: processed={}, skipped={}, errors={}",
                report.processed,
                report.skipped,
                report.errors,
            )
        except Exception:
            await session.rollback()
            logger.exception("Scheduled daily archive job failed")


async def _run_cleanup_pending_job() -> None:
    logger.info("Scheduled cleanup_pending job started")
    async with async_session_factory() as session:
        maintenance_service = MaintenanceService(
            file_repo=FileRepository(session),
            quota_repo=QuotaRepository(session),
            disk_router=get_archive_disk_router(),
        )
        try:
            deleted = await maintenance_service.cleanup_pending_records()
            await session.commit()
            logger.info("Scheduled cleanup_pending job completed: deleted={}", deleted)
        except Exception:
            await session.rollback()
            logger.exception("Scheduled cleanup_pending job failed")


async def _run_cleanup_tmp_job() -> None:
    logger.info("Scheduled cleanup_tmp job started")
    async with async_session_factory() as session:
        maintenance_service = MaintenanceService(
            file_repo=FileRepository(session),
            quota_repo=QuotaRepository(session),
            disk_router=get_archive_disk_router(),
        )
        try:
            deleted = await maintenance_service.cleanup_tmp_dirs()
            await session.commit()
            logger.info("Scheduled cleanup_tmp job completed: deleted={}", deleted)
        except Exception:
            await session.rollback()
            logger.exception("Scheduled cleanup_tmp job failed")


async def _run_reconcile_quotas_job() -> None:
    logger.info("Scheduled reconcile_quotas job started")
    async with async_session_factory() as session:
        maintenance_service = MaintenanceService(
            file_repo=FileRepository(session),
            quota_repo=QuotaRepository(session),
            disk_router=get_archive_disk_router(),
        )
        try:
            report = await maintenance_service.reconcile_quotas()
            await session.commit()
            logger.info(
                "Scheduled reconcile_quotas job completed: checked={}, fixed={}",
                report.checked,
                report.fixed,
            )
        except Exception:
            await session.rollback()
            logger.exception("Scheduled reconcile_quotas job failed")


async def _run_db_backup_job() -> None:
    logger.bind(action="db_backup", result="started").info("Scheduled db backup job started")
    backup_service = BackupService(
        disk_router=get_archive_disk_router(),
        settings=settings,
    )
    try:
        result = await backup_service.run_db_backup()
        logger.bind(action="db_backup", result="success").info(
            "Scheduled db backup job completed: filename={}",
            result.filename,
        )
    except Exception:
        logger.bind(action="db_backup", result="error", error_code="BACKUP_JOB_FAILED").exception(
            "Scheduled db backup job failed",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_daily_archive_job,
        "cron",
        hour=3,
        minute=0,
        id="daily_archive",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_cleanup_pending_job,
        "interval",
        hours=1,
        id="cleanup_pending",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_cleanup_tmp_job,
        "interval",
        hours=1,
        id="cleanup_tmp",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_reconcile_quotas_job,
        "cron",
        hour=4,
        minute=0,
        id="reconcile_quotas",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_db_backup_job,
        "cron",
        hour=2,
        minute=0,
        id="db_backup",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "APScheduler started: db_backup@02:00, daily_archive@03:00, cleanup_pending@1h, "
        "cleanup_tmp@1h, reconcile_quotas@04:00",
    )
    yield
    scheduler.shutdown(wait=False)
    await get_session_store().close()
    directory = get_user_directory()
    close = getattr(directory, "aclose", None)
    if close is not None:
        await close()
        get_user_directory.cache_clear()


app = FastAPI(title="HomeCloud", version="1.0", lifespan=lifespan)
register_exception_handlers(app)
app.add_middleware(AuthRateLimitMiddleware)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(quota_router)
app.include_router(file_router)
app.include_router(shared_router)
app.include_router(photo_router)
app.include_router(private_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _spa_index() -> Path | None:
    spa_dir = Path(settings.spa.static_dir)
    index = spa_dir / "index.html"
    if index.is_file():
        return index
    return None


_spa_index_path = _spa_index()
if _spa_index_path is not None:
    spa_dir = _spa_index_path.parent
    assets_dir = spa_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")
    logger.info("Serving built SPA from {} (HTML Cache-Control: no-cache)", spa_dir)

    spa_index_headers = {"Cache-Control": "no-cache"}

    @app.get("/")
    async def spa_root() -> FileResponse:
        return FileResponse(_spa_index_path, headers=spa_index_headers)

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = spa_dir / full_path
        if candidate.is_file() and spa_dir in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(_spa_index_path, headers=spa_index_headers)
else:
    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "version": "1.0"}
