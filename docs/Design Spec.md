# Design Spec — HomeCloud

> **Status:** Active  
> **Version:** 1.0  
> **Related:** [Master Document.md](./Master%20Document.md), [API Contract.md](./API%20Contract.md), [Data Models.md](./Data%20Models.md), [adr/](./adr/index.md)

---

## 1. System Context

```
┌────────────┐     HTTPS/HTTP       ┌─────────────────┐
│  Browser   │ ◄─────────────────►  │  Frontend SPA   │
│            │                      │  (Nginx/Vite)   │
└────────────┘                      └────────┬────────┘
                                             │ /api/*
                                    ┌────────▼────────┐
                                    │  FastAPI app    │
                                    │  Presentation   │
                                    └────────┬────────┘
                         ┌───────────────────┼───────────────────┐
                         ▼                   ▼                   ▼
                   PostgreSQL              Redis              MinIO
                   (metadata)           (sessions)         (S3 blobs)
```

External: Google OAuth 2.0 (optional). Ops: APScheduler inside app process for backup/archive/maintenance.

---

## 2. Logical Architecture (Clean Architecture)

```
backend/app/
├── domain/           # Entities, value objects, domain exceptions
├── application/      # Use-case services
├── infrastructure/   # DB, Redis, storage adapters, jobs helpers
└── presentation/     # Routers, Pydantic schemas, deps, middleware
```

**Dependency rule:** Domain has no outward deps. Application depends on Domain. Infrastructure implements ports used by Application. Presentation wires HTTP to Application.

### Domain

| Type | Responsibility |
|---|---|
| `User` | Identity, role, activity |
| `FileRecord` | File metadata + lifecycle |
| `DiskVolume` | Logical disk → MinIO bucket |
| `Role` | Access predicates (`can_access_shared`, `can_access_admin`) |
| `FileSection` / `FileStatus` | Section & lifecycle enums |
| `StorageQuota` | used/limit helpers |
| `ErrorCode` | Stable API error codes |
| `decide_registration_target` | Which Files/Photos object is offered to RAG, or skipped |
| Domain exceptions | Mapped to HTTP in presentation (`SearchQueryInvalid`, `IndexingUnavailable`, `PrivateIndexRefused`) |

### Application services

| Service | Responsibility |
|---|---|
| `AuthService` | Register, login, Google link/create, JWT |
| `FileService` | List/upload/download/mkdir/rename/delete/ZIP for a section |
| `PhotoService` | Photo upload, thumbnails, pagination, delete |
| `PrivateService` | Unlock/lock/reset; builds encrypted `FileService` |
| `KeysRegistryService` | Bootstrap/parse/save `Keys/keys.yaml` via unlocked private `FileService` |
| `ResumeService` | Country/company/vacancy tree, `meta.yaml` and `statuses.yaml` via a `RESUMES`-scoped `FileService` ([ADR-010](./adr/ADR-010-resumes-yaml-tree-on-s3.md)) |
| `AdminService` | Users, roles, private quotas, block, delete, disk stats |
| `ArchiveService` | Idle-file zstd archive + stats |
| `BackupService` | `pg_dump` + zstd + retention |
| `MaintenanceService` | PENDING cleanup, `.tmp` cleanup, quota reconcile |
| Index enqueue | Request-path writes to `file_index_links` only. No RAG I/O |
| `index_sync` | One batch of due links per tick. Does not commit |
| Index backfill / search / status | One backfill page, photo search proxy, caller and admin counts |

### Infrastructure

| Component | Notes |
|---|---|
| `S3StorageAdapter` | MinIO object I/O; SHA-256; logical-key traversal checks; hides `.tmp`/dot prefixes |
| `EncryptedStorageAdapter` | Decorator over `StorageAdapter`: AES-GCM content + encrypted names; PBKDF2 key |
| `DiskRouter` | Pick write bucket by free space (MinIO capacity); cache TTL |
| `ThumbnailService` | Pillow (+ HEIF); max edge `THUMBNAIL_MAX_PX` |
| `ArchiveManager` | zstd L22; `pre_encrypt` / `post_encrypt` |
| `SessionStore` (Redis) | Private keys, OAuth state/ticket, rate limits |
| Repositories | `UserRepository`, `FileRepository`, `QuotaRepository`, `FileIndexLinkRepository` |
| `RagIndexClient` | httpx to `RAG_BASE_URL`. Header `X-Auth-User-Id`. No cookie, no `X-Auth-Role` |
| ORM models | SQLAlchemy 2 async. `FileIndexLink` added in Alembic `008` |

