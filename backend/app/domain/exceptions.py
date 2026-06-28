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
