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


class FileNotFoundError(Exception):
    """Raised when a requested file or directory does not exist."""


class AccessDeniedError(Exception):
    """Raised when a user attempts to access a resource they do not own."""
