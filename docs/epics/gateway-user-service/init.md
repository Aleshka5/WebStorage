# Epic: Gateway headers + User-Service + Common infra

> **ID:** E-GWUS  
> **Status:** Implemented  
> **Repos:** WebStorage (this epic). **Assumed done:**  
> - Auth-Service [storage host CR](../../../../Auth-Service/docs/user-story-storage-host-headers-session.md)  
> - User-Service [list users + unique email](../../../../User-Service/docs/user-story-list-users-and-unique-email.md)  
> **Supersedes:** [E-AUTHZ](../auth-service-roles/init.md) (gRPC Validate / ListUsers). **Delete gRPC and everything related.**  
> **No backward compatibility.** Existing WebStorage DB and blob data may be wiped.

## Overview

WebStorage is **business logic only**. TLS and login live in front (host Caddy → Auth Gateway). This service:

1. Trusts identity headers from the gateway.
2. Resolves **storage** role from `X-Storage-Role` if present, else User-Service (cached). Hub `global_role` / `X-Auth-Role` is **ignored**.
3. Keeps a local `users` projection (UUID + email) for FKs, quotas, object prefixes. **Drops** local role column.
4. Keys the private vault on cookie `auth_session` (unchanged idea). **No logout** in this service.
5. Uses **Common** Postgres, Redis, and MinIO (`common_network`). One bucket `storage`. No local `db` / `redis` / `minio` / `minio-init`. No k8s manifests. No Caddy in this repo.

```text
Browser
  → host Caddy (TLS)
    → Auth Gateway :8083  (JWT, inject headers, forward auth_session)
      → WebStorage app:8000
           ├─ identity: X-User-Id + X-Storage-Role (else GET user_service …/roles/storage)
           ├─ vault Redis: private_key:{auth_session}
           └─ data: Common postgres / redis / minio
```

---

## Locked decisions (from product)

| # | Decision |
|---|---|
| D1 | One gateway upstream (`app:8000`). No Caddy here. |
| D2 | No in-app login redirect / OAuth URL. Unauthenticated HTML is the gateway’s job. |
| D3 | `/health` stays public. |
| D4 | Prefer headers; else User-Service. If the role cannot be obtained → **error**, never invent STRANGER from hub role. |
| D5 | Headers to trust: `X-User-Id`, `X-Auth-User-Id`, `X-Auth-Email`, `X-Auth-Name`, `X-Auth-Role` (ignore for gates), `X-Storage-Role`. |
| D6 | Service key is `storage`. Hub ADMIN ≠ storage ADMIN. |
| D7 | Cache User-Service reads. |
| D8 | Join `user_network`; `USER_SERVICE_URL=http://user_service:8000`. Network trust only. |
| D9 | Admin list = User-Service `GET /users` (assumed shipped). |
| D10 | User-Service outage is **user-directory** failure (`USER_SERVICE_UNAVAILABLE`), not “auth service down”. |
| D11 | Private vault stays passphrase → Redis `private_key:{auth_session}`. |
| D12 | Logout is **gone**. Do not clear cookies. Do not call hub logout. |
| D13 | Still read cookie `auth_session` (vault only). |
| D14 | Keep local user projection. Drop local role. Trust User-Service / headers for role. |
| D15 | Email uniqueness is User-Service’s problem (assumed unique). |
| D16 | Compose: `common_network` external; drop local db/minio/redis. Common is **already up**. |
| D17 | Postgres: database `db_storage`, role `storage`. MinIO: bucket `storage`, endpoint `http://minio:9000`. No disk buckets. Do not touch `mlflow`. |
| D18 | Move Redis to Common. Wipe old data. Remove k8s manifests. |
| D19 | Expand upload timeouts only where this app still owns a timeout (gateway CR owns proxy 30m). |

---

## User Stories

### US-GWUS-01 — Identity from gateway headers (User-Service fallback + cache)

**As** a signed-in family member, **I use storage** after the gateway has already authenticated me.

**Acceptance**

- `get_current_user` does **not** call gRPC. It does **not** require a valid product JWT issued by this app.
- User id: `X-User-Id` if present and a UUID, else `X-Auth-User-Id`. Missing / invalid → **401** `UNAUTHORIZED`.
- Storage role: `X-Storage-Role` if present and a valid enum (`ADMIN` \| `FAMILY` \| `STRANGER` \| `BLOCKED`).  
  Else `GET {USER_SERVICE_URL}/users/{id}/roles/storage`. Cache that result (in-memory or Common Redis; TTL e.g. 60s; key includes user id).  
  **Do not** use `X-Auth-Role`.
