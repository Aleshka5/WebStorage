# API Contract — HomeCloud

> **Status:** Active  
> **Base URL:** `/api` (SPA proxies to backend)  
> **Auth:** Gateway identity headers (`X-User-Id` / `X-Auth-User-Id`, `X-Storage-Role`). Cookie `auth_session` is vault-only. See [E-GWUS](./epics/gateway-user-service/init.md).  
> **Error shape:** `{ "detail": { "error_code": "<CODE>", "message": "<text>", ... } }`  
> **SSE:** None (downloads use `StreamingResponse` only)

Related: [Data Models.md](./Data%20Models.md), [Design Spec.md](./Design%20Spec.md).

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Content types | JSON unless multipart upload or binary download |
| IDs | UUID strings |
| Paths | Query/body relative paths; leading `/` accepted; traversal rejected |
| Success empties | Prefer `204 No Content` |
| Pagination | `page` (1-based), `limit` |

### Common error codes

| Code | Typical HTTP |
|---|---|
| `UNAUTHORIZED` | 401 |
| `INVALID_CREDENTIALS` | 401 |
| `PRIVATE_SESSION_EXPIRED` | 401 |
| `ACCESS_DENIED` | 403 |
| `FILE_NOT_FOUND` / `USER_NOT_FOUND` | 404 |
| `EMAIL_ALREADY_EXISTS` | 409 |
| `QUOTA_EXCEEDED` | 413 (+ optional `available_bytes`) |
| `UNSUPPORTED_FORMAT` / `PATH_TRAVERSAL_DETECTED` | 400 |
| `KEY_NAME_EMPTY` / `KEY_VALUE_EMPTY` / `KEY_NAME_DUPLICATE` | 400 |
| `KEYS_YAML_INVALID` | 409 (existing `keys.yaml` is not a flat string map; GET does not overwrite) |
| `TOO_MANY_ATTEMPTS` | 429 (+ optional `retry_after`) |
| `DISK_UNAVAILABLE` | 503 |
| `USER_SERVICE_UNAVAILABLE` | 503 (User-Service down/timeout/failed role lookup; **E-GWUS**) |
| `INTERNAL_ERROR` | 500 |
| `NOT_IMPLEMENTED` | 501 |

---

## 2. Health

| Method | Path | Auth | Response |
|---|---|---|---|
| `GET` | `/` | — | `{ "status": "ok", "version": "1.0" }` |
| `GET` | `/health` | — | `{ "status": "ok" }` |

---

## 3. Auth — `/api/auth`

Identity is resolved from gateway headers. WebStorage does not issue JWTs, run OAuth, or log the user out.

| Header | Use |
|---|---|
| `X-User-Id` | Preferred user UUID |
| `X-Auth-User-Id` | Fallback user UUID |
| `X-Storage-Role` | Storage role if a valid enum (`ADMIN` \| `FAMILY` \| `STRANGER` \| `BLOCKED`) |
| `X-Auth-Email` | Email for local upsert |
| `X-Auth-Role` | **Ignored** (hub `global_role`) |

Missing / invalid user id → `401 UNAUTHORIZED`. Invalid `X-Storage-Role` → `500 INTERNAL_ERROR`. Role lookup failure → `503 USER_SERVICE_UNAVAILABLE`. `BLOCKED` → `403 ACCESS_DENIED`.

### `POST /api/auth/register` / `login` / `google*`

- `410` retired. Sign in via the Auth hub.

### `POST /api/auth/logout`

- `410`. Logout is the hub’s job. This service does not clear cookies or delete the vault Redis key.

### `GET /api/auth/me`

- Auth: identity headers (and User-Service fallback for storage role / email)
- `200` `{ user_id, email, role }` where `role` is the **storage** role
- Errors: `401 UNAUTHORIZED`, `403 ACCESS_DENIED`, `503 USER_SERVICE_UNAVAILABLE`

---

## 4. Files — `/api/files` (authenticated)

Section on disk: `users/{user_id}/files`. Section enum: `FILES`.

