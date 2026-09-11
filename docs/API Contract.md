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
| `RESUME_NAME_INVALID` / `RESUME_DEPTH_INVALID` / `RESUME_STATUS_INVALID` / `RESUME_FIELD_INVALID` | 400 |
| `RESUME_NODE_EXISTS` / `RESUME_META_INVALID` | 409 |
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

## 9. Resumes — `/api/resumes` (FAMILY | ADMIN)

Job-application workspace. Tree levels are S3 directories under `users/{user_id}/resumes`;
metadata is YAML written through the shared `FileService`. See
[E-RESUMES](./epics/resumes/init.md) and [ADR-010](./adr/ADR-010-resumes-yaml-tree-on-s3.md).

`path` is always relative to the resumes root and identifies a node by its depth:
`""` = root (children are countries), `"{country}"`, `"{country}/{company}"`,
`"{country}/{company}/{vacancy}"`.

### Tree

| Method | Path | Body / Query | Success |
|---|---|---|---|
| `GET` | `/api/resumes/tree` | `path` (default `""`) | `{ path, level, items: ResumeNode[] }` |
| `POST` | `/api/resumes/tree` | `{ path, name }` | `201` `ResumeNode` |
| `PATCH` | `/api/resumes/tree` | `{ path, new_name }` | `200` `ResumeNode` |
| `DELETE` | `/api/resumes/tree` | `path` (required) | `204` |

**ResumeNode**: `name`, `path`, `level` (`COUNTRY` \| `COMPANY` \| `VACANCY`), `child_count`,
`modified_at`, and for `VACANCY` only: `status_id` (`string \| null`), `website_url` (`string`).

`level` is derived from depth. `GET` on a vacancy path returns its attachment folders, not vacancies.
`POST` below vacancy depth → `400 RESUME_DEPTH_INVALID`; create folders inside a vacancy through
`/api/resumes/files/mkdir` instead. Creating a vacancy also writes its `meta.yaml`; the optional
`status_id` may be supplied on `POST` and must exist in the status list.

`DELETE` is recursive and hard: objects, `file_records` rows and quota are released together.

### Flat vacancy list

| Method | Path | Query | Success |
|---|---|---|---|
| `GET` | `/api/resumes/vacancies` | — | `{ items: VacancyListItem[] }` |

**VacancyListItem**: `country`, `company`, `name`, `path`, `status_id` (`string | null`),
`website_url` (`string`), `modified_at`.

Every vacancy in the tree, ordered by country, then company, then vacancy name (case-insensitive).
Countries and companies with no vacancies contribute nothing. A vacancy whose `meta.yaml` is corrupt
is still listed, with `status_id: null` and an empty `website_url` — the same degradation the tree
listing applies. Cost is one listing per country and per company plus one `meta.yaml` read per
vacancy (ADR-010).

### Vacancy metadata

| Method | Path | Body / Query | Success |
|---|---|---|---|
| `GET` | `/api/resumes/vacancy` | `path` (vacancy) | `200` `VacancyMeta` |
| `PUT` | `/api/resumes/vacancy` | `path` + `{ website_url, status_id, fields }` | `200` `VacancyMeta` |

**VacancyMeta**: `path`, `name`, `website_url` (`string`, `""` when unset), `status_id`
(`string \| null`), `fields` (`[{ name, value }]`, order preserved).

`website_url` is optional. A non-empty value without a scheme is stored as `https://<value>`.
`status_id` that no longer exists is returned as-is and rendered as “No status” (statuses detach on
delete). Whole-document replacement, last write wins.

### Statuses

| Method | Path | Body | Success |
|---|---|---|---|
| `GET` | `/api/resumes/statuses` | — | `{ statuses: ResumeStatus[] }` |
| `PUT` | `/api/resumes/statuses` | `{ statuses: ResumeStatus[] }` | `{ statuses: ResumeStatus[] }` |

**ResumeStatus**: `id` (uuid4 hex; server-generated when omitted on `PUT`), `name`, `color`
(`#RRGGBB`).

First `GET` seeds `statuses.yaml` with Applied / Interview / Offer / Rejected when the file is
missing. `PUT` replaces the whole list; ids are stable across renames so vacancy references survive.

### Vacancy files — `/api/resumes/files`

Same request/response contract as [§4 Files](#4--files--apifiles-authenticated), scoped to
`FileSection.RESUMES`: `GET ""`, `POST /upload`, `POST /upload-zip`, `GET /download`,
`GET /download-folder`, `DELETE ""`, `POST /mkdir`, `PATCH /rename`, `GET /search` (`501`).
`path` is relative to the resumes root, so the SPA passes
`{country}/{company}/{vacancy}/…`. Quota errors behave exactly as on `/api/files`.

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `RESUME_NAME_INVALID` | 400 | Empty/oversized name, `/` or `\`, `.`/`..`, or a reserved name (`meta.yaml`, `statuses.yaml`) |
| `RESUME_DEPTH_INVALID` | 400 | Tree operation targets a depth outside country/company/vacancy |
| `RESUME_STATUS_INVALID` | 400 | Empty or duplicate status name, or a colour that is not `#RRGGBB` |
| `RESUME_FIELD_INVALID` | 400 | Empty or duplicate vacancy field name |
| `RESUME_NODE_EXISTS` | 409 | A sibling with that name already exists (case-insensitive) |
| `RESUME_META_INVALID` | 409 | `meta.yaml` / `statuses.yaml` is not valid YAML of the expected shape; read does not overwrite |
| `ACCESS_DENIED` | 403 | STRANGER (or BLOCKED) on any resumes route |
| `FILE_NOT_FOUND` | 404 | Unknown node path |

---

## 10. Admin — `/api/admin` (ADMIN only)

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

## 11. Frontend API Clients

| Client | Covers |
|---|---|
| `services/api.ts` | Axios instance, credentials, private-session interceptor |
| `filesApi.ts` | Files + shared (via prefix) |
| `photosApi.ts` | Photos |
| `privateApi.ts` | Unlock/session/quota/reset (+ file ops via FileManager) |
| `adminApi.ts` | Users + storage |
| `resumesApi.ts` | Resumes tree, vacancy metadata, statuses (+ file ops via FileManager) |

---

## 12. Contract Change Rules

1. Additive fields preferred; breaking renames require Master Document + ADR update.
2. New `error_code` values must be added to `ErrorCode` enum and this doc.
3. Reserved `501` endpoints must not silently start returning data without Flow/Test Spec updates.
