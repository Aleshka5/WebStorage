# Data Models — HomeCloud

> **Status:** Active  
> **Related:** [API Contract.md](./API%20Contract.md), [Design Spec.md](./Design%20Spec.md)

Metadata is authoritative in PostgreSQL. Blob bytes live on disk volumes. Redis holds ephemeral session material only.

---

## 1. Entity Relationship Overview

```
users 1──1 user_quota_usage
  │
  ├──* file_records
  └──* upload_sessions   (reserved / unused by services)

Disk volumes are configuration-driven (STORAGE_DISKS), not a DB table.
```

---

## 2. Enums

### Role (`STRANGER` | `FAMILY` | `ADMIN`)

| Method / rule | Meaning |
|---|---|
| `can_access_shared()` | FAMILY, ADMIN |
| `can_access_admin()` | ADMIN only |
| Default on register / first Google | `STRANGER` |

### FileSection

`PHOTOS` | `FILES` | `PRIVATE` | `SHARED`

### FileStatus

| Status | Meaning |
|---|---|
| `PENDING` | Upload in progress / not committed |
| `COMMITTED` | Durable & visible |
| `ARCHIVED` | Content moved to zstd archive path |
| `DELETED` | Soft-deleted / removed from active use |

Stale `PENDING` older than 1 hour → maintenance cleanup.

### ErrorCode

`QUOTA_EXCEEDED`, `UNSUPPORTED_FORMAT`, `PRIVATE_SESSION_EXPIRED`, `DISK_UNAVAILABLE`, `PATH_TRAVERSAL_DETECTED`, `FILE_NOT_FOUND`, `ACCESS_DENIED`, `TOO_MANY_ATTEMPTS`, `EMAIL_ALREADY_EXISTS`, `INVALID_CREDENTIALS`, `UNAUTHORIZED`, `USER_NOT_FOUND`, `INTERNAL_ERROR` (+ router-local `NOT_IMPLEMENTED`).

### Disk health (admin)

`HEALTHY` | `LOW_SPACE` | `UNAVAILABLE`

---

## 3. Domain Entities

### User

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `email` | string | Unique |
| `password_hash` | string \| null | Null for OAuth-only |
| `google_id` | string \| null | Unique when set |
| `role` | Role | |
| `is_active` | bool | Block sets false |
| `created_at` | datetime | |

### FileRecord

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | Owner (shared files still have uploader) |
| `disk_id` | string | e.g. `disk1` |
| `relative_path` | string | Path under disk root |
| `original_name` | string | May be ciphertext for private |
| `size_bytes` | int | |
| `mime_type` | string | |
| `is_encrypted` | bool | |
| `section` | FileSection | |
| `status` | FileStatus | |
| `checksum_sha256` | string \| null | |
| `created_at` | datetime | |
| `last_accessed_at` | datetime | Archive eligibility |
| `is_archived` | bool | |
| `archive_path` | string \| null | |

### DiskVolume

| Field | Type | Notes |
|---|---|---|
| `id` | string | Matches env disk id |
| `mount_path` | Path | `{STORAGE_ROOT}/{id}` |
| `priority` | int | Reserved for strategy |
| `is_active` | bool | |

### StorageQuota (value object)

| Field | Type |
|---|---|
| `used_bytes` | int |
| `limit_bytes` | int |

Helpers: `available_bytes()`, `is_exceeded()`.

---

## 4. ORM Tables

### `users`

| Column | Constraints |
|---|---|
| `id` | UUID PK |
| `email` | String(255) unique, indexed |
| `password_hash` | String(255) nullable |
| `google_id` | String(255) unique, indexed, nullable |
| `role` | Enum default `STRANGER` |
| `is_active` | Boolean default true |
| `created_at` | timestamptz server default now |

Relationships: `file_records`, `quota_usage` (1:1), `upload_sessions`.

### `file_records`

Mirrors domain FileRecord; FK `user_id` → `users.id` ON DELETE CASCADE; indexed by `user_id`.

### `user_quota_usage`

| Column | Notes |
|---|---|
| `user_id` | PK / FK |
| `total_bytes` | All sections contributing to quota |
| `private_bytes` | Private section usage |
| `private_limit_bytes` | Admin-configured private cap |
| `photos_bytes` | Photo usage (denormalized) |
| `updated_at` | |

### `upload_sessions` (reserved)

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `user_id` | FK |
| `target_path` | |
| `total_size` / `received_bytes` | |
| `is_encrypted` | |
| `expires_at` / `created_at` | |

Not used by current application services.

---

## 5. Filesystem Layout Mapping

| Section | Path pattern |
|---|---|
| Photos originals | `{disk}/users/{user_id}/photos/originals/{id}{ext}` |
| Photos previews | `{disk}/users/{user_id}/photos/previews/{id}_thumb.jpg` |
| Files | `{disk}/users/{user_id}/files/{relative}` |
| Private | `{disk}/users/{user_id}/private/{encrypted_relative}` + `.marker` |
| Shared | `{disk}/shared/{relative}` |
| DB backups | `{first_disk}/_meta/backups/db_backup_*.sql.zst` |
| Archive temp | `{disk}/.archive_tmp/` |

---

## 6. Redis Keys

| Prefix | Payload | TTL |
|---|---|---|
| `private_key:` | Encoded AES key for session | `PRIVATE_SESSION_TTL_HOURS` (sliding) |
| `oauth_state:` | CSRF state | 600s |
| `oauth_ticket:` | One-time login bridge | 120s |
| `auth_rate:` | Login/register counters | window |
| `unlock_attempts:` | Private unlock attempts | ~900s |

Keying private session by JWT cookie value binds vault unlock to the auth cookie without storing passphrase.

---

## 7. API / UI DTOs (logical)

| DTO | Fields (core) |
|---|---|
| `UserResponse` | `user_id`, `email`, `role` |
| `FileNode` | `name`, `is_dir`, `size`, `modified_at`, `path`, `uploaded_by?` |
| `PhotoItem` | `id`, `preview_url`, `original_url`, `created_at`, `size` |
| `QuotaResponse` | `used_bytes`, `limit_bytes`, `private_bytes`, `private_limit_bytes` |
| `DiskStat` | id, total/used/free bytes, status |
| `UserAdminView` | identity + role + activity + usage/limits |

Frontend mirrors: `types/files.ts`, `types/photos.ts`, auth store `User`.

---

## 8. Invariants

1. A committed file has matching FS object (or archive path if archived).
2. Quota counters must not go negative; reconcile repairs drift.
3. Private names/content unreadable without session key.
4. STRANGER total usage ≤ `STRANGER_QUOTA_MB * 1MiB`.
5. Shared ACL: delete only by owner or ADMIN.
6. Soft domain rules: admin cannot delete self or demote/change own role via admin API.

---

## 9. Migrations

Alembic under `backend/alembic/versions/`:

- `001_initial` — core schema
- `002_add_google_id`
- `003_add_private_limit_bytes`

Entrypoint runs `alembic upgrade head` on container start.