### Presentation

- Routers: `auth`, `files`, `shared`, `photos`, `private`, `quota`, `resumes` (+ `resumes/files`), `generated`, `admin`, `indexing`.
- Indexing routes (`/api/photos/search`, `/api/indexing/status`, `/api/admin/indexing`, backfill, retry) are a separate router. Admin routes use `check_role(Role.ADMIN)`.
- The `/api/resumes/files` endpoints are built from the same file-handler set as `/api/files` rather than hand-copied, and are scoped to `FileSection.RESUMES`.
- Cookie auth dependency; role checks for shared/resumes/admin.
- Global exception handlers → structured `error_code`.
- Rate-limit middleware on sensitive auth routes.

---

## 3. Frontend Architecture

```
frontend/src/
├── pages/            # Auth, Photos, Files, Private, Keys Registry, Resumes, All Vacancies, Shared, Admin
├── components/
│   ├── Layout/       # AppLayout, Header, Sidebar, StorageUsageBar
│   ├── FileManager/  # Reusable manager (plain | encrypted)
│   ├── PhotoGrid/    # Grid, Lightbox, FAB, PhotoSearchBar
│   └── ui/           # Button, Input, Modal, ErrorMessage
├── services/         # Axios clients (files, photos, private, keys, resumes, admin, api)
├── store/            # Zustand: auth, quota
├── hooks/            # upload, infinite scroll, private session
└── router.tsx        # Guards + bootstrap fetchMe
```

**FileManager contract**

```ts
mode: "plain" | "encrypted"
apiPrefix: "/api/files" | "/api/private" | "/api/shared" | "/api/resumes/files"
basePath?: string        // prefix every API path; breadcrumbs root here (resumes vacancy folder)
hiddenNames?: string[]   // entries filtered from the listing (e.g. meta.yaml)
```

`basePath` and `hiddenNames` are optional; omitting both preserves the pre-Resumes behaviour exactly.

Auth: `withCredentials: true`; JWT never stored in localStorage. Private expiry: axios interceptor detects `PRIVATE_SESSION_EXPIRED` and emits event for unlock modal.

---

## 4. Module Contracts

### 4.1 Storage adapter interface (conceptual)

| Method | Behavior |
|---|---|
| `list(path)` | Nodes (name, is_dir, size, modified) |
| `write(path, stream)` | Persist bytes; return checksum |
| `read(path)` | Stream bytes |
| `mkdir(path)` | Create directory |
| `delete(path)` | Remove file/dir |
| `rename(...)` | Rename within section |

Encrypted adapter encrypts/decrypts names and payloads around the inner `StorageAdapter` (`S3StorageAdapter`).

### 4.2 DiskRouter

- Inputs: `STORAGE_DISKS`, `S3_*`, `MIN_FREE_SPACE_MB`, `DISK_SPACE_CACHE_TTL`.
- Mapping: `disk_id` → bucket `{S3_BUCKET_PREFIX}{disk_id}`.
- Capacity: HeadBucket + object size sum + MinIO Admin API (1 TiB total fallback).
- Output: `disk_id` for new writes, or `StorageUnavailableError`.
- Strategy env `DISK_STRATEGY` reserved; **implemented behavior = most free space**.

### 4.3 Quota

- Denormalized `user_quota_usage` updated on upload/delete.
- All roles: total limit = stored `user_quota_usage.limit_bytes` (default 100 MB; admin-mutable; `0` = unlimited).
- Private bytes checked against `private_limit_bytes` on private uploads; private uploads also enforce the total cap.
- Reconcile job corrects drift > 1 MB.

