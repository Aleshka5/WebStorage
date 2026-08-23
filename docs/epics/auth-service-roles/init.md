# Epic: Auth-Service Role Management via gRPC Validate

> **ID:** E-AUTHZ  
> **Status:** Planned  
> **Repos:** WebStorage (this epic) + sibling **Auth-Service** (`/home/aleksey/projects/Auth-Service`)  
> **Specs:** [Master Document §4](../../Master%20Document.md), [API Contract §3 / §9](../../API%20Contract.md), [Flow Spec §2 / §7](../../Flow%20Spec.md), [Test Spec](../../Test%20Spec.md), [ADR-001](../../adr/ADR-001-clean-architecture.md), [ADR-003](../../adr/ADR-003-jwt-httponly-cookie.md), [ADR-004](../../adr/ADR-004-private-key-redis-ttl.md), Auth-Service `proto/auth.proto` + README “gRPC Validate”  
> **Supersedes:** local role source of truth (`users.role`); product session JWT in `access_token` (ADR-003) for HomeCloud HTTP APIs  
> **Does not supersede:** ADR-004 private-vault Redis key (TTL stays local; only the Redis key input changes)

## Overview

Replace HomeCloud’s **local role management and product session** with Auth-Service:

1. The browser authenticates at the Auth hub (Google OAuth2, cookie `auth_session` on `.filenkov.store`).
2. **Every authenticated WebStorage HTTP request** extracts that cookie and calls gRPC `auth.v1.Auth/Validate` (no caller-side cache).
3. Authorization uses the projected field **`storage_roles`** (`ADMIN` | `FAMILY` | `STRANGER`), not hub `roles` (`ADMIN` | `STRANGER` only).
4. HomeCloud keeps PostgreSQL for **files, quotas, private vault, ACL** — keyed by Auth-Service user UUID.

Today `get_current_user()` decodes a local HS256 JWT (`access_token`) and loads `User.role` from WebStorage Postgres. Admin `PATCH /api/admin/users/{id}/role` mutates that column. None of that is the source of truth after this epic.

## Assumptions (locked unless the team rejects them)

These match Auth-Service’s published contract (`README.md`, `proto/auth.proto`). If any assumption is wrong, stop and revise this epic before implementation.

| # | Assumption | Why |
|---|---|---|
| A1 | **Full SSO**, not a hybrid. WebStorage stops issuing product JWTs and stops email/password + in-app Google OAuth as login. | Auth-Service is the session issuer; “Validate on every request” is meaningless if HomeCloud still mints `access_token`. |
| A2 | **No Validate cache** in WebStorage (per proto comment). | Session revoke / role change / block must take effect on the next request. |
| A3 | Authorize with **`storage_roles`**, never hub `roles`. | Hub `roles` has no `FAMILY`; storage capabilities live in the per-service enum. |
| A4 | Auth-Service whitelist for `storage.filenkov.store` is **extended** to project `id` and `google_email` (code change — env `WHITELIST_JSON` cannot add unknown fields today). | Default projection is only `name` + `storage_roles`. Files/quotas need a stable UUID. |
| A5 | Local `users` row remains as a **projection** (upsert on first Validate). Role column is not authoritative. | `file_records.user_id`, quota, private prefix `users/{user_id}/`. |
| A6 | **Role writes stay in Auth-Service** (HTTP admin `PATCH /api/admin/users/{id}`). No gRPC **write** API. | Validate/ListUsers are read-only. WebStorage admin shows role as **immutable text**. |
| A7 | Private vault Redis key uses `auth_session` (or `user_id + session_id`), not the old JWT string (ADR-004 still valid). | `_get_session_id()` currently reads `access_token`. |
| A8 | Storage admin user table shows **live** `storage_roles` for every listed user (plain text). | `Validate` only returns the **caller**. Listing everyone needs a second read RPC (`ListUsers`) — not a stale local column and not hub HTTP (that API requires hub `roles=ADMIN`, which a storage-only admin may lack). |

## Goals

- Single identity and role source: Auth-Service Postgres enums + Redis session.
- Per-request gRPC Validate from the FastAPI auth dependency (the choke point).
- Keep HomeCloud capability matrix: STRANGER / FAMILY / ADMIN for files, shared, admin, quotas.
- Fail closed: missing cookie, bad session, blocked user, unknown `caller_host`, or gRPC outage must not serve protected APIs.
- Clean Architecture: gRPC client lives in Infrastructure; Application sees a port (`AuthValidator` / similar), not `grpcio` types.

## Non-goals (this epic)

- Replacing private-vault crypto or ADR-004 TTL semantics.
- gRPC Logout or gRPC role **writes** (hub HTTP admin stays the writer).
- Making WebStorage an OAuth provider for other products.
- Caching Validate in Redis/memory.
- Changing blob storage (MinIO epic is independent).
- Hub `roles` for HomeCloud authorization.

