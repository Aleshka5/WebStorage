from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.generated_service import GeneratedListItem, GeneratedMeta, GeneratedService
from app.domain.entities.user import User
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.auth import get_current_user
from app.presentation.dependencies.generated import get_generated_service
from app.presentation.schemas.generated import (
    GeneratedCreatedResponse,
    GeneratedListItemResponse,
    GeneratedListResponse,
    GeneratedRunResponse,
)

router = APIRouter(prefix="/api/generated", tags=["generated"])

_IMAGE_MEDIA_TYPE = "image/png"


def _file_url(run_id: str, name: str) -> str:
    return f"/api/generated/{run_id}/files/{name}"


def _run_response(run_id: str, meta: GeneratedMeta) -> GeneratedRunResponse:
    return GeneratedRunResponse(
        prompt=meta.prompt,
        negative_prompt=meta.negative_prompt,
        seed=meta.seed,
        steps=meta.steps,
        true_cfg_scale=meta.true_cfg_scale,
        width=meta.width,
        height=meta.height,
        duration=meta.duration,
        references=[_file_url(run_id, name) for name in meta.references],
        result=_file_url(run_id, meta.result),
    )


def _list_item_response(item: GeneratedListItem) -> GeneratedListItemResponse:
    return GeneratedListItemResponse(id=item.id, created_at=item.created_at, prompt=item.prompt)


async def _read_upload(upload: UploadFile | None) -> bytes | None:
    if upload is None:
        return None
    payload = await upload.read()
    if not upload.filename and not payload:
        return None
    return payload


async def _read_references(uploads: list[UploadFile] | None) -> list[bytes]:
    if not uploads:
        return []

    payloads: list[bytes] = []
    for upload in uploads:
        payload = await upload.read()
        if not upload.filename and not payload:
            continue
        payloads.append(payload)
    return payloads


@router.post("", response_model=GeneratedCreatedResponse, status_code=status.HTTP_201_CREATED)
async def store_generated_run(
    prompt: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    seed: str | None = Form(default=None),
    steps: str | None = Form(default=None),
    true_cfg_scale: str | None = Form(default=None),
    width: str | None = Form(default=None),
    height: str | None = Form(default=None),
    duration: str | None = Form(default=None),
    images: list[UploadFile] | None = File(default=None),
    result: UploadFile | None = File(default=None),
    current_user: User = Depends(get_current_user),
    generated_service: GeneratedService = Depends(get_generated_service),
    session: AsyncSession = Depends(get_async_session),
) -> GeneratedCreatedResponse:
    logger.info("Generated run POST for user {}", current_user.id)
    references = await _read_references(images)
    result_bytes = await _read_upload(result)

    try:
        stored = await generated_service.store_run(
            current_user.id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            steps=steps,
            true_cfg_scale=true_cfg_scale,
            width=width,
            height=height,
            duration=duration,
            references=references,
            result=result_bytes,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Generated run {} stored for user {}", stored.id, current_user.id)
    return GeneratedCreatedResponse(id=stored.id, created_at=stored.created_at)


@router.get("", response_model=GeneratedListResponse)
async def list_generated_runs(
    current_user: User = Depends(get_current_user),
    generated_service: GeneratedService = Depends(get_generated_service),
    session: AsyncSession = Depends(get_async_session),
) -> GeneratedListResponse:
    logger.info("Generated run list GET for user {}", current_user.id)

    try:
        items = await generated_service.list_runs(current_user.id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return GeneratedListResponse(items=[_list_item_response(item) for item in items])


@router.get("/{run_id}", response_model=GeneratedRunResponse)
async def get_generated_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    generated_service: GeneratedService = Depends(get_generated_service),
    session: AsyncSession = Depends(get_async_session),
) -> GeneratedRunResponse:
    logger.info("Generated run GET {} for user {}", run_id, current_user.id)

    try:
        meta = await generated_service.get_run(current_user.id, run_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _run_response(run_id, meta)


@router.get("/{run_id}/files/{name}")
async def get_generated_file(
    run_id: str,
    name: str,
    current_user: User = Depends(get_current_user),
    generated_service: GeneratedService = Depends(get_generated_service),
    session: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    logger.info("Generated file GET {}/{} for user {}", run_id, name, current_user.id)

    try:
        relative_path = await generated_service.prepare_file(current_user.id, run_id, name)
    except Exception:
        await session.rollback()
        raise

    async def stream_and_commit() -> AsyncIterator[bytes]:
        try:
            async for chunk in generated_service.iter_file(current_user.id, relative_path):
                yield chunk
            await session.commit()
            logger.info(
                "Committed generated file read for user {} run {} file {}",
                current_user.id,
                run_id,
                name,
            )
        except Exception:
            await session.rollback()
            raise

    return StreamingResponse(
        stream_and_commit(),
        media_type=_IMAGE_MEDIA_TYPE,
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_generated_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    generated_service: GeneratedService = Depends(get_generated_service),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    logger.info("Generated run DELETE {} for user {}", run_id, current_user.id)

    try:
        await generated_service.delete_run(current_user.id, run_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return Response(status_code=status.HTTP_204_NO_CONTENT)
