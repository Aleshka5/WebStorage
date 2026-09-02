from app.domain.value_objects.error_codes import ErrorCode


class StorageUnavailableError(Exception):
    """Raised when no storage disk is available for write operations."""


class EmailAlreadyExistsError(Exception):
    """Raised when registering with an email that is already taken."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials are invalid."""


class PathTraversalError(Exception):
    """Raised when a user-supplied path escapes the allowed base directory."""


class QuotaExceededError(Exception):
    """Raised when a storage operation would exceed the user's quota."""

    def __init__(self, message: str, *, available_bytes: int | None = None) -> None:
        super().__init__(message)
        self.available_bytes = available_bytes


class FileNotFoundError(Exception):
    """Raised when a requested file or directory does not exist."""


class AccessDeniedError(Exception):
    """Raised when a user attempts to access a resource they do not own."""


class UnsupportedFormatError(Exception):
    """Raised when an uploaded file format is not supported."""


class PrivateSessionExpiredError(Exception):
    """Raised when the private encryption session key is missing or expired."""


class UserNotFoundError(Exception):
    """Raised when a requested user does not exist."""


class SelfRoleChangeError(Exception):
    """Raised when an admin attempts to change their own role."""


class SelfUserDeletionError(Exception):
    """Raised when an admin attempts to delete their own account."""


class AuthUnauthenticatedError(Exception):
    """Raised when identity headers are missing or not a UUID."""


class AuthBlockedError(Exception):
    """Raised when the storage role is BLOCKED."""


class AuthAccessDeniedError(Exception):
    """Raised when the caller is denied a storage section."""


class AuthMisconfiguredError(Exception):
    """Raised when an identity header cannot be mapped (invalid role string)."""


class UserServiceUnavailableError(Exception):
    """Raised when User-Service is unreachable, times out, or returns a failed lookup."""


class KeysValidationError(Exception):
    """Raised when a keys-registry payload fails name/value validation."""

    def __init__(self, message: str, *, error_code: ErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


class KeysYamlInvalidError(Exception):
    """Raised when keys.yaml is not valid YAML or not a flat string mapping."""
