from pydantic import BaseModel, Field


class GeneratedCreatedResponse(BaseModel):
    id: str
    created_at: str


class GeneratedListItemResponse(BaseModel):
    id: str
    created_at: str
    prompt: str


class GeneratedListResponse(BaseModel):
    items: list[GeneratedListItemResponse]


class GeneratedRunResponse(BaseModel):
    prompt: str
    negative_prompt: str
    seed: int
    steps: int
    true_cfg_scale: float
    width: int
    height: int
    duration: float
    references: list[str] = Field(default_factory=list)
    result: str
