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
| `KeysRegistryService` | Bootstrap `Keys/keys.yaml`; PUT/GET round-trip; reject empty/duplicate; invalid YAML not overwritten; one FileRecord on overwrite |
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
| Keys Registry | GET without unlock → 401 `PRIVATE_SESSION_EXPIRED`; GET creates `Keys/` + empty `keys.yaml`; PUT/GET round-trip; empty/duplicate → 400; invalid YAML → 409 and file unchanged; second GET does not wipe; overwrite does not add a second FileRecord |
| Quota | `/api/quota/me` shape |
| Admin | non-admin 403; **after E-AUTHZ:** `GET /api/admin/users` includes live `role` text; `PATCH .../role` gone; quota/storage still ADMIN-only |

Assert **error_code** strings, not only HTTP status.

### 3.4 Jobs

- Backup creates `.sql.zst` under `_meta/backups`.
- Cleanup removes stale PENDING.
- Reconcile corrects artificial drift.

### 3.5 Gateway headers + User-Service (epic E-GWUS)

Related: [gateway-user-service](./epics/gateway-user-service/init.md). Fake `UserDirectory` in CI.

| Case | Expectation |
|---|---|
| `X-User-Id` + `X-Storage-Role=FAMILY` | 200 on `/api/files`; zero User-Service calls |
| `X-User-Id` only | GET `…/roles/storage`; cache hit on the next client call |
| Only `X-Auth-Role=ADMIN`, User-Service down | 503 `USER_SERVICE_UNAVAILABLE`; not ADMIN |
| Invalid `X-Storage-Role` | 500; not STRANGER |
| No user-id headers | 401; no User-Service call |
| `X-Storage-Role=STRANGER` on `/api/shared` | 403 |
| `X-Storage-Role=ADMIN` on `/api/admin/*` | 200 |
| `X-Storage-Role=BLOCKED` | 403 `ACCESS_DENIED` |
| Vault cookie missing | 401 `UNAUTHORIZED` (not `PRIVATE_SESSION_EXPIRED`) |
| Vault Redis key missing | 401 `PRIVATE_SESSION_EXPIRED` |

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
3a. Keys Registry unlock → add key → save → reload list; delete in UI → save.
4. STRANGER cannot open `/shared` or `/admin`.
5. FAMILY opens `/shared`.
6. Admin changes **Auth-Service `storage_roles`** (not HomeCloud `PATCH .../role`); storage UI reflects on next load.

---

## 5. Non-Functional Checks

| Concern | Check |
|---|---|
| Streaming | Download &gt; small buffer without full memory load (integration) |
| Logging | No passphrase/password in captured logs during private unlock test; no key **values** in Keys Registry logs |
| Config | Settings load from env; forbidden direct `os.getenv` in app code (lint/grep in CI) |
| Migrations | Fresh DB + `alembic upgrade head` succeeds |

---

## 6. Fixtures & Test Data

- Deterministic ADMIN via settings or factory.
- Moto / MinIO-backed S3 adapters per test (no local blob FS).
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

As of docs creation: automated backend suite lives under `backend/tests/` (`uv run pytest`). Ad-hoc verify scripts under `backend/scripts/` are not a replacement. New SDD work should add tests alongside features.