### 4.4 Encryption

| Step | Detail |
|---|---|
| KDF | PBKDF2-HMAC-SHA256, 100_000 iterations, salt = `user_id` bytes |
| Cipher | AES-256-GCM |
| Chunk framing | `IV(12) + uint32 BE ciphertext_len + ciphertext` |
| Names | Encrypt → urlsafe Base64 |
| Marker | `.marker` plaintext expected `HOMECLOUD_MARKER_V1` after decrypt |
| Session | Redis key under `private_key:` keyed by JWT cookie value |

---

## 5. Background Jobs (APScheduler)

| Job | Schedule | Service |
|---|---|---|
| `db_backup` | Daily 02:00 | `BackupService.run_db_backup` |
| `daily_archive` | Daily 03:00 | `ArchiveService.run_daily_archive` |
| `reconcile_quotas` | Daily 04:00 | `MaintenanceService.reconcile_quotas` |
| `cleanup_pending` | Every 1h | Stale `PENDING` (>1h) |
| `cleanup_tmp` | Every 1h | Orphan `.tmp` files |
| `index_sync` | Every `RAG_SYNC_INTERVAL_SECONDS` (default 60) | `run_index_sync`. Registered only when `RAG_ENABLED` is true. `max_instances=1`, `coalesce=True`, plus an in-process lock. Does not commit; `main.py` commits after the tick |

Startup calls `backfill(links)` once when `RAG_ENABLED` is true. That function writes nothing when `RAG_BACKFILL_ENABLED` is false. It does not loop.

Request-path hooks (upload, rename, move, overwrite, delete, archive) only insert or update `file_index_links` on the current session. A failed insert propagates and the request rolls back. The job is what talks to RAG.

Collections: PDF/DOCX/XLSX → `documents`. JPEG/PNG/WebP/GIF within `RAG_MAX_IMAGE_BYTES` → `images`. Photos HEIC/HEIF and oversized Photos use the JPEG preview. Files HEIC and oversized Files images are `SKIPPED` / `unsupported_type`. Shared, Resumes, and Generated are never indexed. Private and encrypted records raise `PrivateIndexRefused` and get no row.

Rename or move keeps `rag_file_id` and re-keys (`PUT /api/files/{id}/object`). A content change deletes the vectors, registers again, and resets `attempts` to 0. Delete and archive remove vectors only. A missing original object becomes `FAILED` / `object_not_found` and is not retried. A missing photo preview retries, then becomes `SKIPPED` / `preview_missing`. A non-terminal HTTP error on delete or archive keeps `REMOVED` or `ARCHIVED`, increments `attempts`, and sets `next_attempt_at`. `404` and `409 file_busy` count as vectors gone.

Admin retry moves a failed row that already has `rag_file_id` to `SUBMITTED` (poll). A row that never registered stays `PENDING` with a null id.

Backfill is one pass. It also inserts archived Files and Photos that have no link as `SKIPPED` / `archived` and does not send them to RAG. `POST /api/admin/indexing/backfill` does not call `backfill` when `RAG_ENABLED` is false.

---

## 6. Integrations

| Integration | Purpose | Notes |
|---|---|---|
| Google OAuth 2.0 | Social login | State in Redis; ticket bridge to set cookie |
| PostgreSQL | Metadata | Alembic migrations on startup |
| Redis | Sessions / rate limits | Required for private unlock & OAuth |
| MinIO | Blob storage | One bucket `storage`. App does not mount a blob disk. See [ADR-011](./adr/ADR-011-shared-minio-bucket-with-rag.md) |
| RAG edge | Vector index for Files and Photos | `http://rag-edge:8080` on external network `rag_edge`. Reads objects by key. Does not own them |
| Caddy / Ingress (optional) | TLS | Outside core compose |

---

## 7. Configuration Model

Root: `Settings` via `get_settings()` (`@lru_cache`).

