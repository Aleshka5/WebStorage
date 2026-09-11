from fastapi import APIRouter, Depends, Query, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.resume_service import ResumeNode as ResumeNodeEntry
from app.application.resume_service import ResumeService
from app.application.resume_service import ResumeStatus as ResumeStatusEntry
from app.application.resume_service import VacancyField as VacancyFieldEntry
from app.application.resume_service import VacancyListItem as VacancyListItemEntry
from app.application.resume_service import VacancyMeta as VacancyMetaEntry
from app.domain.entities.file_record import FileSection
from app.domain.entities.user import User
from app.domain.value_objects.role import Role
from app.infrastructure.database.session import get_async_session
from app.presentation.dependencies.resumes import get_resume_service, get_resumes_file_service
from app.presentation.middleware.check_role import check_role
from app.presentation.routers.file_router_factory import build_file_router
from app.presentation.schemas.resumes import (
    ResumeNode,
    ResumeNodeCreateRequest,
    ResumeNodeRenameRequest,
    ResumeStatus,
    ResumeStatusesPutRequest,
    ResumeStatusListResponse,
    ResumeTreeResponse,
    VacancyFieldItem,
    VacancyListItem,
    VacancyListResponse,
    VacancyMeta,
    VacancyMetaPutRequest,
)

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

files_router = build_file_router(
    prefix="/api/resumes/files",
    tags=["resumes-files"],
    file_service_dependency=get_resumes_file_service,
    section=FileSection.RESUMES,
    user_dependency=check_role(Role.FAMILY, Role.ADMIN),
    log_label="Resumes",
)


def _node_response(node: ResumeNodeEntry) -> ResumeNode:
    return ResumeNode(
        name=node.name,
        path=node.path,
        level=node.level,
        child_count=node.child_count,
        modified_at=node.modified_at,
        status_id=node.status_id,
        website_url=node.website_url,
    )


def _vacancy_response(meta: VacancyMetaEntry) -> VacancyMeta:
    return VacancyMeta(
        path=meta.path,
        name=meta.name,
        website_url=meta.website_url,
        status_id=meta.status_id,
        fields=[VacancyFieldItem(name=item.name, value=item.value) for item in meta.fields],
    )


def _statuses_response(statuses: list[ResumeStatusEntry]) -> ResumeStatusListResponse:
    return ResumeStatusListResponse(
        statuses=[
            ResumeStatus(id=status_entry.id, name=status_entry.name, color=status_entry.color)
            for status_entry in statuses
        ],
    )


@router.get("/tree", response_model=ResumeTreeResponse)
async def list_resume_tree(
    path: str = Query(default=""),
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> ResumeTreeResponse:
    logger.info("Resumes tree GET for user {} at path {!r}", current_user.id, path)

    try:
        tree = await resume_service.list_nodes(current_user.id, path)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return ResumeTreeResponse(
        path=tree.path,
        level=tree.level,
        items=[_node_response(node) for node in tree.items],
    )


@router.post("/tree", response_model=ResumeNode, status_code=status.HTTP_201_CREATED)
async def create_resume_node(
    body: ResumeNodeCreateRequest,
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> ResumeNode:
    logger.info(
        "Resumes tree POST for user {} at path {!r}",
        current_user.id,
        body.path,
    )

    try:
        node = await resume_service.create_node(
            current_user.id,
            body.path,
            body.name,
            status_id=body.status_id,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _node_response(node)


@router.patch("/tree", response_model=ResumeNode)
async def rename_resume_node(
    body: ResumeNodeRenameRequest,
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> ResumeNode:
    logger.info(
        "Resumes tree PATCH for user {} at path {!r}",
        current_user.id,
        body.path,
    )

    try:
        node = await resume_service.rename_node(current_user.id, body.path, body.new_name)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _node_response(node)


@router.delete("/tree", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume_node(
    path: str = Query(...),
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    logger.info("Resumes tree DELETE for user {} at path {!r}", current_user.id, path)

    try:
        await resume_service.delete_node(current_user.id, path)
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("/vacancy", response_model=VacancyMeta)
async def get_vacancy(
    path: str = Query(...),
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> VacancyMeta:
    logger.info("Resumes vacancy GET for user {} at path {!r}", current_user.id, path)

    try:
        meta = await resume_service.get_vacancy(current_user.id, path)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _vacancy_response(meta)


@router.put("/vacancy", response_model=VacancyMeta)
async def save_vacancy(
    body: VacancyMetaPutRequest,
    path: str = Query(...),
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> VacancyMeta:
    logger.info("Resumes vacancy PUT for user {} at path {!r}", current_user.id, path)

    try:
        meta = await resume_service.save_vacancy(
            current_user.id,
            path,
            website_url=body.website_url,
            status_id=body.status_id,
            fields=[
                VacancyFieldEntry(name=item.name, value=item.value) for item in body.fields
            ],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _vacancy_response(meta)


def _vacancy_list_item(item: VacancyListItemEntry) -> VacancyListItem:
    return VacancyListItem(
        country=item.country,
        company=item.company,
        name=item.name,
        path=item.path,
        status_id=item.status_id,
        website_url=item.website_url,
        modified_at=item.modified_at,
    )


@router.get("/vacancies", response_model=VacancyListResponse)
async def list_all_vacancies(
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> VacancyListResponse:
    logger.info("Resumes flat vacancy list requested by user {}", current_user.id)

    try:
        items = await resume_service.list_all_vacancies(current_user.id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info("Returned {} vacancies for user {}", len(items), current_user.id)
    return VacancyListResponse(items=[_vacancy_list_item(item) for item in items])


@router.get("/statuses", response_model=ResumeStatusListResponse)
async def list_statuses(
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> ResumeStatusListResponse:
    logger.info("Resumes statuses GET for user {}", current_user.id)

    try:
        statuses = await resume_service.list_statuses(current_user.id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _statuses_response(statuses)


@router.put("/statuses", response_model=ResumeStatusListResponse)
async def save_statuses(
    body: ResumeStatusesPutRequest,
    current_user: User = Depends(check_role(Role.FAMILY, Role.ADMIN)),
    resume_service: ResumeService = Depends(get_resume_service),
    session: AsyncSession = Depends(get_async_session),
) -> ResumeStatusListResponse:
    logger.info("Resumes statuses PUT for user {}", current_user.id)

    try:
        statuses = await resume_service.save_statuses(
            current_user.id,
            [
                ResumeStatusEntry(
                    id=item.id or "",
                    name=item.name,
                    color=item.color,
                )
                for item in body.statuses
            ],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _statuses_response(statuses)
