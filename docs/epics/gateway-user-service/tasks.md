# E-GWUS implementation tasks

Source of truth: [init.md](./init.md). Check items off as they land. No backward compatibility.

## Wave 1 — delete + infra + schema

### T1 — Delete gRPC and old auth edge leftovers (US-GWUS-02)

- [x] Delete `backend/app/infrastructure/auth_grpc/` (client, mapping, proto stubs)
- [x] Delete `backend/scripts/generate_auth_grpc_stubs.py`
- [x] Delete `backend/app/application/ports/auth_validator.py`; update `ports/__init__.py`
- [x] Remove `AuthGrpcSettings` and `AUTH_GRPC_*` / `AUTH_LOGIN_URL` / `AUTH_LOGOUT_URL` / `AUTH_CALLER_HOST` from `backend/config.py` and `.env.example`
- [x] Remove `grpcio`, `protobuf` from `pyproject.toml` dependencies; `grpcio-tools` from dev; refresh `uv.lock`
- [x] Delete gRPC-only tests: `test_auth_grpc_client.py`, `test_auth_grpc_settings.py`, `test_auth_grpc_current_user.py`
- [x] Rewrite remaining tests that fake `AuthValidator` (`test_auth_role_matrix.py`, `test_admin_roles_readonly.py`) to header / User-Service fakes
- [x] Delete unused local JWT issuer / OAuth: `backend/app/application/auth_service.py`, `backend/app/infrastructure/oauth_client.py`, `get_google_oauth_client` / `get_auth_service` if unused
- [x] Drop unused JWT/password deps if nothing else imports them (`python-jose`, `passlib`, `bcrypt`)
- [x] Delete one-shot Auth migration leftovers: `backend/scripts/migrate_local_users_to_auth.py`, `backend/app/infrastructure/user_uuid_migration.py`, `backend/tests/test_migrate_local_users_to_auth.py`
- [x] Retire `backend/scripts/init_db.py` (local ADMIN password bootstrap)
- [x] `POST /api/auth/logout` → 410; do not clear cookies; do not delete vault Redis key; do not BFF-forward hub logout
- [x] Delete `Caddyfile`, Caddy compose volumes (`caddy_data`, `caddy_config`)
- [x] Delete k8s manifests: `deployment.yaml`, `combined-deployment.yaml`
- [x] Compose: drop gRPC `auth` network purpose; keep `auth_network` external (`AUTH_DOCKER_NETWORK`, default `deploy_auth`) so gateway DNS can reach `app:8000`
- [x] `rg grpc` / `AUTH_GRPC` in `backend/` is empty (except historical docs)

### T2 — Common compose + env + README init (US-GWUS-05)

- [x] Rewrite `docker-compose.yml`: only `app` (+ optional `frontend` for local Vite)
- [x] Networks: `default`, `common_network` external, `user_network` external, `auth_network` external (`name: ${AUTH_DOCKER_NETWORK:-deploy_auth}`)
- [x] Do not start Common; no `depends_on` Common containers; drop services `db`, `redis`, `minio`, `minio-init` and their volumes
- [x] Update `.env.example`: `DATABASE_URL=postgresql+asyncpg://storage:<password>@postgres:5432/db_storage`, `REDIS_URL=redis://redis:6379`, `S3_ENDPOINT_URL=http://minio:9000`, `S3_BUCKET=storage`, `USER_SERVICE_URL=http://user_service:8000`, `AUTH_COOKIE_NAME=auth_session`; remove `STORAGE_DISKS`, `S3_BUCKET_PREFIX`, gRPC/login/logout/JWT/Google/admin-password vars that are retired
- [x] Collapse storage config to one bucket `storage` (`StorageSettings` / `S3Settings` / `DiskRouter` / `S3StorageAdapter`); object keys stay `users/{id}/…`, `shared/…`, `_meta/backups/` inside that bucket
- [x] README: one-time Common init (`CREATE USER storage`, `CREATE DATABASE db_storage`, `mc mb local/storage`, do not touch `mlflow`); operator order: Common already up → User-Service → Auth → WebStorage
- [x] Fix S3/disk-router tests broken by single-bucket change (`test_s3_settings.py`, `test_disk_router_s3.py`, `test_us_s3_*`)

### T3 — Drop local `users.role` (US-GWUS-06)

- [x] Alembic migration `005_drop_users_role_and_legacy_auth.py`: drop `users.role`, `password_hash`, `google_id`; drop unused `user_role` enum
- [x] Update `backend/app/infrastructure/database/models.py` (no role / password_hash / google_id)
- [x] Update domain `User` (request-time `role` only; no password_hash / google_id)
- [x] Update `UserRepository`: upsert id+email only; no `update_role` / `link_google_id` / `get_by_google_id`; authorization never reads role from DB
- [x] Domain `Role` includes `BLOCKED`

