from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.value_objects.role import Role

STORAGE_SERVICE_KEY = "storage"


@dataclass(frozen=True)
class DirectoryUser:
    """User-Service directory row projected for storage admin / upsert."""

    id: UUID
    email: str
    username: str
    storage_role: Role


class UserDirectory(Protocol):
    """Application port for User-Service HTTP (network trust, no JWT)."""

    async def get_storage_role(self, user_id: UUID) -> Role:
        """GET /users/{id}/roles/storage. 200 STRANGER is success."""

    async def get_user(self, user_id: UUID) -> DirectoryUser:
        """GET /users/{id} for email when the gateway omitted X-Auth-Email."""

    async def list_users(self) -> list[DirectoryUser]:
        """GET /users. Missing storage service row → STRANGER."""
