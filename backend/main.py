from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infrastructure.session_store import get_session_store
from app.presentation.routers.auth_router import router as auth_router
from app.presentation.routers.file_router import router as file_router
from app.presentation.routers.photo_router import router as photo_router
from app.presentation.routers.private_router import router as private_router
from app.presentation.routers.quota_router import router as quota_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await get_session_store().close()


app = FastAPI(title="HomeCloud", version="1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(quota_router)
app.include_router(file_router)
app.include_router(photo_router)
app.include_router(private_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "version": "1.0"}
