from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import grpc
import pytest

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.exceptions import (
    AuthAccessDeniedError,
    AuthBlockedError,
    AuthMisconfiguredError,
    AuthUnauthenticatedError,
    AuthUnavailableError,
)
from app.domain.value_objects.role import Role
from app.infrastructure.auth_grpc import auth_pb2
from app.infrastructure.auth_grpc.client import AuthGrpcClient
from app.infrastructure.auth_grpc.mapping import (
    exception_from_rpc_error,
    principal_from_fields,
)
from config import get_settings

FAMILY_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
ADMIN_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
CALLER_HOST = "storage.filenkov.store"
SESSION_ID = "opaque-session-id-not-for-logs"

FAMILY_FIELDS = {
    "id": str(FAMILY_USER_ID),
    "google_email": "family@example.test",
    "name": "Family User",
    "storage_roles": "FAMILY",
}

ADMIN_FIELDS = {
    "id": str(ADMIN_USER_ID),
    "google_email": "admin@example.test",
    "name": "Admin User",
    "storage_roles": "ADMIN",
}

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROOT = BACKEND_ROOT / "app" / "domain"
APPLICATION_ROOT = BACKEND_ROOT / "app" / "application"


class FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str = "") -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class FakeAuthStub:
    def __init__(
        self,
        *,
        fields: dict[str, str] | None = None,
        error: BaseException | None = None,
        users: list[dict[str, str]] | None = None,
        error_queue: list[BaseException] | None = None,
    ) -> None:
        self.fields = fields or {}
        self.error = error
        self.users = users
        self.error_queue = list(error_queue or [])
        self.validate_calls = 0
        self.list_calls = 0
        self.last_request = None
        self.last_timeout: float | None = None

    def _raise_if_needed(self) -> None:
        if self.error_queue:
            raise self.error_queue.pop(0)
        if self.error is not None:
            raise self.error

    async def Validate(self, request: object, timeout: float | None = None) -> object:
        self.validate_calls += 1
        self.last_request = request
        self.last_timeout = timeout
        self._raise_if_needed()
        return SimpleNamespace(fields=self.fields)

    async def ListUsers(self, request: object, timeout: float | None = None) -> object:
        self.list_calls += 1
        self.last_request = request
        self.last_timeout = timeout
        self._raise_if_needed()
        users = self.users if self.users is not None else [self.fields]
        return SimpleNamespace(users=[SimpleNamespace(fields=item) for item in users])


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(stub: FakeAuthStub) -> AuthGrpcClient:
    return AuthGrpcClient(stub=stub, settings=get_settings())


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def _imports_grpc(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "grpc" or name.startswith("grpc.") or "pb2" in name:
                    found.append(name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                module == "grpc"
                or module.startswith("grpc.")
                or "pb2" in module
                or "auth_grpc" in module.split(".")
            ):
                found.append(module)
    return found


def test_generated_stubs_match_auth_proto_contract() -> None:
    request = auth_pb2.ValidateRequest(
        session_id="sid",
        caller_host=CALLER_HOST,
    )
    assert request.session_id == "sid"
    assert request.caller_host == CALLER_HOST
    assert "session_id" in auth_pb2.ValidateRequest.DESCRIPTOR.fields_by_name
    assert "caller_host" in auth_pb2.ValidateRequest.DESCRIPTOR.fields_by_name
    assert "session_id" in auth_pb2.ListUsersRequest.DESCRIPTOR.fields_by_name
    assert "caller_host" in auth_pb2.ListUsersRequest.DESCRIPTOR.fields_by_name
    assert "users" in auth_pb2.ListUsersResponse.DESCRIPTOR.fields_by_name


def test_domain_and_application_do_not_import_grpc() -> None:
    offenders: list[str] = []
    for root in (DOMAIN_ROOT, APPLICATION_ROOT):
        for path in _python_files(root):
            imported = _imports_grpc(path)
            if imported:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}: {imported}")
    assert offenders == []


def test_principal_from_fields_maps_family_role() -> None:
    principal = principal_from_fields(FAMILY_FIELDS)
    assert principal.role == Role.FAMILY
    assert principal.id == FAMILY_USER_ID
    assert principal.email == "family@example.test"
    assert principal.name == "Family User"


def test_principal_from_fields_invalid_storage_roles_is_misconfigured() -> None:
    fields = {**FAMILY_FIELDS, "storage_roles": "GUEST"}
    with pytest.raises(AuthMisconfiguredError, match="invalid storage_roles"):
        principal_from_fields(fields)


def test_principal_from_fields_empty_id_is_misconfigured() -> None:
    fields = {**FAMILY_FIELDS, "id": ""}
    with pytest.raises(AuthMisconfiguredError, match="missing user id"):
        principal_from_fields(fields)


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, "invalid session", AuthUnauthenticatedError),
        (grpc.StatusCode.INVALID_ARGUMENT, "empty session_id", AuthUnauthenticatedError),
        (grpc.StatusCode.PERMISSION_DENIED, "blocked", AuthBlockedError),
        (
            grpc.StatusCode.PERMISSION_DENIED,
            "unknown caller_host",
            AuthAccessDeniedError,
        ),
        (grpc.StatusCode.UNAVAILABLE, "connection refused", AuthUnavailableError),
        (grpc.StatusCode.DEADLINE_EXCEEDED, "deadline", AuthUnavailableError),
    ],
)
def test_exception_from_rpc_error_mapping(
    code: grpc.StatusCode,
    details: str,
    expected: type[Exception],
) -> None:
    mapped = exception_from_rpc_error(FakeRpcError(code, details))
    assert isinstance(mapped, expected)


@pytest.mark.asyncio
async def test_validate_maps_family_principal() -> None:
    stub = FakeAuthStub(fields=FAMILY_FIELDS)
    principal = await _client(stub).validate(SESSION_ID, CALLER_HOST)

    assert isinstance(principal, AuthPrincipal)
    assert principal.role == Role.FAMILY
    assert principal.id == FAMILY_USER_ID
    assert principal.email == "family@example.test"
    assert stub.validate_calls == 1
    assert stub.last_timeout == 2.0
    assert stub.last_request.session_id == SESSION_ID
    assert stub.last_request.caller_host == CALLER_HOST


@pytest.mark.asyncio
async def test_validate_invalid_storage_roles_raises_misconfigured() -> None:
    stub = FakeAuthStub(fields={**FAMILY_FIELDS, "storage_roles": "not-a-role"})
    with pytest.raises(AuthMisconfiguredError):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)
    assert stub.validate_calls == 1


@pytest.mark.asyncio
async def test_validate_empty_id_raises_misconfigured() -> None:
    stub = FakeAuthStub(fields={**FAMILY_FIELDS, "id": "   "})
    with pytest.raises(AuthMisconfiguredError, match="missing user id"):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)


@pytest.mark.asyncio
async def test_validate_unauthenticated() -> None:
    stub = FakeAuthStub(
        error=FakeRpcError(grpc.StatusCode.UNAUTHENTICATED, "invalid session"),
    )
    with pytest.raises(AuthUnauthenticatedError, match="invalid session"):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)
    assert stub.validate_calls == 1


@pytest.mark.asyncio
async def test_validate_permission_denied_blocked() -> None:
    stub = FakeAuthStub(error=FakeRpcError(grpc.StatusCode.PERMISSION_DENIED, "blocked"))
    with pytest.raises(AuthBlockedError, match="blocked"):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)
    assert stub.validate_calls == 1


@pytest.mark.asyncio
async def test_validate_unavailable_retries_once_then_fails() -> None:
    stub = FakeAuthStub(
        error=FakeRpcError(grpc.StatusCode.UNAVAILABLE, "auth down"),
    )
    with pytest.raises(AuthUnavailableError):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)
    assert stub.validate_calls == 2


@pytest.mark.asyncio
async def test_validate_deadline_exceeded() -> None:
    stub = FakeAuthStub(
        error=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED, "deadline"),
    )
    with pytest.raises(AuthUnavailableError):
        await _client(stub).validate(SESSION_ID, CALLER_HOST)
    assert stub.validate_calls == 1


@pytest.mark.asyncio
async def test_list_users_maps_repeated_fields() -> None:
    stub = FakeAuthStub(users=[FAMILY_FIELDS, ADMIN_FIELDS])
    principals = await _client(stub).list_users(SESSION_ID, CALLER_HOST)

    assert [item.role for item in principals] == [Role.FAMILY, Role.ADMIN]
    assert [item.id for item in principals] == [FAMILY_USER_ID, ADMIN_USER_ID]
    assert principals[0].email == "family@example.test"
    assert principals[1].email == "admin@example.test"
    assert stub.list_calls == 1
    assert stub.last_request.session_id == SESSION_ID
    assert stub.last_request.caller_host == CALLER_HOST


@pytest.mark.asyncio
async def test_validate_uses_timeout_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_GRPC_TIMEOUT_MS", "3500")
    get_settings.cache_clear()
    stub = FakeAuthStub(fields=FAMILY_FIELDS)
    await AuthGrpcClient(stub=stub, settings=get_settings()).validate(
        SESSION_ID,
        CALLER_HOST,
    )
    assert stub.last_timeout == 3.5
