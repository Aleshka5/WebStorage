from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from loguru import logger

from app.domain.entities.user import User
from app.domain.value_objects.error_codes import ErrorCode
from app.domain.value_objects.role import Role
from app.presentation.dependencies.auth import get_current_user


def check_role(*allowed_roles: Role) -> Callable[..., User]:
    allowed = set(allowed_roles)

    async def _check_role(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            logger.warning(
                "Access denied for user {} with role {} (required: {})",
                current_user.id,
                current_user.role.value,
                ", ".join(role.value for role in allowed_roles),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": ErrorCode.ACCESS_DENIED,
                    "message": "Insufficient permissions for this section",
                },
            )
        return current_user

    return _check_role
