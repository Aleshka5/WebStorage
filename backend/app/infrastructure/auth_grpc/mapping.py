from collections.abc import Mapping
from uuid import UUID

import grpc
from loguru import logger

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.exceptions import (
    AuthAccessDeniedError,
    AuthBlockedError,
    AuthMisconfiguredError,
    AuthUnauthenticatedError,
    AuthUnavailableError,
)
from app.domain.value_objects.role import Role

_BLOCKED_DETAIL = "blocked"


def principal_from_fields(fields: Mapping[str, str]) -> AuthPrincipal:
    """Map Auth-Service whitelist fields to AuthPrincipal.

    Requires `id` and `storage_roles`. Invalid role strings fail closed
    (never default to STRANGER).
    """
    raw_id = str(fields.get("id") or "").strip()
    if not raw_id:
        logger.error(
            "Auth-Service response is missing user id field_keys={}",
            sorted(fields.keys()),
        )
        raise AuthMisconfiguredError("Auth-Service response is missing user id")

    try:
        user_id = UUID(raw_id)
    except ValueError as exc:
        logger.error("Auth-Service returned an invalid user id")
        raise AuthMisconfiguredError("Auth-Service returned an invalid user id") from exc

    raw_role = str(fields.get("storage_roles") or "").strip()
    if not raw_role:
        logger.error(
            "Auth-Service response is missing storage_roles for user_id={}",
            user_id,
        )
        raise AuthMisconfiguredError("Auth-Service response is missing storage_roles")

    try:
        role = Role(raw_role)
    except ValueError as exc:
        logger.error(
            "Auth-Service returned invalid storage_roles for user_id={}",
            user_id,
        )
        raise AuthMisconfiguredError(
            f"Auth-Service returned invalid storage_roles {raw_role!r}"
        ) from exc

    return AuthPrincipal(
        id=user_id,
        email=str(fields.get("google_email") or ""),
        name=str(fields.get("name") or ""),
        role=role,
    )


def exception_from_rpc_error(error: grpc.RpcError) -> Exception:
    """Map a gRPC status to a domain auth exception."""
    code = error.code()
    details = (error.details() or "").strip()

    if code in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.INVALID_ARGUMENT):
        return AuthUnauthenticatedError(details or "Authentication required")

    if code == grpc.StatusCode.PERMISSION_DENIED:
        details_lower = details.lower()
        if details_lower == _BLOCKED_DETAIL or details_lower.startswith(
            f"{_BLOCKED_DETAIL} "
        ):
            return AuthBlockedError(details or "User is blocked")
        return AuthAccessDeniedError(details or "Access denied")

    if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
        return AuthUnavailableError(details or "Authentication service unavailable")

    return AuthUnavailableError(
        details or f"Authentication service error ({code.name})"
    )
