# ADR-009: Gateway headers + User-Service directory + Common data plane

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** team
- **Supersedes:** [ADR-008](./ADR-008-auth-service-grpc-roles.md) (gRPC Validate / ListUsers)
- **Related:** Epic [E-GWUS](../epics/gateway-user-service/init.md), [ADR-001](./ADR-001-clean-architecture.md), [ADR-004](./ADR-004-private-key-redis-ttl.md)

## Context

Auth-Service no longer exposes gRPC. The public edge is host Caddy → Auth Gateway. User-Service is the people directory (`GET /users`, `GET /users/{id}/roles/storage`). Common owns Postgres, Redis, and MinIO. WebStorage is business logic only.

## Decision

1. **Identity:** Trust `X-User-Id` (else `X-Auth-User-Id`). Storage role from `X-Storage-Role` if it is a valid enum, else cached `GET {USER_SERVICE_URL}/users/{id}/roles/storage`. Never use `X-Auth-Role` / hub `global_role`.
2. **Fail closed:** User-Service 5xx / timeout / network / 404 / invalid role → `503 USER_SERVICE_UNAVAILABLE`. Malformed header role → `500`. Do not invent `STRANGER` on lookup failure. `200` with `STRANGER` is success.
3. **Local users:** Keep UUID + email projection. Drop `users.role` (and unused `password_hash` / `google_id`). Authorization uses the request principal only.
4. **Admin list:** `GET http://user_service:8000/users`, then join local quota. Missing `services[].storage` → `STRANGER`. Directory down → `503`.
5. **Vault:** Cookie `auth_session` → Redis `private_key:{sid}` on Common. No logout in this service.
6. **Compose:** Join `common_network`, `user_network`, and `auth_network` (external). Do not start Common. One MinIO bucket `storage`. No k8s manifests, no Caddy, no gRPC.

## Consequences

- Hub ADMIN is not storage ADMIN.
- The SPA must not redirect to Google on 401; the gateway already handled anonymous HTML.
- Production `app` serves API + built SPA as the gateway’s single upstream.
