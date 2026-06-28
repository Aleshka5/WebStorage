from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from loguru import logger

from app.application.archive_service import ArchiveService
from app.application.maintenance_service import MaintenanceService
from app.infrastructure.database.repositories.file_repo import FileRepository
from app.infrastructure.database.repositories.quota_repo import QuotaRepository
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.session_store import get_session_store
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
from config import get_settings


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
    scheduler.start()
    logger.info(
        "APScheduler started: daily_archive@03:00, cleanup_pending@1h, "
        "cleanup_tmp@1h, reconcile_quotas@04:00",
    )
    yield
    scheduler.shutdown(wait=False)
    await get_session_store().close()


app = FastAPI(title="HomeCloud", version="1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(quota_router)
app.include_router(file_router)
app.include_router(shared_router)
app.include_router(photo_router)
app.include_router(private_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "version": "1.0"}
