# Change Request: Storage → RAG indexing (vectorise the library)

> **ID:** E-SIDX
> **Status:** Planned (spec only; no code)
> **Repos:** WebStorage (this document). RAG side: [RAG-Chat `docs/user-stories.md` US-11…US-13](../../../../RAG-Chat/docs/user-stories.md) and [RAG-Chat `docs/integration-webstorage.md`](../../../../RAG-Chat/docs/integration-webstorage.md).
> **Specs to update when this ships:** [API Contract](../../API%20Contract.md), [Data Models](../../Data%20Models.md), [Flow Spec](../../Flow%20Spec.md), [Design Spec](../../Design%20Spec.md), [FRONTEND_DOCS](../../FRONTEND_DOCS.md), [BACKEND_DOCS](../../BACKEND_DOCS.md), [epics index](../init.md). An ADR is required for the shared MinIO bucket, because it changes the deployment topology.

**Actor:** A signed-in person who keeps PDFs, Office documents and photos in their own sections.

**Goal:** Every readable file in **Files** and **Photos** is copied into the RAG vector index, kept in sync on rename/move/delete, and the Photos page gets one line to find a photo by describing it.

**Value:** The library becomes searchable by meaning instead of by filename, without a second upload and without touching the private vault.

---

## 1. Background

RAG-Chat already owns the vector side: collection `documents` holds text chunks of a file, collection `images` holds one vector per image, both 768-dim, both filtered by `user_id`. It already accepts a file it did not upload: `POST /api/files/documents` and `POST /api/files/images` take a JSON manifest (`s3_key`, `file_name`, `content_sha256`, `file_size`, `content_type`) plus `X-Auth-User-Id`, `HEAD` the object in MinIO, and queue the existing pipeline.

This service already knows everything the manifest needs: `file_records` carries `user_id`, `relative_path` (the object key), `original_name`, `size_bytes`, `mime_type`, `checksum_sha256`, `is_encrypted`, `section`, `status`, `is_archived`. Blobs are plain bytes in MinIO for `FILES` and `PHOTOS`; only `PRIVATE` is encrypted.

Two gaps are closed by this epic and its RAG counterparts:

1. Words and spreadsheets. RAG today rejects anything but PDF and images with `415`. US-11 in RAG adds DOCX and XLSX to `documents`.
2. Two buckets. RAG reads only its own `documents` bucket. The owner decision is to merge: one MinIO bucket `storage`, and RAG reads the storage objects in place (RAG US-12).

## 2. Locked decisions