| Method | Path | Request | Success |
|---|---|---|---|
| `GET` | `/api/files` | Query `path` default `/` | `FileNodeResponse[]` |
| `POST` | `/api/files/upload` | Query `path`; multipart `file` | `201 FileRecordResponse` |
| `POST` | `/api/files/upload-zip` | Query `path`; multipart `.zip` | `201 ZipUploadResponse` |
| `GET` | `/api/files/download` | Query `path` | Stream + Content-Disposition |
| `GET` | `/api/files/download-folder` | Query `path` | `application/zip` stream |
| `DELETE` | `/api/files` | Query `path` | `204` |
| `POST` | `/api/files/mkdir` | Body `{ path, name }` | `201 { path, name }` |
| `PATCH` | `/api/files/rename` | Body `{ path, new_name }` | `FileRecordResponse` or `204` (dir) |
| `GET` | `/api/files/search` | Query `q` | `501` |

### Schemas

**FileNodeResponse**

```json
{
  "name": "report.pdf",
  "is_dir": false,
  "size": 1024,
  "modified_at": "2026-08-01T12:00:00Z",
  "path": "/docs/report.pdf",
  "uploaded_by": null
}
```

**FileRecordResponse**

```json
{
  "id": "uuid",
  "name": "report.pdf",
  "size": 1024,
  "section": "FILES",
  "status": "COMMITTED",
  "created_at": "2026-08-01T12:00:00Z"
}
```

**ZipUploadResponse**

```json
{ "files": 3, "dirs": 1, "total_bytes": 4096 }
```

Notable errors: `413 QUOTA_EXCEEDED`, `400 PATH_TRAVERSAL_DETECTED`, `404 FILE_NOT_FOUND`.

---

## 5. Shared — `/api/shared` (FAMILY | ADMIN)

Same surface as Files (list/upload/upload-zip/download/download-folder/delete/mkdir/rename/search).

- Disk root: `{disk}/shared/...`
- Section: `SHARED`
- List may include `uploaded_by`
- Delete: owner or `ADMIN` only → else `403 ACCESS_DENIED`
- Search: `501`

---

## 6. Photos — `/api/photos` (authenticated)

