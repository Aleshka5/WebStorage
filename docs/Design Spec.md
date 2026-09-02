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
| Domain exceptions | Mapped to HTTP in presentation |

### Application services

| Service | Responsibility |
|---|---|
| `AuthService` | Register, login, Google link/create, JWT |
| `FileService` | List/upload/download/mkdir/rename/delete/ZIP for a section |
| `PhotoService` | Photo upload, thumbnails, pagination, delete |
| `PrivateService` | Unlock/lock/reset; builds encrypted `FileService` |
| `KeysRegistryService` | Bootstrap/parse/save `Keys/keys.yaml` via unlocked private `FileService` |
| `AdminService` | Users, roles, private quotas, block, delete, disk stats |
| `ArchiveService` | Idle-file zstd archive + stats |
| `BackupService` | `pg_dump` + zstd + retention |
| `MaintenanceService` | PENDING cleanup, `.tmp` cleanup, quota reconcile |

### Infrastructure

| Component | Notes |
|---|---|
| `S3StorageAdapter` | MinIO object I/O; SHA-256; logical-key traversal checks; hides `.tmp`/dot prefixes |
| `EncryptedStorageAdapter` | Decorator over `StorageAdapter`: AES-GCM content + encrypted names; PBKDF2 key |
| `DiskRouter` | Pick write bucket by free space (MinIO capacity); cache TTL |
| `ThumbnailService` | Pillow (+ HEIF); max edge `THUMBNAIL_MAX_PX` |
| `ArchiveManager` | zstd L22; `pre_encrypt` / `post_encrypt` |
| `SessionStore` (Redis) | Private keys, OAuth state/ticket, rate limits |
| Repositories | `UserRepository`, `FileRepository`, `QuotaRepository` |
| ORM models | SQLAlchemy 2 async |

### Presentation

- Routers: `auth`, `files`, `shared`, `photos`, `private`, `quota`, `admin`.
- Cookie auth dependency; role checks for shared/admin.
- Global exception handlers → structured `error_code`.
- Rate-limit middleware on sensitive auth routes.

---

## 3. Frontend Architecture

```
frontend/src/
├── pages/            # Auth, Photos, Files, Private, Keys Registry, Shared, Admin
├── components/
│   ├── Layout/       # AppLayout, Header, Sidebar, StorageUsageBar
│   ├── FileManager/  # Reusable manager (plain | encrypted)
│   ├── PhotoGrid/    # Grid, Lightbox, FAB
│   └── ui/           # Button, Input, Modal, ErrorMessage
├── services/         # Axios clients (files, photos, private, admin, api)
├── store/            # Zustand: auth, quota
├── hooks/            # upload, infinite scroll, private session
└── router.tsx        # Guards + bootstrap fetchMe
```

**FileManager contract**

```ts
mode: "plain" | "encrypted"
apiPrefix: "/api/files" | "/api/private" | "/api/shared"
```

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

---

## 6. Integrations

| Integration | Purpose | Notes |
|---|---|---|
| Google OAuth 2.0 | Social login | State in Redis; ticket bridge to set cookie |
| PostgreSQL | Metadata | Alembic migrations on startup |
| Redis | Sessions / rate limits | Required for private unlock & OAuth |
| MinIO | Blob storage | Buckets 1:1 with `STORAGE_DISKS`; app does not mount blob disk |
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

Compose-only: `POSTGRES_*`. Frontend-only: `VITE_API_BASE_URL` (dev).

---

## 8. Security Design

1. JWT in httpOnly cookie (`access_token`); SameSite=lax in current stack (TZ prefers Strict + Secure — see ADR).
2. Path normalization + traversal rejection (`PATH_TRAVERSAL_DETECTED`).
3. Auth rate limits; private unlock ≤5 attempts / window then `TOO_MANY_ATTEMPTS` (optional reset).
4. Shared delete: owner or ADMIN only.
5. Admin cannot change own role or delete self.
6. Never log passphrase, JWT, or derived keys.

---

## 9. Observability

Structured JSON logs (loguru + JSON formatter). Preferred fields: `timestamp`, `level`, `user_id`, `action`, `file_id`, `disk_id`, `result`, `error_code`.

Health: `GET /`, `GET /health`. Admin: `GET /api/admin/storage/health`.

---

## 10. Deployment Topology

**Docker Compose:** `app` (:8000), `frontend` (:80), `db`, `redis`, `minio` (:9000/:9001), `minio-init` (bucket bootstrap). App does not mount a blob disk; MinIO owns `/data`.

**Init:** `minio-init` (buckets), `scripts/init_db.py` (first ADMIN), Alembic upgrade on entrypoint.

**K8s:** `deployment.yaml` + secrets via `generate-secret.sh`. Blobs are MinIO; do not mount `/storage` on the app pod.

---

## 11. Extension Points (reserved)

| Point | Intent |
|---|---|
| `upload_sessions` table | Chunked/resumable uploads |
| `/search` endpoints | Filename/content search |
| `DISK_STRATEGY` | Round-robin / priority |
| Async thumbnail queue | Decouple upload latency |
| Checksum scrubbing | Integrity audits |

Document new extensions as ADRs before changing contracts.
