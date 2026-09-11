from datetime import datetime

from pydantic import BaseModel, Field

from app.application.resume_service import ResumeLevel


class ResumeNode(BaseModel):
    name: str
    path: str
    level: ResumeLevel | None = None
    child_count: int = Field(ge=0)
    modified_at: datetime
    status_id: str | None = None
    website_url: str | None = None


class ResumeTreeResponse(BaseModel):
    path: str
    level: ResumeLevel | None = None
    items: list[ResumeNode]


class ResumeNodeCreateRequest(BaseModel):
    path: str = ""
    name: str
    status_id: str | None = None


class ResumeNodeRenameRequest(BaseModel):
    path: str
    new_name: str


class VacancyFieldItem(BaseModel):
    name: str
    value: str = ""


class VacancyMeta(BaseModel):
    path: str
    name: str
    website_url: str = ""
    status_id: str | None = None
    fields: list[VacancyFieldItem]


class VacancyMetaPutRequest(BaseModel):
    website_url: str = ""
    status_id: str | None = None
    fields: list[VacancyFieldItem] = Field(default_factory=list)


class VacancyListItem(BaseModel):
    country: str
    company: str
    name: str
    path: str
    status_id: str | None = None
    website_url: str = ""
    modified_at: datetime


class VacancyListResponse(BaseModel):
    items: list[VacancyListItem]


class ResumeStatus(BaseModel):
    id: str | None = None
    name: str
    color: str


class ResumeStatusListResponse(BaseModel):
    statuses: list[ResumeStatus]


class ResumeStatusesPutRequest(BaseModel):
    statuses: list[ResumeStatus]