Allowed extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.heic`, `.heif`.

| Method | Path | Request | Success |
|---|---|---|---|
| `GET` | `/api/photos` | `page`, optional `limit` | `PhotoListResponse` |
| `POST` | `/api/photos/upload` | multipart `file` | `201 PhotoItemResponse` |
| `GET` | `/api/photos/{photo_id}/preview` | — | JPEG (or original mime) |
| `GET` | `/api/photos/{photo_id}/original` | — | Stream |
| `DELETE` | `/api/photos/{photo_id}` | — | `204` |

**PhotoItemResponse**

```json
{
  "id": "uuid",
  "preview_url": "/api/photos/{id}/preview",
  "original_url": "/api/photos/{id}/original",
  "created_at": "2026-08-01T12:00:00Z",
  "size": 204800
}
```

**PhotoListResponse**

```json
{
  "items": [/* PhotoItemResponse */],
  "total": 120,
  "has_next": true
}
```

Default `limit` = `PHOTO_BATCH_SIZE` (30).

---

## 7. Private — `/api/private`

### Session / vault control

| Method | Path | Auth | Body / notes | Success |
|---|---|---|---|---|
| `POST` | `/api/private/unlock` | Auth | `{ "passphrase": string }` | `{ "success": true }` |
| `POST` | `/api/private/lock` | Cookie present | — | `204` |
| `POST` | `/api/private/reset` | Auth | Only when unlock rate-limited | `204` (wipes private data) |
| `GET` | `/api/private/quota` | Auth | — | `{ private_bytes, private_limit_bytes }` |
| `GET` | `/api/private/session` | Cookie | — | `{ active, expires_in_seconds }` |

Unlock errors: `401` invalid passphrase path via domain mapping; `429 TOO_MANY_ATTEMPTS` (`retry_after` ~900).

### File ops (require active private session)

Same shapes as Files under `/api/private` (+ upload/download/download-folder/delete/mkdir/rename).  
Missing/expired key → `401 PRIVATE_SESSION_EXPIRED` (do **not** clear main auth).  
Search → `501`.

Private quota overruns → `413 QUOTA_EXCEEDED`.

### Keys Registry (require active private session)

Same vault as Private file ops (`auth_session` Redis key). All roles except `BLOCKED`. Bootstrap on first `GET`: create `Keys/` if missing; create empty `keys.yaml` if missing.

Canonical private path: `Keys/keys.yaml`.

| Method | Path | Auth | Body / notes | Success |
|---|---|---|---|---|
| `GET` | `/api/private/keys` | Private session | Creates folder/file if absent. Invalid existing YAML → `409 KEYS_YAML_INVALID` (file not rewritten). | `{ "keys": [ { "name": string, "value": string }, ... ] }` (full values; insertion order) |
| `PUT` | `/api/private/keys` | Private session | `{ "keys": [ { "name", "value" }, ... ] }`. Trimmed non-empty unique names and values. Writes the whole file in place (one `FileRecord`; quota = size delta). Recreates the file if it was deleted in FileManager. | Same shape as GET |

Validation: empty name → `400 KEY_NAME_EMPTY`; empty value → `400 KEY_VALUE_EMPTY`; duplicate names → `400 KEY_NAME_DUPLICATE`.  
Missing/expired vault key → `401 PRIVATE_SESSION_EXPIRED`.

---

## 8. Quota — `/api/quota`

### `GET /api/quota/me`

```json
{
  "used_bytes": 1048576,
  "limit_bytes": 104857600,
  "private_bytes": 0,
  "private_limit_bytes": 1073741824
}
```

Note: TZ historically mentioned `total_bytes`; **implemented field is `limit_bytes`**.
`limit_bytes` is the per-user stored cap (default 100 MiB; `0` = unlimited), not remaining disk space.

---

## 9. Admin — `/api/admin` (ADMIN only)

### Users

| Method | Path | Request | Success |
|---|---|---|---|
| `GET` | `/api/admin/users` | `page`, `limit` (1–100), optional `role`, `email` | `{ items: UserAdminView[], total }` (`role` is live `storage_roles` from gRPC ListUsers; display-only; filters apply to live role/email) |
| `PATCH` | `/api/admin/users/{user_id}/role` | — | **410 Gone** — roles are changed in Auth-Service `/admin` |
| `PATCH` | `/api/admin/users/{user_id}/quota` | `{ "limit_mb"?: number ≥ 0, "private_limit_gb"?: number ≥ 0 }` (at least one required) | `204` |
| `POST` | `/api/admin/users/{user_id}/block` | — | `204` (`is_active=false`) |
| `DELETE` | `/api/admin/users/{user_id}` | — | `204` |

Errors: self delete → `403`; missing user → `404 USER_NOT_FOUND`. Role column is read-only; ListUsers outage → `503 AUTH_UNAVAILABLE` (no fallback to local `users.role`).

**UserAdminView** (representative): `id`, `email`, `role`, `is_active`, `created_at`, `quota_used_bytes`, `limit_bytes`, `private_limit_bytes`.

### Storage / ops

| Method | Path | Success |
|---|---|---|
| `GET` | `/api/admin/storage` | `{ disks: DiskStat[] }` — `DiskStat`: `id`, `bucket`, `total_bytes`, `used_bytes`, `free_bytes`, `status` |
| `GET` | `/api/admin/storage/health` | `{ disks: { [disk_id]: "HEALTHY\|LOW_SPACE\|UNAVAILABLE" } }` |
| `GET` | `/api/admin/archive/run` | `{ processed, skipped, errors }` |
| `GET` | `/api/admin/archive/stats` | `{ last_run, processed, skipped, errors, total_archived_bytes }` |
| `GET` | `/api/admin/maintenance/run` | Bundle of cleanup/reconcile results |
| `GET` | `/api/admin/maintenance/stats` | Timestamps + counts |
| `GET` | `/api/admin/backup/run` | `{ filename, path, size_bytes }` |
| `GET` | `/api/admin/backup/list` | `{ items: [{ filename, created_at, size_bytes }] }` |

---

## 10. Frontend API Clients

| Client | Covers |
|---|---|
| `services/api.ts` | Axios instance, credentials, private-session interceptor |
| `filesApi.ts` | Files + shared (via prefix) |
| `photosApi.ts` | Photos |
| `privateApi.ts` | Unlock/session/quota/reset (+ file ops via FileManager) |
| `adminApi.ts` | Users + storage |

---

## 11. Contract Change Rules

1. Additive fields preferred; breaking renames require Master Document + ADR update.
2. New `error_code` values must be added to `ErrorCode` enum and this doc.
3. Reserved `501` endpoints must not silently start returning data without Flow/Test Spec updates.