**In scope (read-only):** gRPC `ListUsers` so the storage admin table can show each user’s `storage_roles` as text.

---

## Session / “token” expiry — what each service does

There is **no product JWT** after this epic. The browser holds Auth-Service cookie `auth_session` (opaque Redis session id). HomeCloud never decodes it; it only forwards the value to gRPC.

### Product session (`auth_session`)

| Step | Who | What |
|---|---|---|
| 1 | Auth-Service | Session lives in Redis `session:{sid}` with TTL `SESSION_TTL` (default **168h**). |
| 2 | Auth-Service | Every **successful** `Validate` calls `Touch` → TTL is **refreshed**. An active user does not expire after 168h from login; they expire **168h after the last successful Validate**. |
| 3 | Auth-Service | Idle too long, logout, or block (all sessions revoked) → Redis key gone. Next `Validate` → gRPC `Unauthenticated` `"invalid session"`. |
| 4 | WebStorage | Maps that to HTTP **401** `UNAUTHORIZED`. Does **not** mint a new cookie. Does **not** retry OAuth by itself. |
| 5 | WebStorage FE | On 401 from `/api/*` (except private-vault code): stop the request, send the browser to `AUTH_LOGIN_URL` (hub Google OAuth with `return_to` = storage). |
| 6 | Auth-Service | New Google login creates a **new** session id, sets a new `auth_session` cookie, redirects back to storage. |

**Cookie still present but dead:** the browser may keep `auth_session` after Redis expiry. WebStorage still gets 401. Logout / login redirect should `Set-Cookie` clear it so the user does not loop on a stale id.

**Auth-Service down / timeout:** not “expired”. WebStorage returns **503** `AUTH_UNAVAILABLE`. FE must **not** treat this as login-expired (no OAuth redirect loop).

**Blocked user:** session may still exist; `Validate` returns `PermissionDenied` `"blocked"` → WebStorage **403** `ACCESS_DENIED` (not the login page).

```text
idle > SESSION_TTL  or  logout  or  session missing
        │
        ▼
Auth-Service Validate → Unauthenticated
        │
        ▼
WebStorage API → 401 UNAUTHORIZED
        │
        ▼
SPA → redirect AUTH_LOGIN_URL (Google)
        │
        ▼
Auth-Service new session cookie → back to storage
```

### Private vault (unchanged idea, different cookie)

| Event | HTTP | User does |
|---|---|---|
| Product session valid, vault Redis TTL expired | 401 `PRIVATE_SESSION_EXPIRED` | Re-enter passphrase. **No** Google login. |
| Product session expired / missing | 401 `UNAUTHORIZED` | Google login first; vault key is gone with the old session id. |

### Today (before E-AUTHZ), for contrast

Local JWT `access_token` has **absolute** `exp` (`SESSION_TTL_SECONDS`, default 24h) and is **not** refreshed on each request. When `exp` passes, `get_current_user` returns 401 and the SPA goes to `/auth`. That path is retired by this epic.

## Current vs target

```text
TODAY
  Browser ──Cookie: access_token (JWT)──► WebStorage
                                            ├─ jwt.decode(JWT_SECRET)
                                            └─ SELECT users.role  → check_role()

TARGET
  Browser ──Cookie: auth_session (Redis sid)──► WebStorage
                                                 └─ gRPC Validate(sid, AUTH_CALLER_HOST)
                                                      Auth-Service api:9090
                                                      → fields[id, google_email, name, storage_roles]
                                                 └─ upsert local user; check_role(storage_roles)
```

## User Stories

Each story has **acceptance**, **tests** (Test Spec pyramid), and a **story DoD**. Epic DoD is at the bottom.

---

### US-AUTHZ-01 — ADR-008: Auth-Service owns session and `storage_roles`
> **ADR:** [ADR-008 — Auth-Service owns product session + `storage_roles`](../../adr/ADR-008-auth-service-grpc-roles.md) (Accepted)

**As an** architect, **I want** an accepted ADR, **so that** TZ / Master Document / ADR-003 deviations are explicit.

**Acceptance:**

- ADR-008 Accepted: cookie `auth_session`; per-request gRPC Validate; `storage_roles` is HomeCloud’s role; local JWT retired for product APIs.
- ADR-003 marked superseded (or “superseded for HomeCloud product APIs”; HttpOnly cookie principle retained).
- ADR-004 updated: Redis private key input is session id from `auth_session`, not JWT.
- ADR index + this epic link the ADR.
- Documents that hub `roles` ≠ `storage_roles`.

**Tests:**

- Docs-only. Review checklist: ADR template sections present; alternatives include “keep local JWT and only fetch roles” and “embed roles in a HomeCloud JWT after one Validate”.

**Story DoD:**

