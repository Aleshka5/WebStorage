from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.domain.value_objects.role import Role


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    user_id: UUID
    email: str
    role: Role

    @classmethod
    def from_user(cls, user_id: UUID, email: str, role: Role) -> "UserResponse":
        return cls(user_id=user_id, email=email, role=role)