- Cannot get a role (User-Service 5xx, timeout, network, 404, invalid role string) → **503** `USER_SERVICE_UNAVAILABLE` or **500** if the header value is malformed. **Fail closed.** Never default to STRANGER on lookup failure.
- `200` from User-Service with `"role": "STRANGER"` (no row) **is** a successful role — use it.
- `BLOCKED` storage role → **403** `ACCESS_DENIED` (do not serve files).
- Upsert local `users` on id + email (`X-Auth-Email`). Do not write a role column.
- `GET /api/auth/me` returns `{ user_id, email, role }` where `role` is the **storage** role for this request.
- Email / name from headers when present; if email missing and needed for upsert, `GET /users/{id}` on User-Service (cached).

**Tests**

| Case | Expect |
|---|---|
| `X-User-Id` + `X-Storage-Role=FAMILY` | 200 on `/api/files`; **zero** User-Service calls |
| `X-User-Id` only | one GET `…/roles/storage`; then cache hit on next request |
| Only `X-Auth-Role=ADMIN`, no storage header, User-Service down | 503; not ADMIN |
| Invalid `X-Storage-Role` | 500 / 401; not STRANGER |
| No user-id headers | 401; no User-Service call |
| `X-Storage-Role=STRANGER` on `/api/shared` | 403 |
| `X-Storage-Role=ADMIN` on `/api/admin/*` | 200 |

**DoD:** All `Depends(get_current_user)` routes use this. Domain has no grpc imports.

---

### US-GWUS-02 — Delete gRPC, logout, Caddy, k8s, local login leftovers

**As** an operator, **I run a business-logic container**, not an auth edge.

**Acceptance**

- Delete `backend/app/infrastructure/auth_grpc/`, proto stubs, `generate_auth_grpc_stubs.py`, `AuthValidator` port, `AuthGrpcSettings`, `grpcio` / `protobuf` / `grpcio-tools` from `pyproject.toml`.
- Delete tests that exist only for gRPC.
- Remove `POST /api/auth/logout` (410/404) and FE logout. User cannot log out here.
- Remove SPA redirect to `AUTH_LOGIN_URL` / OAuth. On 401 show an error (or empty); do not send the browser to Google.
- Remove `AUTH_LOGIN_URL`, `AUTH_LOGOUT_URL`, `AUTH_GRPC_*` settings.
- Remove this repo’s `Caddyfile` and any Caddy compose service / volumes.
- Delete `deployment.yaml`, `combined-deployment.yaml`, and k8s-only docs snippets.
- Local register/login/Google stay gone (already 410). Drop unused JWT issuer / `oauth_client` if nothing else uses them.
- Compose: drop external `auth` gRPC network **unless** needed to reach the gateway as `app` on `auth_network` (keep **`auth_network` external** for that DNS, not for gRPC).

**DoD:** `rg grpc` / `AUTH_GRPC` in backend is empty. No k8s manifests. No Caddyfile.

---

### US-GWUS-03 — Admin list via User-Service `GET /users`

**As** a storage admin, **I see everyone** with live **storage** roles as read-only text.

**Acceptance**

- `GET /api/admin/users`: after the caller is storage `ADMIN`, call `GET http://user_service:8000/users`. Join local quota / usage by UUID.
- Role on each row = that user’s persisted `services[]` entry for `storage`, or `STRANGER` if omitted (that is a successful list, not a failed lookup).
- User-Service down → **503** `USER_SERVICE_UNAVAILABLE`. Do not fall back to a local role column.
- `PATCH …/role` stays gone (410). Quota / storage admin ops stay here.

**Tests:** empty list; join quota; US 503; FAMILY cannot call admin.

---

### US-GWUS-04 — Private vault still uses `auth_session`

**As** a private-storage user, **I unlock with my key** and stay unlocked for `PRIVATE_SESSION_TTL_HOURS`.

**Acceptance**

- Session id = cookie `AUTH_COOKIE_NAME` (default `auth_session`). Redis `private_key:{session_id}` on **Common** Redis.
- Unlock / lock / expiry behavior unchanged (401 `PRIVATE_SESSION_EXPIRED` vs 401 `UNAUTHORIZED` if there is no identity).
- Logout must **not** delete the vault key (logout does not exist). Hub logout (elsewhere) will change/clear the cookie; old key becomes unreachable — acceptable.
- Identity does **not** come from this cookie.

**Tests:** unlock then GET file; cookie missing → cannot use vault; TTL expiry → `PRIVATE_SESSION_EXPIRED`.

---

### US-GWUS-05 — Common Postgres, Redis, MinIO (no local data plane)

