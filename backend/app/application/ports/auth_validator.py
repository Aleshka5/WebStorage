from typing import Protocol

from app.domain.entities.auth_principal import AuthPrincipal


class AuthValidator(Protocol):
    """Application port for Auth-Service session validation and admin listing.

    Implementations live in Infrastructure. Domain and Application must not
    import grpcio or generated protobuf stubs.
    """

    async def validate(self, session_id: str, caller_host: str) -> AuthPrincipal:
        """Validate a session and return the projected storage principal."""

    async def list_users(self, session_id: str, caller_host: str) -> list[AuthPrincipal]:
        """List users with the same host projection as validate (admin-only RPC)."""