- [x] ADR-008 in `docs/adr/`; index updated; ADR-003/004 status lines updated.

---

### US-AUTHZ-02 — Auth-Service: project `id` (+ `google_email`) for storage host

**Repo:** Auth-Service  
**As a** storage backend, **I need** a stable user id from Validate, **so that** I can key files and quotas.

**Acceptance:**

- Projector in `internal/whitelist/whitelist.go` supports `id` (Auth-Service UUID string). Optionally `google_sub` — not required if `id` + `google_email` exist.
- Default whitelist `storage.filenkov.store` → `id`, `google_email`, `name`, `storage_roles`.
- `WHITELIST_JSON` can override but must not be the only way to get `id`.
- Existing hosts (`filenkov.store`, `resume.filenkov.store`) keep current field sets unless explicitly changed.
- Unknown field names still fail projection (`Internal` / projector error).

**Tests (Auth-Service, `go test`):**

| Layer | Case | Expect |
|---|---|---|
| Unit | `Project("storage.filenkov.store", user)` | map contains `id` == `user.ID.String()`, `google_email`, `name`, `storage_roles` |
| Unit | `Project` with unknown field `"uuid"` | error |
| Unit | `Project("resume.filenkov.store", …)` | still only `name`, `resume_roles` |
| gRPC | Validate valid session + `caller_host=storage.filenkov.store` | OK + `fields["id"]` and `fields["storage_roles"]` |
| gRPC | blocked user | `PermissionDenied` `"blocked"` — no fields |
| gRPC | unknown host | `PermissionDenied` `"unknown caller_host"` |
| Config | `TestDefaultWhitelist` | storage host includes `id` |

**Story DoD:**

- [ ] Projector + default whitelist + tests green (`go test ./...`).
- [ ] Auth-Service README table for gRPC fields updated.

---

### US-AUTHZ-02b — Auth-Service: read-only `ListUsers` for storage admin

**Repo:** Auth-Service  
**As a** storage admin UI, **I need every user’s `storage_roles` in one call**, **so that** HomeCloud can render the role column as immutable text (Validate cannot do this — it only projects the **caller**).

**Why this is required:** forwarding `GET /api/admin/users` from the hub is **not** enough. That HTTP route requires hub `roles=ADMIN`. A user can be `storage_roles=ADMIN` and `roles=STRANGER` and would get 403 from the hub while still being allowed on `/api/admin` in storage.

**Acceptance:**

Add to `proto/auth.proto` (same service `Auth`):

```protobuf
rpc ListUsers(ListUsersRequest) returns (ListUsersResponse);

message ListUsersRequest {
  string session_id = 1;
  string caller_host = 2;
}
message ListUser {
  map<string, string> fields = 1;  // same whitelist projection as Validate
}
message ListUsersResponse {
  repeated ListUser users = 1;
}
```

- Authenticate like `Validate` (session, not blocked, known host).
- Authorize: after projecting the **caller**, require `storage_roles=ADMIN` when `caller_host` is the storage host (for resume host, `resume_roles=ADMIN`, etc.). If the caller is not service-admin → `PermissionDenied`.
- Body: every user in Auth-Service Postgres, each projected with the **same field set** as Validate for that host (`id`, `google_email`, `name`, `storage_roles` for storage).
- Do **not** include users’ sessions. Do **not** accept role patches on this RPC.
- LAN gRPC remains unauthenticated at the transport layer (same as Validate); authorization is the caller’s session.

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| gRPC | Caller `storage_roles=ADMIN`, valid session | OK; each item has `id` + `storage_roles`; count = all users |
| gRPC | Caller `storage_roles=FAMILY` | `PermissionDenied` |
| gRPC | Bad/expired session | `Unauthenticated` |
| gRPC | After hub PATCH `storage_roles` on user B | next `ListUsers` shows new value (no cache) |
| gRPC | Blocked caller | `PermissionDenied` `"blocked"` |

**Story DoD:**

- [ ] Proto + Go server + stubs regenerated; tests green; README documents `ListUsers`.

---

### US-AUTHZ-03 — WebStorage settings for the gRPC client

**As an** operator, **I configure** Auth-Service connectivity only via `get_settings()`.

**Acceptance:**

- Nested settings (e.g. `settings.auth_grpc.*`), no `os.getenv` in app code.
- `.env.example` lists every variable (placeholders, no real secrets).

Suggested vars:

