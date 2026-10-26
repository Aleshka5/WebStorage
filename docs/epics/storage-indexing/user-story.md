# User Story: Index Files and Photos into RAG

- **Epic:** [E-SIDX — Storage → RAG indexing](./init.md)
- **Branch:** `feature/storage-indexing`
- **Status:** Implemented on `feature/storage-indexing` (not merged). See [Implementation status](#implementation-status).
- **Language:** English, matching the epic

## Actor

A signed-in person who keeps PDFs, Office documents, and photos in their own **Files** and **Photos** sections, and an operator who needs the queue to drain while RAG is down.

## Goal

Every readable file in **Files** and **Photos** is registered in the existing RAG vector index, stays in sync on rename, move, overwrite, delete, and archive, and the Photos page can find a photo from a short description.

## Value

The library becomes searchable by meaning without a second upload and without sending the private vault, shared tree, resumes, or generated images to RAG.

## Story

**As** a signed-in user, **I want** Files and Photos to be copied into RAG’s existing `documents` and `images` collections and kept in sync, **so that** I can find a photo by describing it and an operator can repair the queue without re-uploading the library.

The eight slices in the epic (US-SIDX-01 … US-SIDX-08) are acceptance groups of this one story. They are not extra product scope.

## Scope IN

- One new table `file_index_links` (Alembic revision `008_add_file_index_links`, `down_revision = 007_add_generated_file_section`). No new column on `file_records`.
- Enqueue on the existing commit path for `FILES` and `PHOTOS` only: `FileService.upload_file`, `upload_zip_folder`, `overwrite_file`, `rename`, `rename_by_path`, `delete_file`, `delete_by_path`, `delete_directory_recursive`, `PhotoService.upload_photo`, `delete_photo`, `ArchiveService._archive_record`.
- An APScheduler job `index_sync` inside the existing `app` process. No new container, no broker, no in-memory queue.
- RAG HTTP client for register, poll, re-key, delete, image search, and `GET /health`.
- `GET /api/photos/search`, `GET /api/indexing/status`, `GET /api/admin/indexing`, `POST /api/admin/indexing/backfill`, `POST /api/admin/indexing/retry`.
- Error codes `INDEXING_UNAVAILABLE` (503) and `SEARCH_QUERY_INVALID` (400), using the current `{"detail": {"error_code": ...}}` envelope.
- Photos page: one search row above the existing grid (placeholder `Describe a photo…`, Enter submits, a clear control, loading, empty, and unavailable states).
- `RagSettings` via `get_settings()`, the `RAG_*` block in `.env.example` and README, and Compose network `rag_edge` (`external: true`) on `app` only.
- ADR for the shared MinIO bucket, plus the spec updates listed in the epic Definition of Done.
- Tests for every acceptance group below, including `httpx.MockTransport` and a two-account search isolation test.

## Scope OUT

- Indexing `SHARED`, `RESUMES`, `GENERATED`, or `PRIVATE`.
- Chat or answers over storage files inside this UI. Search returns photos only.
- A browser call to RAG, CORS, a second cookie, or a link into the RAG chat UI.
- Legacy `.doc` / `.xls`, PowerPoint, CSV, Markdown, plain text.
- Decoding HEIC/HEIF in this service. Photos use the JPEG preview that Photos already writes. Files-section HEIC/HEIF and Files-section images over the size cap are `SKIPPED` (epic D9). The integration note that says Files HEIC uses a preview does not apply here: Files has no preview.
- OCR, collection rebuilds, new Qdrant collections, or a new vector dimension.
- Quota changes, `meta.yaml` sidecars, or new `file_records` columns.
- Any MinIO write or delete performed for indexing. Storage’s existing delete and archive paths remain the only deleters of `users/…` bytes.
- Writing under `rag/`. This epic also does not change the existing `shared/` and `_meta/backups/` writers.
- A new admin screen. Operator actions are the admin JSON API.
- New Python dependencies. `httpx`, APScheduler, loguru, and pydantic-settings are already in `pyproject.toml`.
- Restoring or editing `ТЗ_v1.1_Сетевое_Хранилище.md` (it is deleted in the working tree on purpose).

## Acceptance criteria

### Register on upload (US-SIDX-01)

- The `file_records` commit and the `file_index_links` insert share the request session. Routers keep owning `commit`. A failed link insert rolls the file back with the request. The request path makes no RAG call.
- `upload_file`, `upload_zip_folder`, and `upload_photo` create one row per committed record. `s3_key`, `content_sha256`, `content_type`, `user_id`, and `section` come from the registration decision.
- `.doc`, `.xls`, `.txt`, `.zip`, `.mp4`, and `.yaml` are inserted as `SKIPPED` / `unsupported_type` and are never sent.
- A HEIC/HEIF photo, or a Photos image larger than `RAG_MAX_IMAGE_BYTES`, is queued against `users/{user_id}/photos/previews/{id}_thumb.jpg`. If that object never appears, the row becomes `SKIPPED` / `preview_missing`.
- Upload status and response body stay as they are today. Upload duration does not depend on RAG.
- `PRIVATE` never inserts a row. The repository raises before insert when `section == PRIVATE`. `user_quota_usage` does not change because of indexing.
- `SHARED`, `RESUMES`, and `GENERATED` do not gain a link repository. Their current `FileService(...)` call sites stay valid.

### Sync job (US-SIDX-02)

- `index_sync` runs every `RAG_SYNC_INTERVAL_SECONDS` (default 60), `max_instances=1`, `coalesce=True`, plus an in-process lock so a slow tick cannot overlap the next one.
- One tick takes at most `RAG_INDEX_BATCH_SIZE` due rows across `PENDING`, `SUBMITTED`, `REMOVED`, `ARCHIVED`, and `REINDEX_PENDING` (`next_attempt_at` is null or `<= now`).
- Text goes to `POST /api/files/documents`, images to `POST /api/files/images`. Both send `origin: "storage"` and `X-Auth-User-Id` of the file owner. No cookie and no `X-Auth-Role`.
- `202` stores `rag_file_id` and sets `SUBMITTED`.
- `409 duplicate_file`: if this row’s `rag_file_id` is null or equals `details.file_id`, store that id and set `SUBMITTED`. If it already stores a different id, set `FAILED` / `duplicate_conflict`.
- `400`, `404`, and `415` set `FAILED` immediately with RAG’s code in `last_error_code`.
- `503`, `504`, timeout, and connection errors increment `attempts` and set `next_attempt_at` to `min(3600, 30 * 2^attempts)` seconds ahead. At `RAG_MAX_ATTEMPTS` (8) the row becomes `FAILED`.
- Poll `GET /api/files/{rag_file_id}`: `ready` → `INDEXED`; `error` → `FAILED` with that code; `deleting`, `deleted`, or `404` → `REMOVED`; `uploaded` or `processing` stay `SUBMITTED`.
- A `SUBMITTED` row still not `ready` after `RAG_INDEX_MAX_WAIT_SECONDS` (3600) becomes `FAILED` / `timeout`. The clock is `updated_at` of the transition into `SUBMITTED`. A no-progress poll updates `next_attempt_at` only.
- Restart uses the table alone. A tick that raises is logged with `logger.exception` and does not stop the scheduler.

### Delete (US-SIDX-03)

- File, path, recursive directory, and photo delete set the link to `REMOVED` in the same database transaction as the file delete. `ON DELETE SET NULL` clears `file_record_id`. `user_id` and `rag_file_id` remain.
- The job calls `DELETE /api/files/{rag_file_id}`. `202`, `409 file_busy`, and `404` all mean the vectors are gone.
- A row deleted while still `PENDING` and without `rag_file_id` never receives a manifest.
- Recursive delete does not call RAG on the request. The job drains the `REMOVED` rows across ticks.
- Indexing code does not delete MinIO objects.

### Rename, move, overwrite (US-SIDX-04)

- Rename or move of an `INDEXED` row (and of an already registered row) sets `PENDING`, keeps `rag_file_id`, writes the new `s3_key`, and sets `next_attempt_at` to now. The job sends `PUT /api/files/{rag_file_id}/object` with the new key, the same `content_sha256`, and the same `file_size`. It does not `DELETE`.
- Directory rename or move does the same for every descendant that has a link row.
- A `PENDING` row that has no `rag_file_id` only updates `s3_key`.
- `409 content_changed` on re-key sets `REINDEX_PENDING`.
- `404 object_not_found` on re-key sets `FAILED` / `object_not_found` with no retry storm.
- `overwrite_file` on a row with `rag_file_id` sets `REINDEX_PENDING`. The job deletes the old vectors, then registers again with a new `rag_file_id`. If `rag_file_id` is still null, refresh `s3_key` and hash and stay `PENDING`.
- The file listing stays available through the rebuild. Only the index is rebuilt.

### Backfill (US-SIDX-05)

- `POST /api/admin/indexing/backfill` inserts rows for `COMMITTED` `FILES` and `PHOTOS` records that have no link row, in `(created_at, id)` keyset order.
- Default `limit` is `RAG_INDEX_BATCH_SIZE * 10`. Response is `{ "enqueued": int, "remaining": int }`.
- Unsupported records are inserted as `SKIPPED` / `unsupported_type`. Archived records are inserted as `SKIPPED` / `archived`. Neither is sent to RAG, and the next backfill does not select them again.
- A second backfill does not insert a second row for the same `file_record_id` and does not register twice.
- `RAG_BACKFILL_ENABLED=true` runs that same single pass once at startup. It does not loop until `remaining` is 0.
- Backfill and `index_sync` share the table and the batch size.

### Archive (US-SIDX-06)

- After `_archive_record` returns success, the link becomes `ARCHIVED`. The job deletes the vectors and does not register the `.zst` key.
- A Photos image is removed from `images` the same way.
- Backfill and later ticks do not register an archived file again.
- Admin counts keep `ARCHIVED` separate from `REMOVED`.
- A missing link row (private, shared, resumes, generated) is not created during archive.

### Photo search (US-SIDX-07)

- `GET /api/photos/search?q=&limit=` trims `q`. Empty or longer than 4000 characters is `400 SEARCH_QUERY_INVALID`. `limit` is 1–50, default 20.
- The server calls `GET /api/chats/image-search` with the caller’s `X-Auth-User-Id`, the same `q`, and the same `limit` (`offset` 0). Hits whose `file_id` is not an `INDEXED` link with `section = PHOTOS` for that caller are dropped.
- Each item is `{ "id", "file_name", "preview_url", "original_url", "score" }`. `id` is the photo `file_records.id`. `file_name` is `original_name`. URLs are `/api/photos/{id}/preview` and `/api/photos/{id}/original`. `s3_key`, `s3_url`, and `content_path` are not returned. Order is score descending. `total` is the number of items after the filter. `query` is the trimmed query.
- The grid shows those results. A tile opens the existing lightbox; the tile uses `preview_url` and the lightbox uses `original_url`.
- Empty body shows `No photos match.` and leaves the query in the input.
- RAG down, `502`, `503`, or `504` is `503 INDEXING_UNAVAILABLE`. The page shows `Search is unavailable right now.` The ordinary grid still loads.
- Clearing the query loads `/api/photos` pagination again without a full page reload.
- Two accounts with similar images never see each other’s photos.
- `RAG_ENABLED=false` returns `503` and performs no HTTP call.

### Operator queue (US-SIDX-08)

- `GET /api/indexing/status` returns `{ "indexed", "pending", "failed", "skipped", "total" }` for the caller only.
- `GET /api/admin/indexing` is `ADMIN` only. It returns counts by state (including `ARCHIVED` separate from `REMOVED`), `oldest_pending_seconds`, `last_errors` as `{ "code", "count" }[]`, and `rag_reachable`.
- `rag_reachable` is a live `GET {RAG_BASE_URL}/health` with `RAG_TIMEOUT_SECONDS`. When RAG is disabled it is `false` and no call is made.
- `POST /api/admin/indexing/retry` accepts a missing `state` or `"FAILED"` only. It moves at most `RAG_INDEX_BATCH_SIZE` `FAILED` rows to `PENDING`, resets `attempts`, and clears `next_attempt_at`. Response is `{ "requeued": int }`.
- `RAG_ENABLED=false` creates no rows and makes no outbound calls. Upload behaviour otherwise stays as today.
- Every state change logs one line with `user_id`, `file_record_id`, `rag_file_id`, `state`, and `error_code`. Logs do not contain file-name text, document bytes, or a passphrase.

## Decisions

These close gaps in the epic. Implement them as written.

1. **RAG I/O stays in the job.** Upload, rename, delete, and archive only change `file_index_links` on the current session. Re-key is `PENDING` plus the existing `rag_file_id`, then `PUT` inside `index_sync`. That keeps upload latency unchanged (D5) and avoids a search gap (D7): the job never deletes on a pure rename.
2. **`409 duplicate_file` adopts `details.file_id` when this row has no id yet.** A different stored id is `duplicate_conflict`. This is the crash-after-202 replay. A conflict with another stored id is a failure.
3. **`updated_at` moves on state or payload changes only.** Wait timeout for `SUBMITTED` uses that timestamp. The listed columns are enough; do not add `submitted_at`.
4. **A missing preview uses the same attempt counter and backoff, then `SKIPPED` / `preview_missing`.** It is not `FAILED` and it does not retry forever.
5. **`content_type` comes from the extension allowlist** in the domain decision, not from `mimetypes.guess_type`. Values are `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `image/jpeg`, `image/png`, `image/webp`, `image/gif`.
6. **`s3_key` is the object key already stored in `file_records.relative_path`** (`users/{user_id}/files/…` or `users/{user_id}/photos/originals/…`). Do not prefix the bucket name. Preview key is `users/{user_id}/photos/previews/{id}_thumb.jpg`. For a preview, `content_sha256` and `file_size` are taken from the preview bytes at registration time.
7. **`file_record_id` is unique where it is not null** (PostgreSQL unique index; many nulls allowed). The epic’s “one row” and “backfill twice creates no duplicate” require it. `rag_file_id` is unique where set.
8. **One batch per tick, oldest due row first,** across all five active states. Not 20 per state.
9. **Directory delete may update hundreds of link rows in the file-delete transaction.** It must not call RAG there.
10. **`RAG_ENABLED=false` registers neither `index_sync` nor the startup backfill.** Enqueue collaborators no-op. Search returns 503.
11. **Startup backfill is one pass,** the same function as the admin endpoint, not a drain loop.
12. **New HTTP routes live in a new router module.** Do not add them to `photo_router.py` or `admin_router.py`. Photo search is still the path `/api/photos/search`. Admin routes use `check_role(Role.ADMIN)`.
13. **Link collaboration is an optional constructor argument,** default `None`, on `FileService`, `PhotoService`, and `ArchiveService`. `None` means today’s behaviour. Inject it only from the Files DI, the Photos DI, the archive DI, and the scheduler factory in `main.py`. Do not edit `shared.py`, `resumes.py`, `generated.py`, or `private_service.py`.
14. **`GET /health` on `RAG_BASE_URL`** is the reachability probe (RAG edge already returns `{"status":"ok"}`).
15. **Forward inbound `X-Request-Id` when the request has one.** The job may send a fresh UUID per outbound call and log that id. Do not log the manifest body.

## State machine

```text
upload/commit ─► PENDING ─► SUBMITTED ─► INDEXED
                    │            │
                    │            ├─► FAILED
                    │            └─► REMOVED
                    ├─► SKIPPED
                    └─► FAILED

INDEXED rename/move ─► PENDING (same rag_file_id, job sends PUT)
INDEXED overwrite  ─► REINDEX_PENDING ─► REMOVED ─► PENDING (new rag_file_id)
delete             ─► REMOVED
archive            ─► ARCHIVED
```

`FAILED` returns to `PENDING` only through admin retry. `SKIPPED`, `REMOVED`, and `ARCHIVED` are terminal except that `REMOVED` and `ARCHIVED` still run `DELETE` until RAG confirms.

## Constraints

- Clean Architecture. Domain decides the target and holds states and indexing exceptions. Application services enqueue, sync, backfill, and search. Infrastructure is the SQLAlchemy repository, the httpx client, and MinIO reads for preview bytes. Presentation is routers, schemas, and DI. Domain does not import FastAPI, SQLAlchemy, httpx, or `get_settings()`.
- SOLID. `RagIndexClient` is behind a port. Existing services take an optional link collaborator; they do not grow a second HTTP stack.
- Logging is loguru. `INFO` for a state transition and for job start/finish. `WARNING` for an expected skip (unsupported type, missing preview, empty search). `ERROR` when the cause is already known (bad RAG code, exhausted attempts) and a traceback adds nothing. `logger.exception` only inside `except`. No file contents, names-as-content, or secrets.
- Settings only through `RagSettings` on `Settings` and `get_settings()` (`@lru_cache`). No `os.getenv`. New variables ship in `.env.example` in the same change as the settings class.
- Package manager is `uv`. Do not add a frontend test runner. Frontend check is `pnpm exec tsc --noEmit`.
- Backward compatible HTTP for every existing route. Optional constructor arguments so current call sites and tests keep compiling.
- Indexing is not billed. Do not call the quota repository from the new code.
- `PRIVATE` is refused in the repository, not only in the service.

### Configuration

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

Access: `get_settings().rag.enabled`, `.base_url`, and the matching fields. Strip one trailing slash from the base URL.

## Affected modules

Existing code the story touches, and the new modules agents must add. Names below are the contract; do not invent parallel types.

### Already in the tree

| Module | Role |
|---|---|
| `backend/config.py` | Add `RagSettings` and `settings.rag` only |
| `backend/app/infrastructure/database/models.py` | Append `FileIndexLink` only |
| `backend/alembic/versions/007_add_generated_file_section.py` | Parent revision; do not edit |
| `backend/app/domain/entities/file_record.py` | Read-only. `FileSection` already has `FILES`, `PHOTOS`, `PRIVATE`, `SHARED`, `RESUMES`, `GENERATED` |
| `backend/app/application/file_service.py` | Optional link collaborator and enqueue calls |
| `backend/app/application/photo_service.py` | Same. Previews already land at `previews/{id}_thumb.jpg` under `users/{user_id}/photos` |
| `backend/app/application/archive_service.py` | After a successful `_archive_record`, mark `ARCHIVED` |
| `backend/app/presentation/dependencies/files.py` | Inject the collaborator for the Files service only |
| `backend/app/presentation/dependencies/photos.py` | Inject it for Photos only |
| `backend/app/presentation/dependencies/archive.py` | Inject it for the archive service |
| `backend/main.py` | Register `index_sync`, run startup backfill, include the new router, pass the link repository into the scheduled `ArchiveService` |
| `backend/app/domain/value_objects/error_codes.py` | Two new codes |
| `backend/app/presentation/exception_handlers.py` | Map the two new domain exceptions |
| `backend/app/infrastructure/user_service/client.py` | Pattern only: httpx client, timeout from settings, injectable `httpx.AsyncClient` |
| `frontend/src/pages/PhotosPage.tsx` | Search row and mode switch |
| `frontend/src/services/photosApi.ts` | `searchPhotos` |
| `frontend/src/types/photos.ts` | Search response types |
| `docker-compose.yml` | `app` joins external `rag_edge` |
| `.env.example`, `README.md` | `RAG_*` block |
| `docs/API Contract.md`, `docs/Data Models.md`, `docs/Flow Spec.md`, `docs/Design Spec.md`, `docs/FRONTEND_DOCS.md`, `docs/BACKEND_DOCS.md`, `docs/adr/index.md`, `docs/epics/init.md` | Spec updates |

`FileService` is also constructed for shared, resumes, generated, and private. Those call sites must keep working with the new argument omitted.

### New modules

| Module | Owns |
|---|---|
| `backend/app/domain/indexing.py` | `IndexLinkState`, `RegistrationDecision`, `decide_registration_target`, `preview_object_key`, `PrivateIndexRefused`, `SearchQueryInvalid`, `IndexingUnavailable` |
| `backend/app/application/ports/rag_index.py` | Client port and result dataclasses |
| `backend/app/application/ports/index_links.py` | Repository port used by enqueue, sync, backfill, and search |
| `backend/app/infrastructure/database/repositories/file_index_link_repo.py` | Port implementation. Refuses `PRIVATE` |
| `backend/app/infrastructure/rag/client.py` | httpx `RagIndexClient` |
| `backend/app/application/indexing/__init__.py` | Empty package marker. One owner only |
| `backend/app/application/indexing/enqueue.py` | Request-path transitions |
| `backend/app/application/indexing/sync_job.py` | `run_index_sync` |
| `backend/app/application/indexing/backfill.py` | `backfill` and `run_startup_backfill` |
| `backend/app/application/indexing/search.py` | Photo search mapping |
| `backend/app/application/indexing/status.py` | User counts, admin stats, retry |
| `backend/app/presentation/routers/indexing_router.py` | All five new routes |
| `backend/app/presentation/schemas/indexing.py` | Request and response models |
| `backend/app/presentation/dependencies/indexing.py` | DI for the new services |
| `backend/alembic/versions/008_add_file_index_links.py` | Table, FK `ON DELETE SET NULL`, indexes |
| `frontend/src/components/PhotoGrid/PhotoSearchBar.tsx` | Search row |
| `docs/adr/ADR-011-shared-minio-bucket-with-rag.md` | Shared bucket ADR |

### Frozen repository operations

`FileIndexLinkRepository` implements `IndexLinkRepository`:

- `upsert_pending(record, decision)` — insert `PENDING` or `SKIPPED`; on the unique `file_record_id`, do not insert a second row.
- `mark_content_changed(file_record_id, decision)` — `REINDEX_PENDING` when `rag_file_id` is set, otherwise refresh the pending target.
- `mark_rekey(file_record_id, s3_key)` — keep `rag_file_id`, set `PENDING`, `next_attempt_at = now`; if no `rag_file_id`, update `s3_key` only.
- `mark_removed(file_record_ids)` and `mark_archived(file_record_id)` — no-op when no row exists.
- `claim_due(limit, now)` — the five active states.
- `apply_transition(...)` — the only writer of `state`, `attempts`, `next_attempt_at`, `rag_file_id`, `last_error_code`, and the one INFO log.
- `backfill_candidates(limit, after)` and `count_remaining(after_enqueued)`.
- `counts_for_user(user_id)`, `admin_snapshot()`, `retry_failed(limit)`.
- `indexed_photos_by_rag_file_id(user_id, rag_file_ids)`.

`decide_registration_target(record, *, max_image_bytes: int) -> RegistrationDecision` with `kind` of `document`, `image`, `preview`, or `skip`, plus `s3_key`, `content_type`, `content_sha256` (null when the hash must be read from the preview later), and `skip_code`.

## Open questions

None. The decisions above are binding for implementation.

## Implementation status

Implemented on branch `feature/storage-indexing`. Not merged. The indexing test suite passed: 305 tests.

The acceptance criteria above are unchanged. Where they disagree with the code, the code is what shipped, and the specs updated in this change describe the code.

`claim_due` takes `FOR UPDATE SKIP LOCKED` and holds those row locks until `main.py` commits the whole RAG batch. That is a known limitation, not a product feature.

Differences from the epic (`init.md`), which was not rewritten:

1. **Admin retry.** The epic and the acceptance text send every `FAILED` row to `PENDING`. The repository moves a row that already has `rag_file_id` to `SUBMITTED` so the job polls it, and leaves a row that never registered as `PENDING` with a null id. `attempts` is reset to 0 in both cases.
2. **Backfill response.** The epic and the acceptance text return `{ "enqueued", "remaining" }`. The route returns `{ examined, enqueued, skipped, next_cursor, remaining, status }`. When `RAG_ENABLED` is false the handler does not call `backfill` and returns `status: "skipped"` with zero counts.
3. **Delete and `file_record_id`.** The epic says a user delete clears `file_record_id` through `ON DELETE SET NULL`. User delete is a soft delete (`file_records.status = DELETED`). The row stays, so `file_record_id` stays set. `ON DELETE SET NULL` applies only when the `file_records` row is actually deleted.
4. **Content change.** The epic state diagram goes `REINDEX_PENDING → REMOVED → PENDING` and then a new `rag_file_id`. The job deletes the vectors, clears `rag_file_id`, and registers again in the same tick (`SUBMITTED`, new id). `mark_content_changed` resets `attempts` to 0. It does not persist an intermediate `REMOVED` row.
5. **Startup backfill.** The epic says `RAG_BACKFILL_ENABLED=true` runs the pass at startup. `main.py` calls `backfill(links)` only when `RAG_ENABLED` is true. `backfill` itself returns `skipped` and writes nothing when `RAG_BACKFILL_ENABLED` is false. When `RAG_ENABLED` is false the job is not registered and startup backfill is not called.
6. **Delete and archive HTTP errors.** The epic treats `202`, `409 file_busy`, and `404` as vectors gone. The code also keeps `REMOVED` or `ARCHIVED` on any other HTTP error, increments `attempts`, and sets `next_attempt_at`. The row becomes `FAILED` only after `RAG_MAX_ATTEMPTS`.
7. **Caller status `total`.** The response fields match the epic. `total` counts every link row for that user, so `SUBMITTED`, `REMOVED`, `ARCHIVED`, and `REINDEX_PENDING` are inside `total` and not in the four named counters. Admin `last_errors` is every distinct `last_error_code` with its count, not a truncated top list.
