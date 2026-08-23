# ADR-008: Auth-Service owns product session + `storage_roles`

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** team
- **Related:** Epic [E-AUTHZ](../epics/auth-service-roles/init.md), [ADR-001](./ADR-001-clean-architecture.md), [ADR-003](./ADR-003-jwt-httponly-cookie.md) (product JWT issuance superseded), [ADR-004](./ADR-004-private-key-redis-ttl.md) (TTL unchanged; Redis key input follows `auth_session`), Master Document §3 / §8, TZ login / `AuthService`

## Context

HomeCloud today issues an HS256 JWT in cookie `access_token` (ADR-003) and authorizes from local Postgres `users.role`. Admin `PATCH /api/admin/users/{id}/role` mutates that column. Login is email/password plus in-app Google OAuth (ticket bridge).

Auth-Service is now the identity hub: Google OAuth2, Redis session cookie `auth_session`, per-service role enums, and gRPC `auth.v1.Auth/Validate` (and read-only `ListUsers`). Keeping a second product JWT and a writable local role column would let sessions and roles drift, and would make “Validate on every request” meaningless.

Constraints:

- Hub `roles` (`ADMIN` | `STRANGER`) has no `FAMILY`. HomeCloud’s capability matrix needs `ADMIN` | `FAMILY` | `STRANGER`.
- Session revoke, role change, and block must take effect on the **next** protected request (no caller-side Validate cache).
- Private-vault crypto and ADR-004 TTL stay local; only the Redis key input changes.
- Clean Architecture: gRPC client in Infrastructure; Application sees a port, not `grpcio` types.
- Auth-Service gRPC is plaintext LAN; port 9090 must not be published to the public internet.

## Decision

1. **Product session:** The browser holds Auth-Service cookie **`auth_session`** (opaque Redis session id). WebStorage **does not** issue, decode, or refresh a HomeCloud JWT `access_token` for HTTP APIs. It forwards the cookie value to gRPC and never logs the raw session id (truncate/hash only).
2. **Per-request Validate:** Every protected WebStorage request extracts `auth_session` and calls gRPC `Validate(session_id, AUTH_CALLER_HOST)` with **no caller cache**. One RPC per request (at most one retry on `Unavailable`). Missing cookie → 401 `UNAUTHORIZED` without calling Validate.
3. **Authorize with `storage_roles` only:** Map `fields["storage_roles"]` → HomeCloud `Role` (`ADMIN` | `FAMILY` | `STRANGER`). **Never** use hub `roles` for storage gates (`check_role`, Shared, Admin, quotas). A hub admin may be storage `STRANGER` and vice versa.
4. **Local `users` is a projection:** Upsert on first successful Validate, keyed by Auth-Service UUID (`fields["id"]`). Email from `google_email`. The local role column is **not** the source of truth (optional denormalized copy during migration; do not authorize from it). Files, quotas, ACL, and private prefix `users/{user_id}/` keep using that UUID.
5. **Role writes stay in Auth-Service:** HTTP admin `PATCH /api/admin/users/{id}` on the hub. No gRPC write API. Storage admin shows each user’s `storage_roles` as **immutable text** from gRPC `ListUsers` (Validate only projects the **caller**). Do not fall back to the local role column if ListUsers fails.
6. **Session expiry vs auth outage:**
   - Redis key `session:{sid}` TTL = Auth-Service `SESSION_TTL` (default **168h**), **refreshed** (`Touch`) on every successful Validate. Idle too long, logout, or missing session → Validate `Unauthenticated` → WebStorage **401** `UNAUTHORIZED` → SPA redirects to `AUTH_LOGIN_URL` (hub Google OAuth with `return_to` = storage). WebStorage does not mint a cookie and does not start OAuth itself.
   - Auth-Service down / timeout → **503** `AUTH_UNAVAILABLE`. SPA **must not** treat this as login expiry (no OAuth redirect loop).
   - Blocked user → Validate `PermissionDenied` `"blocked"` → **403** `ACCESS_DENIED` (not the login page).
7. **Private vault (ADR-004):** TTL `PRIVATE_SESSION_TTL_HOURS` stays local. Redis private key is keyed by **`auth_session`** (session id), not the old JWT string. Vault expiry → 401 `PRIVATE_SESSION_EXPIRED` (re-enter passphrase, **no** Google login). Product-session expiry still 401 `UNAUTHORIZED` (Google first; old vault key is unreachable).
8. **Identity migration (US-AUTHZ-11):** Prefer matching local `users.email` ↔ `google_email` (and `google_id` if projected) and **rewrite** local UUIDs to Auth-Service UUIDs so existing `users/{user_id}/` paths stay valid. Password-only users with no Google link cannot log in after this decision — report/export unmatched rows; **no silent orphan**. Do not copy local `role` into Auth-Service without an admin review list.

### Local / dev caller host

Auth-Service’s default whitelist copies **hub** fields (`google_email`, `name`, `roles`) onto `localhost` / `127.0.0.1`, **not** storage fields (`id`, `storage_roles`). Calling Validate as `localhost` therefore cannot authorize HomeCloud.

**Chosen local story:**

