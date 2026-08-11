# API Contract — HomeCloud

> **Status:** Active  
> **Base URL:** `/api` (SPA proxies to backend)  
> **Auth:** Cookie `access_token` (JWT, HttpOnly) unless noted  
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
| `TOO_MANY_ATTEMPTS` | 429 (+ optional `retry_after`) |
| `DISK_UNAVAILABLE` | 503 |
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

### `POST /api/auth/register`

- Auth: public; rate limit ~5 / 60s
- Body: `{ "email": string, "password": string }`
- `201` `UserResponse`: `{ user_id, email, role }`
- Errors: `409 EMAIL_ALREADY_EXISTS`, `429 TOO_MANY_ATTEMPTS`

### `POST /api/auth/login`

- Auth: public; rate limit ~10 / 60s
- Body: `{ "email", "password" }`
- `200` `UserResponse` + Set-Cookie `access_token`
- Errors: `401 INVALID_CREDENTIALS`, `429`

### `GET /api/auth/google`

- `307` → Google authorize URL (requires OAuth configured; else `503`)

### `GET /api/auth/google/callback`

- Query: `code`, `state`
- Validates Redis state; creates/links user (default role `STRANGER`)
- `307` → frontend session bridge with one-time `ticket`

### `GET /api/auth/google/session`

- Query: `ticket`
- Consumes ticket; sets cookie; `307` → `{FRONTEND_URL}/files`

### `POST /api/auth/logout`

- Clears cookie; `204`

### `GET /api/auth/me`

- Auth required
- `200` `UserResponse`

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

---

## 9. Admin — `/api/admin` (ADMIN only)

### Users

| Method | Path | Request | Success |
|---|---|---|---|
| `GET` | `/api/admin/users` | `page`, `limit` (1–100), optional `role`, `email` | `{ items: UserAdminView[], total }` |
| `PATCH` | `/api/admin/users/{user_id}/role` | `{ "role": "STRANGER\|FAMILY\|ADMIN" }` | `{ user_id, email, role }` |
| `PATCH` | `/api/admin/users/{user_id}/quota` | `{ "private_limit_gb": number ≥ 0 }` | `204` |
| `POST` | `/api/admin/users/{user_id}/block` | — | `204` (`is_active=false`) |
| `DELETE` | `/api/admin/users/{user_id}` | — | `204` |

Errors: self role change / self delete → `403`; missing user → `404 USER_NOT_FOUND`.

**UserAdminView** (representative): `user_id`, `email`, `role`, `is_active`, `created_at`, usage/quota fields as returned by service.

### Storage / ops

| Method | Path | Success |
|---|---|---|
| `GET` | `/api/admin/storage` | `{ disks: DiskStat[] }` |
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
