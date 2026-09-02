from dataclasses import dataclass
from uuid import UUID

from app.domain.value_objects.role import Role


@dataclass(frozen=True)
class AuthPrincipal:
    """Request identity: User-Service UUID + storage role (never hub global_role)."""

    id: UUID
    email: str
    name: str
    role: Role
