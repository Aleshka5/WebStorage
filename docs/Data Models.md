# Data Models — HomeCloud

> **Status:** Active  
> **Related:** [API Contract.md](./API%20Contract.md), [Design Spec.md](./Design%20Spec.md)

Metadata is authoritative in PostgreSQL. Blob bytes live in MinIO (S3). Redis holds ephemeral session material only.

---

## 1. Entity Relationship Overview

```
users 1──1 user_quota_usage
  │
  ├──* file_records
  └──* upload_sessions   (reserved / unused by services)

One logical volume maps to MinIO bucket `storage`. Object keys stay `users/{id}/…`, `shared/…`, `_meta/backups/`.
```

---

## 2. Enums

### Role (`STRANGER` | `FAMILY` | `ADMIN` | `BLOCKED`)

Request-time **storage** role from `X-Storage-Role` or User-Service. Not stored on `users`.

| Method / rule | Meaning |
|---|---|
| `can_access_shared()` | FAMILY, ADMIN |
| `can_access_admin()` | ADMIN only |
| `BLOCKED` | `403 ACCESS_DENIED` on every authenticated route |

### FileSection

`PHOTOS` | `FILES` | `PRIVATE` | `SHARED` | `RESUMES` | `GENERATED`

`RESUMES` (Alembic `006`) scopes the job-application workspace at `users/{user_id}/resumes`. Plain, never encrypted; counts toward the total quota only. See [E-RESUMES](./epics/resumes/init.md).

`GENERATED` (Alembic `007`) scopes image runs at `users/{user_id}/generated`. Plain, never encrypted; `meta.yaml`, references, and `result.png` count toward the total quota only (not `photos_bytes` or `private_bytes`). Runs are absent from `/files`, `/photos`, `/private`, and `/shared`. See [E-GENERATED](./epics/generated-images/init.md).

### FileStatus

| Status | Meaning |
|---|---|
| `PENDING` | Upload in progress / not committed |
| `COMMITTED` | Durable & visible |
| `ARCHIVED` | Content moved to zstd archive path |
| `DELETED` | Soft-deleted / removed from active use |

Stale `PENDING` older than 1 hour → maintenance cleanup.

### IndexLinkState

Domain enum stored as `varchar(16)` on `file_index_links` (not a PostgreSQL enum).

`PENDING` | `SUBMITTED` | `INDEXED` | `FAILED` | `SKIPPED` | `REMOVED` | `ARCHIVED` | `REINDEX_PENDING`

Due for the sync job: `PENDING`, `SUBMITTED`, `REMOVED`, `ARCHIVED`, `REINDEX_PENDING`, when `next_attempt_at` is null or due. `FAILED`, `SKIPPED`, and a confirmed `REMOVED` / `ARCHIVED` (far-future `next_attempt_at`) stay out of the queue.

### ErrorCode

`QUOTA_EXCEEDED`, `UNSUPPORTED_FORMAT`, `PRIVATE_SESSION_EXPIRED`, `DISK_UNAVAILABLE`, `PATH_TRAVERSAL_DETECTED`, `FILE_NOT_FOUND`, `ACCESS_DENIED`, `TOO_MANY_ATTEMPTS`, `EMAIL_ALREADY_EXISTS`, `INVALID_CREDENTIALS`, `UNAUTHORIZED`, `USER_NOT_FOUND`, `INTERNAL_ERROR`, `RESUME_NAME_INVALID`, `RESUME_DEPTH_INVALID`, `RESUME_STATUS_INVALID`, `RESUME_FIELD_INVALID`, `RESUME_NODE_EXISTS`, `RESUME_META_INVALID`, `GENERATED_INVALID`, `GENERATED_META_INVALID`, `SEARCH_QUERY_INVALID`, `INDEXING_UNAVAILABLE` (+ router-local `NOT_IMPLEMENTED`).

### Disk health (admin)

