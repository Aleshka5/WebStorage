# ADR-003: JWT in httpOnly cookie (not Bearer header)

- **Status:** Accepted — **product JWT issuance superseded by [ADR-008](./ADR-008-auth-service-grpc-roles.md)** (session is Auth-Service `auth_session` + gRPC Validate). HttpOnly + credentialed cookies remain.
- **Date:** 2026-06-01
- **Related:** Auth routers, FE Axios `withCredentials: true`, [ADR-008](./ADR-008-auth-service-grpc-roles.md)

## Context

SPA needs session auth without exposing tokens to XSS via `localStorage`.

## Decision

Issue JWT (HS256) in HttpOnly cookie named `access_token` after login/OAuth bridge. Frontend never reads the token; all API calls use credentialed cookies. Logout clears cookie.

OAuth uses a short-lived Redis `ticket` to set the cookie after redirect.

## Consequences

### Positive

- Token not accessible to JS.
- Simple FE auth store (user profile only).

### Negative / Trade-offs

- CSRF surface requires SameSite (and ideally Secure on HTTPS).
- Non-browser clients must manage cookies explicitly.
- Current compose often runs HTTP locally — Secure flag may be off in dev (ops must enable TLS in production).

## Alternatives Considered

| Option | Why not |
|---|---|
| Bearer in memory | Lost on refresh; more FE complexity |
| localStorage JWT | XSS steals session |

## Follow-up (ADR-008)

HomeCloud no longer mints `access_token`. The SPA still never reads the session cookie; API calls keep `withCredentials: true`. Product session cookie is Auth-Service `auth_session`.
