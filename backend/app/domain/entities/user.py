from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.value_objects.role import Role


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    password_hash: str | None
    role: Role
    is_active: bool
    created_at: datetime