`HEALTHY` | `LOW_SPACE` | `UNAVAILABLE`

---

## 3. Domain Entities

### User

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK (User-Service UUID) |
| `email` | string | Unique; upserted from headers / User-Service |
| `is_active` | bool | Block sets false |
| `created_at` | datetime | |
| `role` | Role | **Request-time only** — not a DB column |

### FileRecord

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | Owner (shared files still have uploader) |
| `disk_id` | string | Logical volume id (`storage`) |
| `relative_path` | string | Object key under the disk bucket (logical path) |
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
| `id` | string | Always `storage` |
| `bucket` | string | `S3_BUCKET` (default `storage`) |
| `priority` | int | Reserved for strategy |
| `is_active` | bool | |

### StorageQuota (value object)

| Field | Type |
|---|---|
| `used_bytes` | int |
| `limit_bytes` | int |

Helpers: `available_bytes()`, `is_exceeded()`, `is_unlimited()`, `would_exceed()`. `limit_bytes = 0` means unlimited.

---

## 4. ORM Tables

### `users`

| Column | Constraints |
|---|---|
| `id` | UUID PK |
| `email` | String(255) unique, indexed |
| `is_active` | Boolean default true |
| `created_at` | timestamptz server default now |

Relationships: `file_records`, `quota_usage` (1:1), `upload_sessions`.

### `file_records`

Mirrors domain FileRecord; FK `user_id` → `users.id` ON DELETE CASCADE; indexed by `user_id`.

A normal user delete sets `status = DELETED` and leaves the row in place. `hard_delete` removes the row.

### `file_index_links`

Alembic `008_add_file_index_links` (`down_revision = 007_add_generated_file_section`). One row per Files or Photos record that is, was, or should be indexed. No new column on `file_records`. Indexing does not change quota counters.

| Column | Constraints |
|---|---|
| `id` | UUID PK |
| `file_record_id` | UUID nullable, FK `file_records.id` `ON DELETE SET NULL`, unique. Many nulls allowed |
| `user_id` | UUID, not a FK. Owner copied from the file record |
| `section` | existing `file_section` enum. Application writes `FILES` or `PHOTOS` only |
| `s3_key` | text. Object key sent to RAG, no bucket prefix |
| `content_sha256` | `CHAR(64)` nullable. Null until a preview hash is read |
| `content_type` | text. Allowlist value, not `mimetypes.guess_type` |
| `rag_file_id` | UUID nullable, unique when set. Many nulls allowed |
| `state` | `varchar(16)`, an `IndexLinkState` value |
| `attempts` | int, default 0 |
| `next_attempt_at` | timestamptz nullable. Null means due now |
| `last_error_code` | text nullable |
| `created_at`, `updated_at` | timestamptz, server default `now()` |

Indexes: `(state, next_attempt_at)`, `(user_id, state)`, unique `file_record_id`, unique `rag_file_id`.

`ON DELETE SET NULL` clears `file_record_id` only when the `file_records` row is actually deleted. A normal user delete is a soft delete, so `file_record_id` stays set. `user_id` and `rag_file_id` are not cleared by that FK.

Private and encrypted records raise `PrivateIndexRefused` and get no row. `SHARED`, `RESUMES`, and `GENERATED` are never indexed.

Registration target (see [ADR-011](./adr/ADR-011-shared-minio-bucket-with-rag.md)):

| Record | Link |
|---|---|
| Files PDF, DOCX, XLSX | `PENDING`, collection `documents`, key is the file |
| JPEG, PNG, WebP, GIF at or under `RAG_MAX_IMAGE_BYTES` | `PENDING`, collection `images`, key is the file |
| Photos HEIC/HEIF, or a Photos image over the cap | `PENDING`, collection `images`, key `users/{user_id}/photos/previews/{id}_thumb.jpg` |
| Files HEIC/HEIF, or a Files image over the cap | `SKIPPED` / `unsupported_type` |
| Anything else in Files or Photos | `SKIPPED` / `unsupported_type` |
| Archived Files or Photos with no link (backfill) | `SKIPPED` / `archived`. Not sent to RAG |