| Variable | Example | Purpose |
|---|---|---|
| `AUTH_GRPC_ADDR` | `api:9090` | Dial target (compose DNS or host) |
| `AUTH_CALLER_HOST` | `storage.filenkov.store` | Must match Auth-Service whitelist after normalize (scheme/port/path stripped) |
| `AUTH_COOKIE_NAME` | `auth_session` | Must match Auth-Service `COOKIE_NAME` |
| `AUTH_GRPC_TIMEOUT_MS` | `2000` | Per-request deadline |
| `AUTH_LOGIN_URL` | `https://filenkov.store/oauth/google?return_to=https://storage.filenkov.store/` | Unauthenticated browser redirect |
| `AUTH_LOGOUT_URL` | `http://api:8080` or hub origin | Server-side logout forward (see US-AUTHZ-06) |

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| Unit | Load Settings from env; `get_settings.cache_clear()` between tests | All fields present |
| Unit | Missing required `AUTH_GRPC_ADDR` in production-like config | Fail fast or documented default |
| Grep/CI | No new `os.getenv` in `backend/app` | Pass (Test Spec §5) |

**Story DoD:**

- [ ] Settings + `.env.example` + unit test.

---

### US-AUTHZ-04 — Infrastructure: `AuthValidator` port + gRPC client

**As a** backend developer, **I have** a typed client behind a port, **so that** Application never imports `grpcio`.

**Acceptance:**

- Domain/Application port, e.g. `AuthValidator.validate(...)` and `list_users(...)` (`ListUsers`, US-AUTHZ-02b).
- `AuthPrincipal`: `id: UUID`, `email: str`, `name: str`, `role: Role` (parsed from `storage_roles`).
- Infrastructure: generate Python stubs from Auth-Service `proto/auth.proto` (checked in or build step); plaintext dial to `AUTH_GRPC_ADDR` (LAN, matches current Auth-Service server — no TLS/interceptors).
- Map gRPC codes:

| gRPC | HTTP | `error_code` |
|---|---|---|
| `Unauthenticated` | 401 | `UNAUTHORIZED` |
| `PermissionDenied` (`blocked`) | 403 | `ACCESS_DENIED` (or dedicated `USER_BLOCKED` if added to API Contract) |
| `PermissionDenied` (`unknown caller_host`) | 403 | `ACCESS_DENIED` |
| `InvalidArgument` | 401 | `UNAUTHORIZED` |
| `Unavailable` / deadline | 503 | `AUTH_UNAVAILABLE` (**new** contract code) |
| Missing `id` or `storage_roles` in fields | 500 | `INTERNAL_ERROR` (misconfigured whitelist) |

- Deadline = `AUTH_GRPC_TIMEOUT_MS`. No response cache. One Validate per request (retries only for `Unavailable`, max 1 retry, still no cache).
- Logging: `user_id`, `caller_host`, gRPC code; **never** full session id (truncate/hash).

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| Unit | Fake gRPC stub returns fields | `AuthPrincipal.role == Role.FAMILY` |
| Unit | Invalid `storage_roles` string | error, not silent STRANGER |
| Unit | `Unauthenticated` | mapped exception / 401 path |
| Unit | `PermissionDenied` blocked | 403 |
| Unit | timeout | 503 `AUTH_UNAVAILABLE` |
| Unit | empty `id` | internal error |
| Contract | Generated stubs match `auth.proto` (`ValidateRequest.session_id`, `caller_host`) | compile |

**Story DoD:**

- [ ] Port + client + mapping tests; Domain has zero grpc imports.

---

### US-AUTHZ-05 — `get_current_user` calls Validate on every request

**As a** security owner, **I want** the existing FastAPI dependency to be the only choke point, **so that** every protected route is covered without a global middleware rewrite.

**Acceptance:**

- `get_current_user` reads `request.cookies[AUTH_COOKIE_NAME]` (default `auth_session`).
- Missing cookie → 401 `UNAUTHORIZED` (same shape as today).
- Calls `AuthValidator` with `AUTH_CALLER_HOST` every time.
- Upsert local user: `id` = Auth UUID, `email` = `google_email`, `google_id` nullable/unused for new users, `is_active` derived from Validate success (blocked never upserts as active).
- **Do not** use local `users.role` for authorization. Set `User.role` on the domain object from `storage_roles` for this request (memory), even if a denormalized DB column still exists during migration.
- `check_role()` unchanged in behavior: compares `current_user.role` to allowed roles.
- Public routes stay public: `/`, `/health`, and the **new** unauthenticated login redirect helper if any.
- Quota resolution still uses `Role` VO (`STRANGER_QUOTA_MB` vs free disk).

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| API | Authenticated `GET /api/auth/me` with valid cookie | 200; `user_id` == Auth UUID; `role` == `storage_roles`; **exactly one** Validate call (spy) |
| API | Second request same cookie | **second** Validate call (proves no cache) |
| API | No cookie on `GET /api/files` | 401 `UNAUTHORIZED`; zero Validate calls |
| API | Validate Unauthenticated | 401; no file I/O |
| API | Validate blocked | 403; not 401 |
| API | Role FAMILY then admin PATCH in Auth-Service to STRANGER (simulated stub) | next `GET /api/shared` → 403 |
| API | Auth-Service down | 503 `AUTH_UNAVAILABLE` |
| Unit | Upsert: first visit creates local user + quota row; second visit does not duplicate | 1 row |