## Wave 2 — identity + vault

### T4 — `get_current_user` from headers + User-Service cache (US-GWUS-01)

- [x] Settings: `USER_SERVICE_URL`, timeout, role-cache TTL (~60s), `AUTH_COOKIE_NAME` for vault only
- [x] Application port `UserDirectory` (no gRPC types): get storage role, get user (email), list users
- [x] Infrastructure HTTP client + in-memory/Redis TTL cache; no JWT to User-Service
- [x] `get_current_user`: id = `X-User-Id` else `X-Auth-User-Id` (UUID); role = valid `X-Storage-Role` else cached `GET /users/{id}/roles/storage`; ignore `X-Auth-Role`; upsert local id+email from `X-Auth-Email` or cached `GET /users/{id}`
- [x] Fail closed: US 5xx/timeout/network/404/invalid role → 503 `USER_SERVICE_UNAVAILABLE`; malformed header role → 500; never invent STRANGER on lookup failure; US 200 `STRANGER` is success
- [x] `BLOCKED` storage role → 403 `ACCESS_DENIED`
- [x] `GET /api/auth/me` returns `{ user_id, email, role }` = storage role
- [x] Error code `USER_SERVICE_UNAVAILABLE` (not `AUTH_UNAVAILABLE`)
- [x] Tests: header matrix from epic (zero US calls when header present; cache hit; US down → 503; ignore `X-Auth-Role`; invalid header role → error not STRANGER; no id → 401; STRANGER on shared → 403; ADMIN on admin → 200)

### T5 — Private vault cookie (US-GWUS-04)

- [x] Session id from `AUTH_COOKIE_NAME` (`auth_session`); Redis `private_key:{sid}` on Common Redis
- [x] Identity does not come from this cookie
- [x] Logout must not delete the vault key (logout gone)
- [x] Tests: unlock then GET file; cookie missing → cannot use vault (`UNAUTHORIZED`); TTL expiry → `PRIVATE_SESSION_EXPIRED`

## Wave 3 — admin + FE + docs

### T6 — Admin list via `GET /users` (US-GWUS-03)

- [x] After storage ADMIN, call `GET {USER_SERVICE_URL}/users`; join local quota by UUID
- [x] Row role = `services[]` entry for `storage`, or `STRANGER` if omitted
- [x] US down → 503 `USER_SERVICE_UNAVAILABLE`
- [x] `PATCH …/role` stays 410; quota / storage admin ops stay
- [x] Tests: empty list; join quota; US 503; FAMILY cannot call admin

### T7 — Frontend (US-GWUS-07)

- [x] Remove logout button / `POST /api/auth/logout` usage
- [x] Remove OAuth/login redirect (`authLogin.ts`, `ProtectedRoute`, `router`, axios interceptor)
- [x] 401 / 503 `USER_SERVICE_UNAVAILABLE` → visible error, no login loop
- [x] Sidebar matrix unchanged using storage role from `/me`
- [x] Production: FastAPI serves built SPA (single upstream); Vite `frontend` compose service may remain for local only
- [x] Copy `USER_SERVICE_UNAVAILABLE` into FE error map

### T8 — Docs + tests wrap-up

- [x] README (Common init, networks, no gRPC, no local db/minio/redis)
- [x] API Contract: `/me`, identity headers, `USER_SERVICE_UNAVAILABLE`, logout 410
- [x] Epics index: E-GWUS in progress / implemented; E-AUTHZ superseded
- [x] ADR-008 superseded; add ADR-009 (gateway headers + User-Service + Common)
- [x] Light updates: BACKEND_DOCS / FRONTEND_DOCS / Data Models / Test Spec / Flow Spec where they still describe gRPC or login redirect
- [x] Full relevant pytest green; `rg grpc` / `AUTH_GRPC` clean in backend code

## Epic DoD

- [x] No gRPC client, proto, or Auth-Service Validate/ListUsers usage
- [x] Identity = gateway headers; storage role header or cached User-Service GET; hub role ignored
- [x] Role lookup failure fails closed
- [x] Local users projection without role column
- [x] Admin list uses User-Service `GET /users`
- [x] Vault still keyed by `auth_session`; no logout in this app
- [x] Compose uses Common + `user_network` + `auth_network`; no local db/redis/minio; no k8s; no Caddy
- [x] Bucket `storage` only; README has Postgres role/db + `mc mb` steps
- [x] Docs + tests for header matrix, cache, US outage, admin list, vault cookie