**As** an operator, **I attach to Common** that is already running.

**Acceptance**

- Compose **removes** services `db`, `redis`, `minio`, `minio-init` and their volumes.
- `app` (and anything that needs data) joins:
  - `common_network` (`external: true`, `name: common_network`)
  - `user_network` (`external: true`)
  - `auth_network` (`external: true`, same name Auth-Service uses) so the gateway can proxy to `app:8000`
- **Do not** start Common from this compose. No `depends_on` on Common containers (they are another project).
- Env:
  - `DATABASE_URL=postgresql+asyncpg://storage:<password>@postgres:5432/db_storage`
  - `REDIS_URL=redis://redis:6379`
  - `S3_ENDPOINT_URL=http://minio:9000`
  - `S3_ACCESS_KEY` / `S3_SECRET_KEY` = Common MinIO root (`minioadmin` from Common `.env.common`) unless an operator sets a dedicated key
  - Single bucket **`storage`**. No `STORAGE_DISKS` multi-bucket. No prefix+disk names. Do not create/touch `mlflow`.
- App config: one logical volume → bucket `storage`. Object keys stay `users/{id}/…`, `shared/…`, `_meta/backups/…` **inside that bucket**.
- Wipe: do not migrate old `pg_data` or `/media/aleksey/HardDisk/storage`.
- Alembic still owns schema **inside** `db_storage` only (never User-Service’s `users` DB).
- README documents **one-time** init on Common (see below). No host publish of MinIO from this repo.

**README init (Common already up):**

```bash
# Postgres (exec on Common postgres container)
psql -U postgres -c "CREATE USER storage WITH PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE db_storage OWNER storage;"

# MinIO bucket (do not touch mlflow)
mc alias set local http://minio:9000 minioadmin minioadmin
mc mb --ignore-existing local/storage
```

Put the password in WebStorage `.env` as part of `DATABASE_URL`. Do not commit secrets.

**DoD:** `docker-compose.yml` has no db/minio/redis services. README has the init block. `.env.example` matches.

---

### US-GWUS-06 — Drop local `users.role`

**As** a developer, **I do not store a role** that can drift from User-Service.

**Acceptance**

- Alembic migration drops `users.role` (and unused legacy auth columns if they are dead: `password_hash`, `google_id` — only if nothing reads them).
- Authorization uses the request principal only.
- Local table keeps `id` (UUID PK) + `email` + whatever quotas/FKs need.

**DoD:** migration + tests; no `user.role` from DB in `check_role`.

---

### US-GWUS-07 — Frontend + `/me` without logout/login

**As** a user already past the gateway, **the SPA just loads**.

**Acceptance**

- `GET /api/auth/me` with credentials still bootstraps `{ user_id, email, role }`.
- Remove logout button / logout API usage.
- Remove “go to Google” on 401. Optional copy: “Open this site through the hub” — no OAuth URL required.
- 503 `USER_SERVICE_UNAVAILABLE` → visible error, not a login loop.
- Sidebar matrix unchanged (Shared = FAMILY/ADMIN, Admin = ADMIN) using **storage** role.
- Production: one HTTP process (`app`) must serve API **and** the built SPA so the gateway’s single upstream works. (Vite `frontend` service may remain for local UI work; it is not the public edge.)

---

## Parallelization

```text
Wave 1                         Wave 2                    Wave 3
US-GWUS-02 delete gRPC/Caddy   US-GWUS-01 headers        US-GWUS-03 admin list
US-GWUS-05 Common compose      US-GWUS-04 vault cookie   US-GWUS-07 FE
US-GWUS-06 drop role column    (needs 01 + 05 Redis)
```

---

## Definition of Done (epic)

- [x] No gRPC client, proto, or Auth-Service Validate/ListUsers usage.
- [x] Identity = gateway headers; storage role header or cached User-Service GET; hub role ignored.
- [x] Role lookup failure fails closed.
- [x] Local users projection without role column.
- [x] Admin list uses User-Service `GET /users`.
- [x] Vault still keyed by `auth_session`; no logout in this app.
- [x] Compose uses Common + `user_network` + `auth_network`; no local db/redis/minio; no k8s; no Caddy.
- [x] Bucket `storage` only; README has Postgres role/db + `mc mb` steps.
- [x] Docs: this epic, epics index, ADR-008 superseded (or ADR-009), API Contract `/me` + errors, README.
- [x] Tests for header matrix, cache, US outage, admin list, vault cookie.

## Suggested order

1. US-GWUS-02 + 05 + 06 (delete and rewire infra)  
2. US-GWUS-01 + 04  
3. US-GWUS-03 + 07 + docs  
