from pydantic import BaseModel, Field


class UnlockRequest(BaseModel):
    passphrase: str = Field(min_length=1)


class UnlockResponse(BaseModel):
    success: bool


class PrivateQuotaResponse(BaseModel):
    private_bytes: int = Field(ge=0)
    private_limit_bytes: int = Field(ge=0)


class PrivateSessionResponse(BaseModel):
    active: bool
    expires_in_seconds: int = Field(ge=0)
