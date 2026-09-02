from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.value_objects.role import Role


@dataclass(frozen=True)
class User:
    """Local projection plus request-time storage role.

    ``role`` is never loaded from the database. It comes from gateway
    ``X-Storage-Role`` or User-Service ``GET /users/{id}/roles/storage``.
    """

    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime
