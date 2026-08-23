from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

import grpc
from loguru import logger

from app.domain.entities.auth_principal import AuthPrincipal
from app.domain.exceptions import AuthUnavailableError
from config import Settings, get_settings

from . import auth_pb2, auth_pb2_grpc
from .mapping import exception_from_rpc_error, principal_from_fields

_SESSION_REF_LENGTH = 12


def _session_ref(session_id: str) -> str:
    """Short hash of a session id for logs. Never log the raw value."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:_SESSION_REF_LENGTH]


class AuthGrpcClient:
    """Plaintext Auth-Service gRPC client implementing the AuthValidator port.

    No Validate/ListUsers response cache. Retries once on Unavailable only.
    """

    def __init__(
        self,
        stub: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._injected_stub = stub
        self._channel: grpc.aio.Channel | None = None
        self._owned_stub: Any | None = None

    async def validate(self, session_id: str, caller_host: str) -> AuthPrincipal:
        request = auth_pb2.ValidateRequest(
            session_id=session_id,
            caller_host=caller_host,
        )
        logger.info(
            "Auth Validate started caller_host={} session_ref={}",
            caller_host,
            _session_ref(session_id),
        )
        stub = self._get_stub()
        response = await self._invoke(stub.Validate, request, caller_host)
        principal = principal_from_fields(response.fields)
        logger.info(
            "Auth Validate succeeded user_id={} caller_host={} grpc_code=OK",
            principal.id,
            caller_host,
        )
        return principal

    async def list_users(self, session_id: str, caller_host: str) -> list[AuthPrincipal]:
        request = auth_pb2.ListUsersRequest(
            session_id=session_id,
            caller_host=caller_host,
        )
        logger.info(
            "Auth ListUsers started caller_host={} session_ref={}",
            caller_host,
            _session_ref(session_id),
        )
        stub = self._get_stub()
        response = await self._invoke(stub.ListUsers, request, caller_host)
        principals = [principal_from_fields(user.fields) for user in response.users]
        logger.info(
            "Auth ListUsers succeeded caller_host={} grpc_code=OK user_count={}",
            caller_host,
            len(principals),
        )
        return principals

    def _timeout_seconds(self) -> float:
        return self._settings.auth_grpc.timeout_ms / 1000.0

    def _get_stub(self) -> Any:
        if self._injected_stub is not None:
            return self._injected_stub
        if self._owned_stub is None:
            addr = self._settings.auth_grpc.addr
            logger.info("Opening plaintext Auth gRPC channel to {}", addr)
            self._channel = grpc.aio.insecure_channel(addr)
            self._owned_stub = auth_pb2_grpc.AuthStub(self._channel)
        return self._owned_stub

    async def _invoke(
        self,
        rpc: Callable[..., Awaitable[Any]],
        request: Any,
        caller_host: str,
    ) -> Any:
        timeout = self._timeout_seconds()
        try:
            return await rpc(request, timeout=timeout)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                logger.warning(
                    "Auth gRPC Unavailable, retrying once addr={} caller_host={} grpc_code={}",
                    self._settings.auth_grpc.addr,
                    caller_host,
                    exc.code().name,
                )
                try:
                    return await rpc(request, timeout=timeout)
                except grpc.RpcError as retry_exc:
                    self._log_rpc_failure(caller_host, retry_exc)
                    raise exception_from_rpc_error(retry_exc) from retry_exc
            self._log_rpc_failure(caller_host, exc)
            raise exception_from_rpc_error(exc) from exc
        except TimeoutError as exc:
            logger.warning(
                "Auth gRPC deadline exceeded caller_host={} grpc_code=DEADLINE_EXCEEDED",
                caller_host,
            )
            raise AuthUnavailableError("Authentication service unavailable") from exc

    def _log_rpc_failure(self, caller_host: str, error: grpc.RpcError) -> None:
        logger.warning(
            "Auth gRPC failed addr={} caller_host={} grpc_code={}",
            self._settings.auth_grpc.addr,
            caller_host,
            error.code().name,
        )