**Story DoD:**

- [ ] Dependency swapped; all existing `Depends(get_current_user)` / `check_role` routes inherit Validate.
- [ ] Tests above green with a fake validator (and one optional live gRPC test behind a marker).

---

### US-AUTHZ-06 — Retire local product auth (register / login / Google / JWT)

**As a** platform owner, **I want** a single login path, **so that** roles cannot drift between two user tables.

**Acceptance:**

- Remove or permanently 410/301: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/google`, callback, session ticket.
- `GET /api/auth/me` remains (backed by Validate + local projection).
- `POST /api/auth/logout`: WebStorage **must not** only delete a local JWT. Forward session invalidation to Auth-Service `POST /api/logout` on the LAN (cookie attached), then `Set-Cookie` clear `auth_session` for `.filenkov.store` (and host-only in dev). Auth-Service has **no CORS**; do not call hub logout from the SPA cross-origin.
- Unauthenticated SPA: redirect browser to `AUTH_LOGIN_URL` (`/oauth/google?return_to=` whitelist host `storage.filenkov.store` or local `localhost` when `AUTH_DEV_HTTP`).
- Stop using `JWT_SECRET` for product sessions (may remain unused until removed from settings).
- `init_db.py` must not create a local ADMIN with password as the production bootstrap; document Auth-Service `ADMIN_EMAIL` instead.
- Rate-limit middleware on register/login can be removed or reduced to unused paths.

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| API | `POST /api/auth/login` | 410/404/301 — **not** 200 + `access_token` |
| API | `POST /api/auth/register` | same |
| API | `GET /api/auth/me` with `auth_session` | 200 |
| API | `POST /api/auth/logout` | Auth-Service session gone (fake HTTP); subsequent Validate Unauthenticated |
| FE unit | Unauthenticated `ProtectedRoute` | Redirect to login URL, not old `/auth` password form (or `/auth` page that immediately redirects) |
| FE | Auth store still maps `UNAUTHORIZED` | Redirect |

**Story DoD:**

- [ ] API Contract §3 rewritten; Flow Spec §2 OAuth-in-app replaced with hub redirect.
- [ ] No `Set-Cookie: access_token` in WebStorage.
- [ ] Tests above.

---

### US-AUTHZ-07 — Admin page: role is immutable text; storage ops stay

**As a** storage admin, **I see each user’s `storage_roles` as read-only text**, **so that** I cannot change roles in HomeCloud (that is Auth-Service admin).

**Acceptance:**

- **UI (AdminPage user table):** replace the role `<select>` with a non-interactive text node (e.g. `<span>FAMILY</span>`). Not a disabled select. No `onChange`, no `updateUserRole`. Same for every row, including the current admin (no special “you cannot edit yourself” control — nobody can edit here).
- Role filter chips (`ALL` / `STRANGER` / `FAMILY` / `ADMIN`) may stay; they filter the list, they do not mutate.
- Remove FE `updateUserRole` and `PATCH /api/admin/users/{id}/role` (410/404).
- **`GET /api/admin/users`:** each item includes `role` (string = live `storage_roles`). Implementation: after `Validate` of the admin, call gRPC `ListUsers` (US-AUTHZ-02b); join local quota / `is_active` projection / usage by `id`. Do **not** serve `users.role` from Postgres as source of truth.
- If ListUsers fails: 503 `AUTH_UNAVAILABLE` (same as Validate outage) — do not fall back to a writable local role.
- `PATCH .../quota`, storage health, archive, backup, maintenance stay in WebStorage, gated by **`storage_roles=ADMIN`**.
- Account **block** remains Auth-Service; local `is_active` alone must not be the block.
- Optional: small hint near the column, e.g. “Роль меняется в Auth-Service /admin” — no inline editor.

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| API | FAMILY any `/api/admin/*` | 403; Validate invoked |
| API | `storage_roles=ADMIN` `GET /api/admin/users` | 200; each `role` matches ListUsers stub; **one** ListUsers per request |
| API | Hub changed user B to STRANGER; admin reloads list | `role` is `STRANGER` without B visiting storage |
| API | `PATCH /api/admin/users/{id}/role` | 410/404; no DB write |
| API | ListUsers Unavailable | 503 `AUTH_UNAVAILABLE` |
| FE component | `AdminPage` | no `<select>` for role; text equals API `role`; `updateUserRole` never called |
| FE | Changing filter to FAMILY | list filtered; still no mutation |

**Story DoD:**

- [ ] API Contract §9: role field read-only; PATCH role removed.
- [ ] AdminPage role column is text-only.
- [ ] Tests above.

---

### US-AUTHZ-08 — Private vault session key follows `auth_session`

**As a** private-storage user, **I stay unlocked for `PRIVATE_SESSION_TTL_HOURS`**, **so that** vault UX is unchanged while product auth moves.

**Acceptance:**

- `_get_session_id()` uses `AUTH_COOKIE_NAME`.
- Redis key must not collide if session ids are hex (Auth-Service 64-byte hex) vs old JWTs.
- Expiry still returns 401 `PRIVATE_SESSION_EXPIRED` **without** requiring a new Google login (ADR-004).
- Logout / Auth-Service session delete: private Redis key should be deleted when WebStorage logout runs (best-effort); if session id changes, old vault key is unreachable (acceptable).

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| API | Unlock private then `GET` file | 200 |
| API | Unlock, then missing/invalid `auth_session` | 401 `UNAUTHORIZED` (not only private expiry) |
| API | Unlock, cookie valid, Redis private TTL expired | 401 `PRIVATE_SESSION_EXPIRED` |
| Unit | Session store key uses cookie value from settings name | not hardcoded `access_token` |

**Story DoD:**

- [ ] ADR-004 follow-up note; private tests updated.

---

### US-AUTHZ-09 — Frontend: SSO cookie, role UX, no password login

**As a** family user, **I sign in once at the hub and use storage**, **so that** Shared/Admin nav still matches `storage_roles`.

**Acceptance:**

- Axios `withCredentials: true` unchanged; cookie is `auth_session` (HttpOnly — FE still never reads it).
- `GET /api/auth/me` remains the bootstrap; store `{ user_id, email, role }` where `role` is HomeCloud role from me.
- Sidebar: Shared for FAMILY/ADMIN; Admin for ADMIN — same matrix.
- Auth page: no email/password register/login against WebStorage; CTA → hub OAuth `return_to`.
- 401 on API → redirect to login URL (not a local password form).
- 503 `AUTH_UNAVAILABLE` → explicit error, not infinite login loop.

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| Unit | Auth store `fetchMe` 200 | role mapped |
| Component | `Sidebar` STRANGER | no Shared, no Admin |
| Component | `Sidebar` FAMILY | Shared, no Admin |
| Component | `ProtectedRoute` 401 | redirect login URL |
| Component | `AdminPage` non-ADMIN | redirect `/files` |
| E2E | STRANGER cannot open `/shared` or `/admin` | Test Spec §4.3.4 |

**Story DoD:**

- [x] FE AuthPage/OAuth-in-app removed or reduced to redirect.
- [x] FRONTEND_DOCS + Flow Spec UI notes updated.

---

### US-AUTHZ-10 — Compose, Caddy, network: reach `api:9090`

**As an** operator, **I run Auth-Service and WebStorage so Validate works**.

**Acceptance:**

- WebStorage app container can dial `AUTH_GRPC_ADDR` (shared Docker network or documented host). Auth-Service compose **does not publish 9090** by default — join the `auth` network or add an explicit internal network; do not expose gRPC to the public internet.
- `AUTH_CALLER_HOST` matches the public host Caddy uses (`storage.filenkov.store` in prod; `localhost` only if Auth-Service whitelist has it — **dev:** Auth-Service copies hub fields to `localhost`/`127.0.0.1`, **not** storage fields — **gap:** local storage Validate may need `WHITELIST_JSON` for `localhost` with **storage** fields, or a dedicated `AUTH_CALLER_HOST=storage.filenkov.store` even in dev plus Caddy Host header. Document the chosen dev story in ADR-008).
- Cookie `Domain=.filenkov.store` so `storage.filenkov.store` receives `auth_session`. Local HTTP: `AUTH_DEV_HTTP` host-only cookie implies **same host** as the hub, or a documented workaround (Caddy on localhost).
- README: login URL, gRPC addr, “do not cache”, whitelist host, do not publish 9090 publicly.

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| Ops smoke | `docker compose` WebStorage + Auth-Service | one `GET /api/auth/me` with cookie succeeds |
| Docs | `.env.example` + README section | vars match US-AUTHZ-03 |
| Negative | Wrong `AUTH_CALLER_HOST` | 403 unknown host |

**Story DoD:**

- [x] Compose/network + README; smoke path documented.

---

### US-AUTHZ-11 — Migrate existing HomeCloud users to Auth UUIDs

**As an** operator with existing files, **I map old `users.id` to Auth-Service ids**, **so that** `users/{user_id}/` prefixes and `file_records` stay valid.

**Acceptance:**

- Mapping strategy (pick one, record in ADR-008):
  1. **Preferred:** match `users.email` ↔ `google_email` / `users.google_id` ↔ future `google_sub` if projected; rewrite local UUID to Auth UUID **or** keep a `auth_user_id` column and stop using local UUID in new paths (paths today use local UUID — rewrite is safer for one-time migration).
  2. If no Google link: those password-only users cannot login after A1 — export/communicate; no silent orphan.
- Quota rows and `file_records.user_id` follow the same id.
- Dry-run + checksum of row counts; loguru without emails dumped in full if avoidable (or hash).
- Do not copy local `role` into Auth-Service automatically without an admin review list (roles may be wrong).

**Tests:**

| Layer | Case | Expect |
|---|---|---|
| Unit | Two local users, one matching Auth email | one remapped, one reported unmatched |
| Unit | Dry-run | no DB writes |
| Integration | After remap, Validate `id` loads same files | list non-empty |

**Story DoD:**

- [x] Script + README; unmatched users report.

---

### US-AUTHZ-12 — Regression: role matrix on gRPC-backed auth

**As a** QA owner, **I prove** the Master Document matrix still holds when roles come from Validate.

**Acceptance:** Re-run Test Spec authz cases with a **fake AuthValidator** in CI (no live Auth-Service required) plus one optional compose-marked live test.

**Tests:**

| Role (`storage_roles`) | `/api/files` | `/api/shared` | `/api/admin/*` |
|---|---|---|---|
| none / invalid session | 401 | 401 | 401 |
| STRANGER | 200 | 403 | 403 |
| FAMILY | 200 | 200 | 403 |
| ADMIN | 200 | 200 | 200 |
| blocked | 403 | 403 | 403 |

Additional:

- Shared delete ACL: owner or `storage_roles=ADMIN` (existing `FileService` rule).
- `Role.can_access_shared` / `can_access_admin` unit matrix unchanged.
- No `access_token` cookie in responses.
- Logs on deny include `user_id` + role, not session secret.

**Story DoD:**

- [ ] pytest matrix in CI; Test Spec §3.3 Auth/Admin/Shared updated to gRPC.

---

## Parallelization matrix

```text
Wave 1 (parallel)          Wave 2              Wave 3 (parallel)           Wave 4
┌──────────────────┐       ┌────────────┐      ┌─────────────────┐        ┌────────────┐
│ US-AUTHZ-01 ADR  │       │ US-AUTHZ-04│      │ US-AUTHZ-05 dep │        │ US-AUTHZ-11│
│ US-AUTHZ-02 AS   │──────►│ gRPC port  │─────►│ US-AUTHZ-08 pvt │───────►│ migrate    │
│ US-AUTHZ-02b List│       │ Validate + │      │ US-AUTHZ-09 FE  │        │ US-AUTHZ-12│
│ US-AUTHZ-03 env  │       │ ListUsers  │      │ US-AUTHZ-06 kill│        │ tests      │
│ US-AUTHZ-10 ops* │       └────────────┘      │ US-AUTHZ-07 adm │        └────────────┘
└──────────────────┘                           └─────────────────┘
```

\* US-AUTHZ-10 compose can start in wave 1 but smoke needs 04–05.

| Story | Parallel with | Blocked by |
|---|---|---|
| 01 ADR | 02, 02b, 03, 10 | — |
| 02 Auth-Service whitelist | 01, 02b, 03 | — |
| 02b ListUsers RPC | 01, 02, 03 | 02 field names (can land in same Auth-Service PR as 02) |
| 03 settings | 01, 02 | — |
| 04 gRPC client | — | 02, 02b, 03 |
| 05 get_current_user | 08, 09 after 05 starts | 04 |
| 06 retire local auth | 07, 09 | 05 (`/me` works) |
| 07 admin text roles | 06 | 04 (`list_users`) + 05 |
| 08 private key | 05 | 03 cookie name |
| 09 FE | 06 | 05 `/me` |
| 10 ops | 01–03 | smoke after 05 |
| 11 migrate | — | 05 |
| 12 matrix | — | 05–09 |

**Do not** ship 06 (remove login) before 05 is green. **Do not** parallelize 04 with 05 (same dependency module).

---

## Architecture Consistency Review

### Compatible

| Principle / doc | Verdict | Notes |
|---|---|---|
| ADR-001 Clean Architecture | **Compatible** | gRPC client + stub gen in Infrastructure; port in Application/Domain. |
| Role matrix STRANGER/FAMILY/ADMIN | **Compatible** | Same `Role` VO; source becomes `storage_roles`. |
| Shared / admin gates | **Compatible** | `check_role()` stays; data from Validate. |
| ADR-004 private TTL | **Compatible if US-AUTHZ-08** | Only the cookie/key input changes. |
| Quotas by role | **Compatible** | Still `Role`-driven after principal mapping. |
| Config via `get_settings()` | **Compatible** | US-AUTHZ-03. |
| HttpOnly cookie (XSS) | **Compatible** | `auth_session` is already HttpOnly; FE never reads it. |
| MinIO / storage adapters | **Independent** | No conflict with E-S3MINIO. |

### Conflicts / required doc updates (DoD before “Implemented”)

| Area | Current | Conflict | Direction |
|---|---|---|---|
| ADR-003 | JWT `access_token` issued by WebStorage | Product session moves to Auth-Service Redis cookie | ADR-008 supersedes issuance; keep HttpOnly + `withCredentials`. |
| Master Document §3/§8 | Email/password + Google in HomeCloud; JWT TTL `SESSION_TTL_SECONDS` | Hub Google-only; session TTL is Auth-Service `SESSION_TTL` (default 168h) | Update Master Document; private TTL stays local. |
| API Contract §3 | register/login/google/ticket | Retired | Replace with `/me` + logout-forward + unauth redirect. |
| API Contract header | Auth cookie `access_token` | `auth_session` | Update. |
| API Contract §9 | `PATCH .../role`, local block | Role writes in Auth-Service | 410 + hub admin. |
| Flow Spec §2 | In-app OAuth ticket bridge | `return_to` whitelist on hub | New flow: storage → hub OAuth → cookie on `.filenkov.store` → storage. |
| E-AUTH | Implemented local auth | Product auth leaves this repo | Mark “Superseded for session/roles by E-AUTHZ”; password reset backlog becomes hub-only / N/A. |
| E-ADMIN US-ADM-02 | Change role in HomeCloud | Auth-Service PATCH `storage_roles` | Supersede story. |
| TZ / Product Brief login | Email+password | Google via hub only | ADR-008 records TZ deviation. |
| `users.role` column | Source of truth | Stale if still written | Stop writes; optional drop in a later migration. |
| `init_db.py` ADMIN | Local password admin | Hub `ADMIN_EMAIL` + `storage_roles` | Ops doc. |
| Auth-Service default whitelist | no `id` | Cannot upsert by UUID | US-AUTHZ-02 **required**. |
| Auth-Service no CORS | SPA cannot call hub `/api/logout` | Logout must be same-site redirect or BFF forward | US-AUTHZ-06. |
| Auth-Service gRPC | plaintext `:9090`, no auth | LAN trust | Do not publish 9090; compose internal network only. |
| Dev whitelist | `localhost` gets **hub** fields (`roles`), not `storage_roles` | Local Validate as `localhost` may miss FAMILY or `id` | ADR-008 must pick `AUTH_CALLER_HOST` + `WHITELIST_JSON` for dev. |
| Identity of password-only users | Local bcrypt users | No Google account | US-AUTHZ-11 unmatched report; no silent login. |
| Dual admin concepts | HomeCloud ADMIN vs hub `roles=ADMIN` | A hub admin may be storage STRANGER | Document; storage APIs use `storage_roles` only. |

### Layering checklist (epic DoD gate)

- [ ] Domain: no `grpcio` / generated pb2 imports.
- [ ] Application services do not dial gRPC; they receive `User` from presentation/deps.
- [ ] Production gRPC: `Validate` only from `get_current_user`; `ListUsers` only from `GET /api/admin/users` (tests inject fakes).
- [ ] `check_role` does not query Postgres for role.
- [ ] `.env.example` lists every new setting.
- [ ] API Contract error codes include `AUTH_UNAVAILABLE`.
- [ ] No Validate cache (asserted by test: two requests → two RPCs).

---

## Definition of Done (epic)

- [ ] ADR-008 accepted; ADR-003/004 and Master/API/Flow/Test/E-AUTH/E-ADMIN updated for consistency.
- [ ] Auth-Service projects `id` + `google_email` + `name` + `storage_roles` for the storage host; README matches code.
- [ ] Every protected HomeCloud route goes through Validate (no cookie JWT decode).
- [ ] Role matrix (Test Spec + US-AUTHZ-12) green in CI with fake validator.
- [ ] Local register/login/Google/JWT issuance gone; logout invalidates Auth-Service session.
- [ ] Admin user table shows live `storage_roles` as immutable text (ListUsers); `PATCH .../role` gone.
- [ ] Session expiry: idle Redis TTL / logout → 401 → hub OAuth; 503 does not redirect to login.
- [ ] Private vault still unlocks with ADR-004 semantics.
- [ ] Compose/README: gRPC internal, cookie domain, login `return_to`, dev whitelist caveat.
- [ ] Migration path for existing file owners documented; unmatched users listed.
- [ ] Logging: auth success/deny with `user_id` / `storage_roles` / gRPC code; no raw session ids, no tokens.
- [ ] Epics index status updated when complete.

## Suggested implementation order

1. US-AUTHZ-01 + 02 + 02b + 03 (+ 10 draft)  
2. US-AUTHZ-04  
3. US-AUTHZ-05 + 08  
4. US-AUTHZ-06 + 07 + 09  
5. US-AUTHZ-11 + 12 + 10 smoke  
