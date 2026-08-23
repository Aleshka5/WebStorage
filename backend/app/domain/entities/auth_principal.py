from dataclasses import dataclass
from uuid import UUID

from app.domain.value_objects.role import Role


@dataclass(frozen=True)
class AuthPrincipal:
    """Identity projected from Auth-Service Validate / ListUsers (`storage_roles`)."""

    id: UUID
    email: str
    name: str
    role: Role