| Group | Key settings |
|---|---|
| `DatabaseSettings` | `DATABASE_URL` |
| `CacheDBSettings` | `REDIS_URL` |
| `StorageSettings` | `STORAGE_DISKS`, `DISK_STRATEGY`, `DISK_SPACE_CACHE_TTL`, `MIN_FREE_SPACE_MB` |
| `S3Settings` | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_USE_SSL`, `S3_BUCKET_PREFIX`, `S3_PATH_STYLE` |
| `AuthSettings` | Google OAuth, `FRONTEND_URL`, `JWT_SECRET`, session TTLs |
| `BusinessLogicSettings` | Photo batch, thumbnail size, stranger quota, archive days |
| `AdminSettings` | `ADMIN_EMAIL`, `ADMIN_PASSWORD` (bootstrap) |
| `LoggingSettings` | Level, optional file sink |
| `RagSettings` | `settings.rag`. See the table below |

Compose-only: `POSTGRES_*`. Frontend-only: `VITE_API_BASE_URL` (dev). `app` also joins external network `rag_edge`.

`RagSettings` (`get_settings().rag`). One trailing slash is stripped from `RAG_BASE_URL`.

| Variable | Default |
|---|---|
| `RAG_ENABLED` | `false` |
| `RAG_BASE_URL` | `http://rag-edge:8080` |
| `RAG_TIMEOUT_SECONDS` | `10` |
| `RAG_SYNC_INTERVAL_SECONDS` | `60` |
| `RAG_INDEX_BATCH_SIZE` | `20` |
| `RAG_MAX_ATTEMPTS` | `8` |
| `RAG_INDEX_MAX_WAIT_SECONDS` | `3600` |
| `RAG_MAX_IMAGE_BYTES` | `10485760` |
| `RAG_BACKFILL_ENABLED` | `false` |

`RAG_ENABLED=false` creates no link rows, does not register `index_sync`, and does not run startup backfill. Photo search then returns `503 INDEXING_UNAVAILABLE` with no outbound call.

---

## 8. Security Design

1. JWT in httpOnly cookie (`access_token`); SameSite=lax in current stack (TZ prefers Strict + Secure — see ADR).
2. Path normalization + traversal rejection (`PATH_TRAVERSAL_DETECTED`).
3. Auth rate limits; private unlock ≤5 attempts / window then `TOO_MANY_ATTEMPTS` (optional reset).
4. Shared delete: owner or ADMIN only.
5. Admin cannot change own role or delete self.
6. Never log passphrase, JWT, derived keys, or document bytes. Index logs carry `user_id`, `file_record_id`, `rag_file_id`, `state`, and `error_code`.
7. RAG calls send `X-Auth-User-Id` only. Private and encrypted bytes are refused before a row exists.

---

## 9. Observability

Structured JSON logs (loguru + JSON formatter). Preferred fields: `timestamp`, `level`, `user_id`, `action`, `file_id`, `disk_id`, `result`, `error_code`.

Health: `GET /`, `GET /health`. Admin: `GET /api/admin/storage/health`.

---

## 10. Deployment Topology

**Docker Compose:** `app` joins `common_network`, `user_network`, `auth_network`, and external `rag_edge`. It does not join `rag_internal` and does not publish `:8000` as a second public edge. Blobs stay in the shared MinIO bucket `storage` ([ADR-011](./adr/ADR-011-shared-minio-bucket-with-rag.md)). App does not mount a blob disk.

**Init:** `minio-init` (buckets), `scripts/init_db.py` (first ADMIN), Alembic upgrade on entrypoint.

**K8s:** `deployment.yaml` + secrets via `generate-secret.sh`. Blobs are MinIO; do not mount `/storage` on the app pod.

---

## 11. Extension Points (reserved)

| Point | Intent |
|---|---|
| `upload_sessions` table | Chunked/resumable uploads |
| `/api/files/search` | Still `501`. Photo description search is `GET /api/photos/search` |
| `DISK_STRATEGY` | Round-robin / priority |
| Async thumbnail queue | Decouple upload latency |
| Checksum scrubbing | Integrity audits |

Document new extensions as ADRs before changing contracts.