- **Production:** `AUTH_CALLER_HOST=storage.filenkov.store` (must match the whitelist after host normalize: scheme/port/path stripped). Cookie `Domain=.filenkov.store`.
- **Local storage:** do **not** rely on the default localhost projection. Operators must:
  1. set WebStorage `AUTH_CALLER_HOST=storage.filenkov.store` (Caddy / Host header so Auth-Service sees that host), **and/or**
  2. extend Auth-Service `WHITELIST_JSON` so `localhost` and `127.0.0.1` project **storage** fields: `id`, `google_email`, `name`, `storage_roles`.

`WHITELIST_JSON` cannot invent unknown field names; `id` on the storage host is an Auth-Service projector change (epic US-AUTHZ-02).

### Intentional deviation (TZ / Master Document / ADR-003)

| Source | Stated | This ADR |
|---|---|---|
| TZ / Product Brief; Master Document §3 / §8 | Email/password + Google OAuth **in HomeCloud**; JWT `access_token`; TTL `SESSION_TTL_SECONDS` | Google OAuth **only at the Auth hub**. HomeCloud does not register/login or mint product JWTs. Session TTL is Auth-Service `SESSION_TTL` (default 168h, sliding on Validate). |
| ADR-003 | WebStorage issues HS256 JWT in HttpOnly `access_token` | Product session is Auth-Service Redis cookie `auth_session`. **HttpOnly + credentialed cookies** (`withCredentials`) **retained**. |
| Local `users.role` | Source of truth; storage admin PATCH | Projection only. Writes in Auth-Service; storage UI is read-only text via `ListUsers`. |

ADR-004 private-vault TTL and `.marker` unlock remain valid and are **not** superseded.

## Consequences

### Positive

- Single identity and role source: Auth-Service Postgres enums + Redis session; revoke/role/block apply on the next request.
- HomeCloud keeps STRANGER / FAMILY / ADMIN without overloading hub `roles`.
- SPA never reads the session cookie (HttpOnly); auth store stays profile-only (`/api/auth/me`).
- Private vault UX unchanged (separate TTL, separate 401 code).

### Negative / Trade-offs

- Auth-Service is on the path of every protected request; outage → 503, not degraded local JWT.
- Sliding 168h session is longer than today’s absolute 24h JWT; idle timeout is “168h after last Validate”, not “168h after login”.
- Dual admin concepts: hub `roles=ADMIN` ≠ `storage_roles=ADMIN`; storage admin must not call hub HTTP user-list (that API requires hub admin).
- Password-only HomeCloud users cannot sign in after cutover (migration report required).
- LAN gRPC is unauthenticated at the transport layer; trust is network isolation (do not publish 9090).

### Follow-ups

- Settings / `.env.example`: `AUTH_GRPC_ADDR`, `AUTH_CALLER_HOST`, `AUTH_COOKIE_NAME`, `AUTH_GRPC_TIMEOUT_MS`, `AUTH_LOGIN_URL`, `AUTH_LOGOUT_URL` (US-AUTHZ-03).
- Auth-Service: project `id` + `google_email` + `name` + `storage_roles` for the storage host; add read-only `ListUsers` (US-AUTHZ-02 / 02b).
- `AuthValidator` port + `get_current_user` Validate; retire local register/login/Google/JWT (US-AUTHZ-04 … 06).
- Admin role column as immutable text; private Redis key from `auth_session` (US-AUTHZ-07 / 08).
- Master Document, API Contract, Flow Spec, Test Spec, E-AUTH, E-ADMIN — consistency updates (epic DoD).
- User UUID remap script (US-AUTHZ-11).

## Alternatives Considered

| Option | Why not |
|---|---|
| Keep local JWT and only fetch roles from Auth-Service | HomeCloud still mints sessions; revoke/block/role change can lag; two sources of truth; contradicts full SSO (assumption A1) |
| Embed roles in a HomeCloud JWT after one Validate | Caller-side cache of roles/session; next request would not see hub PATCH, logout, or block; proto requires no Validate cache |
| Hybrid: keep email/password in WebStorage + hub Google | Role and user-id drift between two tables; Validate is optional on some paths |
| Authorize with hub `roles` | No `FAMILY`; storage-only admins would be denied or over-privileged incorrectly |
| Cache Validate in Redis/memory | Session revoke and role writes would not apply on the next request |
| Forward hub `GET /api/admin/users` for the role column | Hub HTTP requires hub `roles=ADMIN`; a storage-only admin would get 403 |
| Bearer header / SPA-readable token | XSS can steal the session; contradicts retained HttpOnly cookie principle (ADR-003) |

## Notes

```text
Browser ──Cookie: auth_session (Redis sid)──► WebStorage
                                               └─ gRPC Validate(sid, AUTH_CALLER_HOST)
                                                    → fields[id, google_email, name, storage_roles]
                                               └─ upsert local user; check_role(storage_roles)
```

```text
idle > SESSION_TTL  or  logout  or  session missing
        → Validate Unauthenticated → HTTP 401 UNAUTHORIZED
        → SPA AUTH_LOGIN_URL (Google) → new auth_session → storage

Auth-Service down / timeout
        → HTTP 503 AUTH_UNAVAILABLE  (no OAuth redirect)
```

- Epic: [docs/epics/auth-service-roles/init.md](../epics/auth-service-roles/init.md) (US-AUTHZ-01 … US-AUTHZ-12).
- Logout: WebStorage forwards session invalidation to Auth-Service on the LAN (hub has no CORS); then `Set-Cookie` clears `auth_session`.
