from enum import StrEnum


class Role(StrEnum):
    STRANGER = "STRANGER"
    FAMILY = "FAMILY"
    ADMIN = "ADMIN"
    BLOCKED = "BLOCKED"

    def can_access_shared(self) -> bool:
        return self in (Role.FAMILY, Role.ADMIN)

    def can_access_admin(self) -> bool:
        return self is Role.ADMIN