Content types: `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `image/jpeg`, `image/png`, `image/webp`, `image/gif`. A preview is `image/jpeg`. Its hash is filled from the preview bytes at registration time.

### `user_quota_usage`

| Column | Notes |
|---|---|
| `user_id` | PK / FK |
| `total_bytes` | All sections contributing to quota |
| `limit_bytes` | Admin-configured total cap (default 100 MiB; `0` = unlimited) |
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

## 5. Object Key Layout (MinIO)

All keys live in the single MinIO bucket `storage`. Paths are POSIX-style object keys.

| Section | Key pattern |
|---|---|
| Photos originals | `users/{user_id}/photos/originals/{id}{ext}` |
| Photos previews | `users/{user_id}/photos/previews/{id}_thumb.jpg` |
| Files | `users/{user_id}/files/{relative}` |
| Private | `users/{user_id}/private/{encrypted_relative}` + `.marker` |
| Keys Registry | Same private vault; logical path `Keys/keys.yaml` (normal `FileRecord`, section `PRIVATE`) |
| Resumes tree | `users/{user_id}/resumes/{country}/{company}/{vacancy}/` (directories only) |
| Resume vacancy meta | `users/{user_id}/resumes/{country}/{company}/{vacancy}/meta.yaml` (`FileRecord`, section `RESUMES`) |
| Resume statuses | `users/{user_id}/resumes/statuses.yaml` (`FileRecord`, section `RESUMES`) |
| Resume attachments | `users/{user_id}/resumes/{country}/{company}/{vacancy}/{relative}` |
| Generated run | `users/{user_id}/generated/{id}/` (`meta.yaml`, `result.png`, `ref-N.png`; `FileRecord`, section `GENERATED`) |
| Shared | `shared/{relative}` |
| DB backups | `_meta/backups/db_backup_*.sql.zst` (bucket `storage`) |
| RAG index target | Same bucket `storage`. RAG reads the key already stored on the link. No second bucket. See [ADR-011](./adr/ADR-011-shared-minio-bucket-with-rag.md) |
| Archive / thumbnail staging | process `/tmp` only; persisted via `StorageAdapter` |

---

## 6. Redis Keys

| Prefix | Payload | TTL |
|---|---|---|
| `private_key:{auth_session}` | Encoded AES key for the vault | `PRIVATE_SESSION_TTL_HOURS` |
| `unlock_attempts:` | Private unlock attempts | ~900s |

Identity does not come from this cookie. Logout is not implemented here; hub logout makes the sid unreachable.

---

## 7. API / UI DTOs (logical)

| DTO | Fields (core) |
|---|---|
| `UserResponse` | `user_id`, `email`, `role` |
| `FileNode` | `name`, `is_dir`, `size`, `modified_at`, `path`, `uploaded_by?` |
| `PhotoItem` | `id`, `preview_url`, `original_url`, `created_at`, `size` |
| `QuotaResponse` | `used_bytes`, `limit_bytes`, `private_bytes`, `private_limit_bytes` |
| `KeysListResponse` | `keys: [{ name, value }]` (full values; flat YAML map on disk) |
| `ResumeNode` | `name`, `path`, `level` (`COUNTRY`\|`COMPANY`\|`VACANCY`), `child_count`, `modified_at`, `status_id?`, `website_url?` |
| `VacancyMeta` | `path`, `name`, `website_url`, `status_id \| null`, `fields: [{ name, value }]` |
| `ResumeStatus` | `id` (uuid4 hex), `name`, `color` (`#RRGGBB`) |
| `VacancyListItem` | `country`, `company`, `name`, `path`, `status_id \| null`, `website_url`, `modified_at` |
| `GeneratedCreated` | `id`, `created_at` |
| `GeneratedListItem` | `id`, `created_at`, `prompt` |
| `GeneratedRun` | `prompt`, `negative_prompt`, `seed`, `steps`, `true_cfg_scale`, `width`, `height`, `duration`, `references` (file URLs), `result` (file URL). No `id` or `created_at` |
| `DiskStat` | id, bucket, total/used/free bytes, status |
| `UserAdminView` | identity + role + activity + usage/limits |

