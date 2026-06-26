class StorageUnavailableError(Exception):
    """Raised when no storage disk is available for write operations."""


class EmailAlreadyExistsError(Exception):
    """Raised when registering with an email that is already taken."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials are invalid."""
