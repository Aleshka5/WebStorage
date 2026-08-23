# Test Spec — HomeCloud

> **Status:** Active  
> **Note:** TZ v1.1 allows shipping without a full suite; this spec defines the **target** strategy for Spec Driven Development so new work is testable.

Related: [API Contract.md](./API%20Contract.md), [Data Models.md](./Data%20Models.md), [Flow Spec.md](./Flow%20Spec.md).

---

## 1. Goals

1. Protect role/quota/encryption invariants.
2. Keep API contracts stable (error codes + status).
3. Enable regression tests for FileManager sections without duplicating logic.
4. Prefer fast unit/domain tests; use API integration for boundaries; E2E for critical journeys.

---

## 2. Test Pyramid

| Layer | Scope | Tools (recommended) |
|---|---|---|
| Unit | Domain VOs, quota math, path guards, KDF/marker helpers | `pytest` |
| Application | Services with fake repos/adapters | `pytest` + doubles |
| API integration | Routers + DB + Redis (testcontainers or compose profile) | `pytest` + `httpx.AsyncClient` |
| Frontend unit | Validators, stores, hooks | Vitest |
| Frontend component | FileManager, PhotoGrid, unlock modal | Vitest + Testing Library |
| E2E | Auth → files/photos/private/admin smoke | Playwright |

Package manager for backend tests: **uv**.

---

## 3. Backend Strategy

### 3.1 Domain / unit

| Case | Expectation |
|---|---|
| `StorageQuota.available_bytes` | Non-negative; exceeded detection |
| `Role.can_access_*` | Matrix matches Master Document |
| Path normalization | `../` → `PathTraversalError` |
| Marker validation | Wrong key fails; correct key passes |
| Filename encryption round-trip | Decrypt equals plaintext name |

### 3.2 Application services

Use in-memory / temp FS adapters and fake `SessionStore`.

| Service | Must cover |
|---|---|
| `AuthService` | Register duplicate email; login bad password; JWT subject |
| `FileService` | Upload increments quota; delete decrements; shared ACL |
| `PhotoService` | Reject non-image; create preview path |
| `PrivateService` | Unlock stores key; lock removes; reset wipes |
| `AdminService` | Self role/delete forbidden; block sets inactive |
| `ArchiveService` | Only idle COMMITTED files archived |
| `MaintenanceService` | PENDING &gt;1h removed |

### 3.3 API integration (priority)

| Area | Cases |
|---|---|
| Auth | **Until E-AUTHZ:** register/login/me/logout; rate limit 429. **After E-AUTHZ:** no register/login/JWT; `GET /api/auth/me` + logout-forward; missing cookie 401; Validate failure mapping (see §3.5). |
| Files | CRUD happy path; 413 over quota; traversal 400 |
| Shared | STRANGER 403; FAMILY list/upload; delete ACL |
| Photos | upload + list pagination `has_next` |
| Private | ops without unlock → 401 `PRIVATE_SESSION_EXPIRED`; unlock then download |
| Quota | `/api/quota/me` shape |
| Admin | non-admin 403; **after E-AUTHZ:** `GET /api/admin/users` includes live `role` text; `PATCH .../role` gone; quota/storage still ADMIN-only |

Assert **error_code** strings, not only HTTP status.

### 3.4 Jobs

- Backup creates `.sql.zst` under `_meta/backups`.
- Cleanup removes stale PENDING.
- Reconcile corrects artificial drift.

### 3.5 Auth-Service gRPC (epic E-AUTHZ)

Related: [auth-service-roles](./epics/auth-service-roles/init.md). Use a **fake `AuthValidator`** in CI; optional live gRPC marked `integration`.

| Case | Expectation |
|---|---|
| Two authenticated requests, same cookie | **Two** `Validate` RPCs (no caller cache) |
| Missing `auth_session` | 401 `UNAUTHORIZED`; zero RPCs |
| Expired / unknown Redis session (`Unauthenticated`) | 401 `UNAUTHORIZED`; FE → `AUTH_LOGIN_URL`, not private-unlock UI |
| gRPC `PermissionDenied` / blocked | 403 `ACCESS_DENIED` (not login redirect) |
| gRPC `Unavailable` / deadline | 503 `AUTH_UNAVAILABLE` (FE must **not** OAuth-redirect) |
| `storage_roles=STRANGER` | `/api/shared` and `/api/admin` → 403; `/api/files` → 200 |
| `storage_roles=FAMILY` | shared 200; admin 403 |
| `storage_roles=ADMIN` | shared + admin 200 |
| Invalid / missing `storage_roles` in fields | 500 `INTERNAL_ERROR` (misconfig), not silent STRANGER |
| Domain/Application | No `grpcio` imports |

---

## 4. Frontend Strategy

### 4.1 Unit

- `validation.ts`: email, password match, file name rules.
- `format.ts`: bytes/dates.
- Auth store: maps `EMAIL_ALREADY_EXISTS` / `INVALID_CREDENTIALS` to fields.
- Axios interceptor: dispatches private-session-expired event only for that code.

### 4.2 Component

| Component | Cases |
|---|---|
| `ProtectedRoute` | Redirect unauthenticated |
| `Sidebar` | Shared/Admin visibility by role |
| `AdminPage` (E-AUTHZ) | Role column is text, not `<select>`; no `updateUserRole` |
| `FileManager` | Empty, list, mkdir dialog validation |
| `PrivateUnlockModal` | Submit passphrase; show lockout/reset affordance |
| `PhotoGrid` / `Lightbox` | Render items; open original |

Mock API modules; do not hit real backend in unit/component tests.

### 4.3 E2E (smoke)

1. Admin login → `/files` upload → download → delete.
2. Photo upload → appears in grid → lightbox.
3. Private unlock → upload → lock/expiry → unlock again.
4. STRANGER cannot open `/shared` or `/admin`.
5. FAMILY opens `/shared`.
6. Admin changes **Auth-Service `storage_roles`** (not HomeCloud `PATCH .../role`); storage UI reflects on next load.

---

## 5. Non-Functional Checks

| Concern | Check |
|---|---|
| Streaming | Download &gt; small buffer without full memory load (integration) |
| Logging | No passphrase/password in captured logs during private unlock test |
| Config | Settings load from env; forbidden direct `os.getenv` in app code (lint/grep in CI) |
| Migrations | Fresh DB + `alembic upgrade head` succeeds |

---

## 6. Fixtures & Test Data

- Deterministic ADMIN via settings or factory.
- Temp `STORAGE_ROOT` per test session.
- Redis DB index isolated or flushed between tests.
- Sample images: tiny JPEG/PNG fixtures; one invalid `.txt` for format rejection.

---

## 7. Definition of Done (testing)

For a new epic/story:

1. Happy path covered at API or E2E level.
2. At least one negative path (authz, validation, or quota).
3. Contract fields referenced in [API Contract.md](./API%20Contract.md) asserted when touched.
4. No secrets in fixtures committed to git.

---

## 8. Current Baseline

As of docs creation: **no formal automated suite is mandated by TZ**; verify scripts under `backend/scripts/` (e.g. private storage / encrypted adapter checks) are ad-hoc aids, not a replacement for this strategy. New SDD work should add tests alongside features rather than expanding verify scripts only.