| # | Decision |
|---|---|
| D1 | Indexed sections: `FILES` and `PHOTOS` only. `SHARED`, `RESUMES`, `GENERATED` are not indexed. `PRIVATE` never — bytes are AES-256-GCM and the key never leaves Redis. |
| D2 | Collections are RAG's existing ones. Text (PDF, DOCX, XLSX) → `documents`. Images → `images`. No new collection, no per-section or per-user collection, no new dimension. |
| D3 | Object types: `.pdf`, `.docx`, `.xlsx`, `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`. Everything else is `SKIPPED` (`unsupported_type`) and stays out of the queue. Legacy `.doc`/`.xls` are out of scope. |
| D4 | One MinIO bucket `storage` for both services. Storage keeps writing `users/{user_id}/…`; RAG writes `rag/files/…` and reads storage keys in place. Storage never writes under `rag/`, `shared/`, `_meta/`, and RAG never writes or deletes under `users/`, `shared/`, `_meta/`. |
| D5 | Registration is asynchronous and never blocks an upload. The upload response does not wait for RAG, and an RAG outage does not turn a successful upload into an error. |
| D6 | The index is a projection. Deleting vectors never deletes bytes: removal is a RAG-side delete of points and RAG metadata only (`origin=storage`). Storage remains the only owner of its objects. |
| D7 | Rename/move keeps the RAG `file_id` and is sent as a re-key (`PUT /api/files/{file_id}/object`), not as delete + re-register. No window where the file is missing from search. |
| D8 | Changed content is not a re-key: the old index entry is deleted, then the file is registered again (`REINDEX_PENDING`). The `file_id` changes. |
| D9 | HEIC/HEIF photos and any image larger than `RAG_MAX_IMAGE_BYTES` (10 MiB, RAG's `EMBEDDING_MAX_IMAGE_BYTES`) are indexed through the JPEG preview `photos/previews/{id}_thumb.jpg` that the Photos service already generates. Files-section images have no preview, so those are `SKIPPED`. |
| D10 | Archived files leave the index. The archive job replaces the hot object with `….zst`, so the RAG key stops resolving; the link goes to `ARCHIVED` and the vectors are removed. Nothing re-indexes on restore. |
| D11 | Indexing costs no quota and changes no quota counter. `meta.yaml`-style sidecars and `file_records` are untouched. |
| D12 | Identity sent to RAG is `X-Auth-User-Id` = the file owner UUID. No cookie, no `X-Auth-Role`. |
| D13 | The RAG entry point is `http://rag-edge:8080` on the external `rag_edge` network. Storage does not join `rag_internal`. |
| D14 | One new PostgreSQL table, no new column on `file_records`. Alembic revision `008`. |
| D15 | UI copy is English, same as the rest of the app. |
| D16 | No new container: the sync job runs inside `app` under the existing APScheduler, like the archive and cleanup jobs. |

## 3. Scope matrix

| Section | PDF | DOCX | XLSX | JPEG / PNG / WebP / GIF | HEIC / HEIF, or image > 10 MiB |
|---|---|---|---|---|---|
| Files | `documents` | `documents` | `documents` | `images` (original) | `SKIPPED` — no preview exists |
| Photos | — | — | — | `images` (original) | `images` (JPEG preview) |
| Shared / Resumes / Generated | not indexed | | | | |
| Private | never indexed | | | | |

## 4. Data model

New table `file_index_links` — one row per file record that is, was, or should be indexed.

| Column | Type | Rule |
|---|---|---|
| `id` | UUID | PK |
| `file_record_id` | UUID \| null | FK `file_records.id` `ON DELETE SET NULL`. The row outlives the file so the removal can still reach RAG. |
| `user_id` | UUID | Owner, from `file_records.user_id`. Not a FK preference: survives record deletion. |
| `section` | `FileSection` | `FILES` or `PHOTOS` only. |
| `s3_key` | text | Key as sent to RAG, without the disk prefix: `users/{user_id}/files/{relative}` or `users/{user_id}/photos/originals/{id}{ext}` or `users/{user_id}/photos/previews/{id}_thumb.jpg`. |
| `content_sha256` | char(64) | `file_records.checksum_sha256`, or SHA-256 of the preview bytes when the target is a preview. |
| `content_type` | text | `application/pdf`, `…wordprocessingml.document`, `…spreadsheetml.sheet`, or one of `image/jpeg`, `image/png`, `image/webp`, `image/gif`. |
| `rag_file_id` | UUID \| null | RAG `file_id` returned by registration. Unique when set. |
| `state` | varchar(16) | `PENDING`, `SUBMITTED`, `INDEXED`, `FAILED`, `SKIPPED`, `REMOVED`, `ARCHIVED`, `REINDEX_PENDING`. |
| `attempts` | int | Failed attempts since the last reset. |
| `next_attempt_at` | timestamptz \| null | Backoff. `NULL` means "now". |
| `last_error_code` | text \| null | RAG `error` code or a local reason (`unsupported_type`, `preview_missing`, `timeout`, `unauthorized`, `dependency_unavailable`, `duplicate_conflict`). |
| `created_at`, `updated_at` | timestamptz | |

Indexes: `(state, next_attempt_at)`, `(user_id, state)`, unique `(rag_file_id)`, `(file_record_id)`.

State machine:

```text
upload/commit ─► PENDING ─► SUBMITTED ─► INDEXED
                    │            │
                    │            ├─► FAILED   (RAG error, attempts exhausted)
                    │            └─► REMOVED  (deleted while indexing)
                    ├─► SKIPPED  (type, size, missing preview)
                    └─► FAILED   (transport, 4xx other than 409)

rename/move of INDEXED ─► PENDING (re-key, rag_file_id kept)
content change of INDEXED ─► REINDEX_PENDING ─► REMOVED ─► PENDING (new rag_file_id)
delete / delete-dir / archive ─► REMOVED | ARCHIVED
```

## 5. Sync flow

### 5.1 Where rows are created or touched

| Event | Hook | Action |
|---|---|---|
| File upload committed | `FileService.upload_file` | insert `PENDING` (same transaction as the commit) |
| ZIP upload | `FileService.upload_zip_folder` | insert one `PENDING` per committed entry |
| File overwritten | `FileService.overwrite_file` | content changed → `REINDEX_PENDING` if `rag_file_id` set, else refresh `s3_key`/hash and stay `PENDING` |
| Photo upload committed | `PhotoService.upload_photo` | insert `PENDING` |
| Rename / move | `FileService.rename`, `rename_by_path` | `INDEXED` → `PENDING` + `next_attempt_at=now`, new `s3_key`; not yet registered → update `s3_key` only |
| Directory rename / move | `FileService.rename_by_path` (directory branch) | same, for every descendant with a link row |
| File delete | `FileService.delete_file`, `delete_by_path` | `REMOVED` (retry until RAG confirms) |
| Directory delete | `FileService.delete_directory_recursive` | `REMOVED` for every descendant; the rows keep `user_id` and `rag_file_id` after `file_record_id` becomes `NULL` |
| Photo delete | `PhotoService.delete_photo` | `REMOVED` |
| Archive run | `ArchiveService._archive_record` | `ARCHIVED`, then remove the vectors |
| Private anything | — | no row is ever written; `section == PRIVATE` is refused in the repository layer |
| Backfill | `POST /api/admin/indexing/backfill` | `PENDING` for every `COMMITTED` record with no active link row |

### 5.2 The job

APScheduler job `index_sync`, interval `RAG_SYNC_INTERVAL_SECONDS` (default 60), single-flight, `RAG_INDEX_BATCH_SIZE` (20) rows per tick. Three queues in one pass:

1. `PENDING` — build the manifest and call RAG. `202` → `SUBMITTED` + `rag_file_id`. `409 duplicate_file` → if `details.file_id` is ours, `SUBMITTED`; otherwise `FAILED duplicate_conflict`. `404`/`415`/`400` → `FAILED` with the RAG code. `503`/`504`/network → `attempts += 1`, exponential backoff `min(3600, 30 × 2^attempts)`, `FAILED` after `RAG_MAX_ATTEMPTS` (8).
2. `SUBMITTED` — `GET /api/files/{rag_file_id}`: `ready` → `INDEXED`; `error` → `FAILED` + `error` code; `deleting`/`deleted`/`404` → `REMOVED`; `uploaded`/`processing` → keep, backoff, `FAILED timeout` after `RAG_INDEX_MAX_WAIT_SECONDS` (3600).
3. `REMOVED`, `ARCHIVED`, `REINDEX_PENDING` — `DELETE /api/files/{rag_file_id}`; `202`/`409 file_busy`/`404` all count as "vectors gone". `REINDEX_PENDING` then falls through to a fresh `PENDING` with a new `rag_file_id`.

RAG calls, all with `X-Auth-User-Id` and an optional `X-Request-Id`:

| Purpose | Call |
|---|---|
| Register text file | `POST /api/files/documents` with `origin: "storage"` |
| Register image | `POST /api/files/images` with `origin: "storage"` |
| Poll status | `GET /api/files/{rag_file_id}` |
| Re-key after rename/move | `PUT /api/files/{rag_file_id}/object` |
| Remove vectors | `DELETE /api/files/{rag_file_id}` |
| Photo search | `GET /api/chats/image-search?q=…` |

### 5.3 Target selection

`_registration_target(record)` decides key, type and hash before anything is queued:

- `FILES` + `.pdf` / `.docx` / `.xlsx` → the file itself, hash from `checksum_sha256`.
- `FILES` + `image/jpeg|png|webp|gif` and `size_bytes ≤ 10 MiB` → the file itself.
- `FILES` + HEIC/HEIF, or an image over 10 MiB → `SKIPPED unsupported_type` (no preview is generated outside Photos).
- `PHOTOS` + `image/jpeg|png|webp|gif` and `size_bytes ≤ 10 MiB` → the original.
- `PHOTOS` + HEIC/HEIF, or an image over 10 MiB → `photos/previews/{id}_thumb.jpg`; preview not written yet → `PENDING` with backoff, then `SKIPPED preview_missing`. Hash is computed from the preview bytes at registration time.
- Everything else → `SKIPPED unsupported_type`.

## 6. API additions

| Method | Path | Auth | Body / query | Success |
|---|---|---|---|---|
| `GET` | `/api/photos/search` | authenticated | `q` (1–4000 after trim), `limit` (1–50, default 20) | `{ "items": [ { "id", "file_name", "preview_url", "original_url", "score" } ], "query", "total" }` |
| `GET` | `/api/indexing/status` | authenticated | — | `{ "indexed", "pending", "failed", "skipped", "total" }` for the caller |
| `GET` | `/api/admin/indexing` | ADMIN | — | `{ "by_state": { … }, "oldest_pending_seconds", "last_errors": [ { "code", "count" } ], "rag_reachable": bool }` |
| `POST` | `/api/admin/indexing/backfill` | ADMIN | `{ "limit"?: int }` | `{ "enqueued": int, "remaining": int }` |
| `POST` | `/api/admin/indexing/retry` | ADMIN | `{ "state"?: "FAILED" }` | `{ "requeued": int }` |

New error codes for the `ErrorCode` enum:

| Code | HTTP | Meaning |
|---|---|---|
| `INDEXING_UNAVAILABLE` | 503 | RAG answered `502/503/504` or timed out during `GET /api/photos/search` |
| `SEARCH_QUERY_INVALID` | 400 | `q` empty after trim or longer than 4000 |

`GET /api/photos/search` is a server-side proxy: the SPA calls this service, this service calls RAG with the caller's UUID. The browser never talks to `rag.filenkov.store`, so no CORS, no second cookie, no second origin.

Results are filtered to the caller's **Photos**: a hit is returned only when its `rag_file_id` has a link row with `section = PHOTOS` and `state = INDEXED`. Hits that belong to the Files section are dropped. `preview_url` and `original_url` are this service's `/api/photos/{id}/…` paths; RAG's `content_path` is ignored.

## 7. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RAG_ENABLED` | `false` | Master switch. Off: no rows are created, no calls leave the app, `/api/photos/search` answers `503 INDEXING_UNAVAILABLE`. |
| `RAG_BASE_URL` | `http://rag-edge:8080` | RAG edge, no trailing slash. |
| `RAG_TIMEOUT_SECONDS` | `10` | Per call. |
| `RAG_SYNC_INTERVAL_SECONDS` | `60` | `index_sync` interval. |
| `RAG_INDEX_BATCH_SIZE` | `20` | Rows per tick. |
| `RAG_MAX_ATTEMPTS` | `8` | Failed attempts before `FAILED`. |
| `RAG_INDEX_MAX_WAIT_SECONDS` | `3600` | How long a `SUBMITTED` row may wait for `ready`. |
| `RAG_MAX_IMAGE_BYTES` | `10485760` | Mirrors RAG's `EMBEDDING_MAX_IMAGE_BYTES`. Above it, the preview is used (Photos) or the file is skipped (Files). |
| `RAG_BACKFILL_ENABLED` | `false` | Run the backfill once at startup. |

Compose: `app` joins `rag_edge` as `external: true`. No port is published; nothing else changes. `.env.example` gains the same block.

## 8. User stories

### US-SIDX-01 — Register every readable file at upload

**As** a user, **I upload a PDF, a spreadsheet or a photo**, and it appears in semantic search without a second step.

**Acceptance**

- `file_records` commit and its `file_index_links` row land in one transaction; a failed link insert does not leave a committed file without a link. No RAG call is made on the request path.
- `upload_file`, `upload_zip_folder` and `upload_photo` each produce one `PENDING` row per committed record, with `s3_key`, `content_sha256`, `content_type` and `user_id` filled by `_registration_target`.
- `.doc`, `.xls`, `.txt`, `.zip`, `.mp4`, `.yaml` produce `SKIPPED unsupported_type` and never reach RAG.
- A photo uploaded as HEIC is queued against its preview; a preview that never appears ends as `SKIPPED preview_missing`, not as an endless retry.
- The upload response body and status code do not change. A 100 MB upload takes the same time with RAG down as with RAG up.
- `PRIVATE` never creates a row (repository guard), and no counter in `user_quota_usage` moves.

**Tests:** upload PDF/DOCX/XLSX/JPG/HEIC; assert row state and target key; assert zero HTTP calls on the request path; assert `PRIVATE` writes nothing; assert quota unchanged.

---

### US-SIDX-02 — Sync job talks to RAG and survives its absence

**As** an operator, **I can restart RAG without losing the backlog** and without a manual replay.

**Acceptance**

- `index_sync` runs on the interval, single-flight, batches `RAG_INDEX_BATCH_SIZE`, and never overlaps itself.
- Text files go to `POST /api/files/documents`, images to `POST /api/files/images`, both with `origin: "storage"` and `X-Auth-User-Id` = the file owner. No cookie, no `X-Auth-Role`.
- `202` → `SUBMITTED` + `rag_file_id`. `409 duplicate_file` with our own `details.file_id` → `SUBMITTED` (idempotent replay, not an error). `409` pointing at another file → `FAILED duplicate_conflict`.
- `400`, `404`, `415` → `FAILED` immediately, RAG's code in `last_error_code`, no retry loop.
- `503`, `504`, connection error, timeout → `attempts += 1` and exponential backoff; after `RAG_MAX_ATTEMPTS` the row is `FAILED` and stays visible in `/api/indexing/status`.
- `SUBMITTED` rows are polled: `ready` → `INDEXED`, `error` → `FAILED` with the RAG code, `deleting`/`deleted`/`404` → `REMOVED`, still `uploaded`/`processing` after `RAG_INDEX_MAX_WAIT_SECONDS` → `FAILED timeout`.
- Restarting the app or RAG resumes from the table alone: no in-memory queue, no message broker, no duplicate registrations.
- A tick that ends in an exception does not kill the scheduler; the next tick continues.

**Tests:** fake RAG via `httpx.MockTransport` for 202/409/415/503/timeout; clock-driven backoff; restart idempotency; poll transitions.

---

### US-SIDX-03 — Deleting a file frees its vectors and nothing else

**As** a user, **I delete a file** and it stops answering in search, while the rest of the library is untouched.

**Acceptance**

- `delete_file`, `delete_by_path`, `delete_directory_recursive` and `delete_photo` move the link to `REMOVED` and the row survives the `file_records` delete (`file_record_id` becomes `NULL`).
- The job sends `DELETE /api/files/{rag_file_id}`; `202`, `409 file_busy` and `404` are all treated as done.
- No MinIO delete is issued by this service for indexing purposes, and none is issued by RAG for `origin=storage`: the object is removed by storage's own delete path exactly once, as today.
- A file deleted while its registration is still `PENDING` never reaches RAG: the row goes to `REMOVED` and no manifest is sent.
- A directory delete of 500 files produces 500 `REMOVED` rows processed across ticks, not one transaction that blocks the request.
- `GET /api/indexing/status` reflects the removal after the tick.

**Tests:** single delete, recursive delete, delete-before-registration, delete-during-`processing`, RAG down during removal, idempotent repeat.

---

### US-SIDX-04 — Rename and move keep the file searchable

**As** a user, **I rename or move a document** and search still finds it under the new name, with no gap.

**Acceptance**

- Renaming an `INDEXED` file calls `PUT /api/files/{rag_file_id}/object` with the new `s3_key`, the unchanged `content_sha256` and the unchanged `file_size`.
- The same `rag_file_id` is kept; no delete is sent; there is no interval in which the file is absent from the index.
- Renaming a directory re-keys every indexed descendant.
- Renaming a file that is still `PENDING` only rewrites `s3_key`; nothing is sent twice.
- A `409 content_changed` from RAG is impossible for a rename by construction, but if it happens the row goes to `REINDEX_PENDING` and is re-registered with a new `rag_file_id`.
- Overwriting content (`overwrite_file`) goes to `REINDEX_PENDING`: delete, then register again. The user sees the file continuously in the listing; only the index is rebuilt.
- `404` from re-key (object missing at the new key) → `FAILED object_not_found`, visible in admin stats, no silent retry storm.

**Tests:** rename file, move file, rename directory, rename of `PENDING` row, re-key idempotency, overwrite content.

---

### US-SIDX-05 — Backfill the library that already exists

**As** a user with 4000 old files, **I get them indexed** without re-uploading.

**Acceptance**

- `POST /api/admin/indexing/backfill` enqueues `PENDING` rows for committed `FILES` and `PHOTOS` records with no active link, in `created_at` keyset order, limited by `limit` (default `RAG_INDEX_BATCH_SIZE × 10`).
- The call returns `{enqueued, remaining}` and can be repeated until `remaining` is `0`.
- `RAG_BACKFILL_ENABLED=true` runs the same pass once at startup.
- Unsupported records are inserted as `SKIPPED` so they are not re-scanned on the next run.
- Archived records are `SKIPPED archived`; backfill never resurrects an archived file.
- Running backfill twice creates no duplicate link rows and no duplicate RAG registrations.
- The job keeps up: backfill and normal sync share the same queue and the same batch limit.

**Tests:** empty library, mixed library with unsupported and archived records, repeated backfill, progress counts.

---

### US-SIDX-06 — The archive does not leave dead vectors

**As** an operator, **I run the daily archive** and search never returns a file whose bytes are gone.

**Acceptance**

- After `ArchiveService._archive_record` succeeds, the link goes to `ARCHIVED` and the job removes the vectors.
- A `PHOTOS` record archived this way is removed from `images` as well.
- No manifest is ever sent for a `…zst` key.
- An archived file is not re-indexed by backfill or by any later sync pass.
- `GET /api/admin/indexing` counts `ARCHIVED` separately from `REMOVED`.

**Tests:** archive a `FILES` PDF and a `PHOTOS` image; assert link state, RAG delete call, no re-index.

---

### US-SIDX-07 — Find a photo by describing it

**As** a user on **Photos**, **I type “sunset over the sea”** and see the matching photos from my library.

**Acceptance**

- A search row sits above the photo grid: one input, placeholder `Describe a photo…`, submit on Enter, a clear control.
- `GET /api/photos/search?q=…&limit=…` validates `q` (1–4000 after trim, else `400 SEARCH_QUERY_INVALID`), calls RAG `GET /api/chats/image-search` with the caller's `X-Auth-User-Id`, and filters hits to the caller's `PHOTOS` links in `INDEXED`.
- Response items expose `id`, `file_name`, `preview_url`, `original_url`, `score`, newest first by score, and never expose `s3_key` or RAG's `content_path`.
- Grid shows the results; a tile opens the existing lightbox; `preview_url` is used for the tile and `original_url` for the lightbox, as today.
- Empty result shows `No photos match.` and keeps the query in the input.
- RAG unreachable, `502`, `503` or `504` → `503 INDEXING_UNAVAILABLE`; the page shows `Search is unavailable right now.` and the normal grid still works.
- Clearing the query restores the ordinary paginated `/api/photos` grid without a reload.
- One user's query never returns another user's photos, verified with two accounts and similar images.

**Tests:** validation matrix, mapping to photo ids, filtering of Files-section hits, RAG outage, empty result, clear restores pagination.

---

### US-SIDX-08 — Operators can see and repair the queue

**As** an admin, **I see what is indexed and retry what failed** without reading the database.

**Acceptance**

- `GET /api/indexing/status` returns the caller's counts by state; it does not list other users' rows.
- `GET /api/admin/indexing` returns counts by state, the age of the oldest `PENDING`, the top `last_error_code` values and whether RAG answered the last health probe.
- `POST /api/admin/indexing/retry` moves `FAILED` rows back to `PENDING` and resets `attempts`, limited to one batch per call.
- `RAG_ENABLED=false` means zero outbound calls, `SKIPPED`-less behaviour unchanged for uploads, and `503` on photo search.
- Every state change is logged once with `user_id`, `file_id`, `rag_file_id`, `state`, `error_code` — never a file name's content, never bytes.

**Tests:** role gate on admin routes, retry resets counters, counts match the table, `RAG_ENABLED=false` issues no HTTP calls.

## 9. Cross-cutting

| Concern | Rule |
|---|---|
| Upload latency | unchanged: one extra row insert, no network |
| Failure isolation | RAG down degrades search only; uploads, downloads, rename, delete and quota are unaffected |
| Quota | untouched; indexing is not billed |
| Logging | one structured line per state transition; no bytes, no document text, no passphrase |
| Idempotency | every call is safe to repeat; the table is the only queue |
| Ordering | `file_record_id` FK is `ON DELETE SET NULL`, so removal survives the record |
| Privacy | `PRIVATE` refused at the repository boundary, not only in the service |

## 10. Out of scope

- Indexing `Shared`, `Resumes` (`meta.yaml`, `statuses.yaml`, attachments) and `Generated` runs.
- Chat or answers over storage files inside the storage UI. Search returns photos only; documents are not question-answerable from here yet.
- A second origin in the browser, CORS on RAG, or a public link from storage into the RAG chat UI.
- Legacy `.doc`/`.xls`, PowerPoint, CSV, Markdown, plain text.
- HEIC/HEIF originals: they are embedded through the JPEG preview, never decoded by RAG.
- OCR: scanned PDFs keep going through RAG's own OCR stage.
- Re-embedding the whole library after a model or chunking change; that stays a RAG operation with a collection rebuild.
- Deleting storage bytes from the index side, or any write by RAG under `users/`, `shared/`, `_meta/`.

## 11. Definition of done

- [ ] Alembic `008_add_file_index_links` with the table, indexes and `ON DELETE SET NULL`.
- [ ] `file_index_links` repository with a hard refusal for `FileSection.PRIVATE`.
- [ ] Enqueue hooks on `upload_file`, `upload_zip_folder`, `overwrite_file`, `upload_photo`, `rename`, `rename_by_path`, `delete_file`, `delete_by_path`, `delete_directory_recursive`, `delete_photo`, `ArchiveService._archive_record`.
- [ ] `RagIndexClient` (httpx, timeout, error mapping) covering register, poll, re-key and delete.
- [ ] `index_sync` APScheduler job with backoff, batch limit and single-flight.
- [ ] Backfill endpoint and startup pass.
- [ ] `GET /api/photos/search`, `/api/indexing/status`, admin stats, admin retry, admin backfill.
- [ ] `INDEXING_UNAVAILABLE` and `SEARCH_QUERY_INVALID` in `ErrorCode`.
- [ ] Photos page search row with loading, empty, error and clear states.
- [ ] Compose `rag_edge` membership and the `RAG_*` env block in `.env.example` and README.
- [ ] Tests from every story above, including the fake-RAG transport suite and the two-account isolation test.
- [ ] API Contract, Data Models, Flow Spec, Design Spec, FRONTEND_DOCS, BACKEND_DOCS, epics index updated; ADR for the shared bucket.
- [ ] RAG-Chat US-11, US-12, US-13 delivered (this epic depends on all three).

## 12. Dependency and operator order

```text
RAG US-12 (bucket merge, origin=storage)   ─┐
RAG US-11 (DOCX/XLSX in documents)         ─┤
RAG US-13 (re-key)                         ─┘
                                             ↓
SIDX-01 → SIDX-02 → SIDX-03 → SIDX-04 → SIDX-05 → SIDX-06 → SIDX-07 → SIDX-08
```

1. Merge the buckets: `S3_BUCKET=storage` for RAG's five processes, keys under `rag/`, storage untouched. Verify `GET /api/files/{id}/content` on an old RAG file and on nothing else.
2. Deploy RAG US-11 and US-13.
3. Attach `app` to `rag_edge`, set `RAG_ENABLED=true`, restart storage.
4. Upload one PDF, one DOCX, one XLSX and one photo; check `/api/admin/indexing` reaches `INDEXED` for each.
5. Run `POST /api/admin/indexing/backfill` until `remaining` is `0`.
6. Search a photo from `/photos`.