Frontend mirrors: `types/files.ts`, `types/photos.ts`, `types/resumes.ts`, `types/generated.ts`, auth store `User`. The detail page fills `id` from the route and, when the GET body omits `created_at`, from the UTC stamp in the run id.

### Resume YAML documents

Not database rows — plain objects written through `FileService.overwrite_file`, one `FileRecord`
each, quota charged as a size delta on rewrite.

`statuses.yaml` (one per user):

```yaml
statuses:
  - id: 7f4c1e0a9b2d4f6e8a1c3b5d7e9f0a2b
    name: Applied
    color: "#38BDF8"
```

`meta.yaml` (one per vacancy):

```yaml
website_url: https://example.com/jobs/42
status_id: 7f4c1e0a9b2d4f6e8a1c3b5d7e9f0a2b
fields:
  - name: Salary
    value: 4000 EUR
```

`status_id` has no referential integrity by design: deleting a status leaves the id dangling and the
vacancy renders as "No status" (ADR-010).

### Generated run YAML

Not a database row. One directory per run, written through `FileService` with section `GENERATED`.
`{id}` is UTC `YYYYmmddTHHMMSSZ` plus `-` and 8 hex characters. Every string in `meta.yaml` is quoted.

```yaml
prompt: "A cinematic portrait of the person, soft rim light, 85mm lens"
negative_prompt: ""
seed: 1823486689
steps: 40
true_cfg_scale: 1.0
width: 2048
height: 2048
duration: 96.4
created_at: "2026-09-27T11:50:12Z"
references:
  - "ref-1.png"
result: "result.png"
```

`created_at` is `YYYY-MM-DDTHH:MM:SSZ`. `references` lists unique `ref-N.png` names in upload order.
`result` is `result.png`. A directory whose `meta.yaml` is missing or not this shape is omitted from
the list. `GET /api/generated/{id}` of that id is `409 GENERATED_META_INVALID` and does not rewrite
the file. Delete removes the objects, `file_records` (including `ARCHIVED`), and the total-quota bytes.

---

## 8. Invariants

1. A committed file has a matching MinIO object (or archive key if archived).
2. Quota counters must not go negative; reconcile repairs drift.
3. Private names/content unreadable without session key.
4. Total usage ≤ `user_quota_usage.limit_bytes` for every role, unless `limit_bytes` is `0` (unlimited).
5. Shared ACL: delete only by owner or ADMIN.
6. Soft domain rules: admin cannot delete self. Role writes are 410 (User-Service).
7. Indexing is a projection. Deleting vectors does not delete MinIO objects. A user delete soft-deletes `file_records` and leaves `file_index_links.file_record_id` set.

---

## 9. Migrations

Alembic under `backend/alembic/versions/`:

- `001_initial` — core schema
- `002_add_google_id`
- `003_add_private_limit_bytes`
- `004_add_user_limit_bytes` — per-user total cap (default 100 MiB)
- `005_drop_users_role_and_legacy_auth` — drop `users.role`, `password_hash`, `google_id`
- `006_add_resumes_file_section` — `file_section` value `RESUMES`
- `007_add_generated_file_section` — `file_section` value `GENERATED` (`ALTER TYPE ... ADD VALUE` outside a transaction; downgrade is a no-op)
- `008_add_file_index_links` — table `file_index_links`, FK `ON DELETE SET NULL`, unique `file_record_id` and `rag_file_id`, indexes on `(state, next_attempt_at)` and `(user_id, state)`

Entrypoint runs `alembic upgrade head` on container start.
