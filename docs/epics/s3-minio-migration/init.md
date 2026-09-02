# Epic: Move from PC File System to S3 MinIO

> **ID:** E-S3MINIO  
> **Status:** Implemented (capacity fallback / HTTP e2e gaps documented)  
> **Specs:** [Design Spec §4](../../Design%20Spec.md), [ADR-001](../../adr/ADR-001-clean-architecture.md), [ADR-002](../../adr/ADR-002-storage-adapter-decorator.md), [ADR-005](../../adr/ADR-005-disk-router-most-free.md), [ADR-006](../../adr/ADR-006-metadata-db-blobs-fs.md), [ADR-007](../../adr/ADR-007-minio-blob-backend.md), TZ §4 / §9  
> **Supersedes:** ADR-006 blob placement; ADR-005 FS `statvfs` probes

## Overview

Local multi-disk filesystem blob storage is **removed**. Blobs live in **self-hosted MinIO** (S3 API). PostgreSQL metadata, Clean Architecture ports, and public HTTP/API contracts remain. Admin `DiskStat.mount_path` is replaced by `bucket`.

Application services (`FileService`, `PhotoService`, `PrivateService`, …) depend on `StorageAdapter`, not on `Path` / `aiofiles` / `statvfs`. There is no `STORAGE_BACKEND` switch.

## Goals

- Persist all blob bytes (files, photos, private ciphertext, archives, optional DB dump objects) in MinIO.
- Keep metadata, quotas, ACL, encryption, and API behavior unchanged for clients.
- Preserve multi-volume expandability semantics via MinIO buckets/prefixes (or multiple MinIO volumes), without migrating historical object keys when capacity is added.
- Make `EncryptedStorageAdapter` decorate **any** `StorageAdapter` (inner is `S3StorageAdapter`).
- Introduce an explicit ADR that supersedes ADR-006’s “blobs on filesystem” decision.
- Cut over to S3-only (FS adapter, env switch, and FS→S3 migrator removed).

## Non-goals (this epic)

- Replacing PostgreSQL with object metadata.
- Client-side encryption redesign.
- Multi-cloud / AWS S3 as primary target (MinIO is the supported self-hosted backend; generic S3 API is the port).
- Automatic rebalancing of existing objects across buckets (same sticky placement rule as today).

## User Stories

### US-S3-01 — ADR: object storage as blob backend
> **ADR:** [ADR-007 — MinIO (S3 API) as blob backend](../../adr/ADR-007-minio-blob-backend.md) (Accepted)

As an architect, I need an accepted ADR (e.g. ADR-007) that:
- chooses MinIO (S3 API) for blob bytes;
- keeps metadata in PostgreSQL;
- maps today’s `disk_id` + `relative_path` to bucket/prefix + object key;
- documents intentional TZ §4 / ADR-006 deviation;
- states consequences for backup, health, and capacity expansion.

### US-S3-02 — Settings & env for MinIO
As an operator, I configure MinIO via `get_settings()` only:
- endpoint, access/secret keys, region, secure flag, bucket prefix, path-style;
- no backend selector — S3 is always used;
- mirror all vars in `.env.example`.

### US-S3-03 — S3StorageAdapter implements StorageAdapter
As a backend developer, I have `S3StorageAdapter` in Infrastructure that implements `StorageAdapter`:
- `list` / `read` / `write` (stream + SHA-256) / `delete` / `mkdir` / `rename` / `exists`;
- path-traversal safety on logical keys;
- hides `.tmp` / dot-prefix objects from listings;
- no `Path`-based public API leak (`base_path: Path` removed or replaced by logical root key prefix).

### US-S3-04 — Decouple EncryptedStorageAdapter from FS
As a security owner, private vault works on MinIO:
- `EncryptedStorageAdapter` wraps any `StorageAdapter`;
- marker, encrypted names, and AES-GCM framing unchanged;
- no direct `aiofiles` / local temp dependency outside the inner adapter’s contract (or a shared temp/stream helper).

### US-S3-05 — Remove FS leaks from Application / DI
As a maintainer, application and DI no longer mkdir local trees or pass `Path` into services:
- photos originals/previews, shared, files, private sections create logical prefixes via adapter;
- `ArchiveManager` / archive download path / thumbnail pipeline / maintenance `.tmp` cleanup use adapter (or a narrow `BlobStore` port);
- `FileRecord.disk_id` + `relative_path` remain the locator contract.

### US-S3-06 — Capacity routing replaces DiskRouter FS probes
As an operator adding capacity, write placement still picks a healthy volume with enough free space:
- map `disk_id` → MinIO bucket (or dedicated prefix on a named volume);
- free-space / health from MinIO metrics (not `statvfs`);
- sticky placement: existing records keep their `disk_id`; no auto-rebalance;
- admin `GET /api/admin/storage/health` still returns `HEALTHY | LOW_SPACE | UNAVAILABLE`.

### US-S3-07 — Compose / ops: MinIO in the stack
As an operator, `docker-compose` (and docs) run MinIO + create buckets/policies needed by HomeCloud; README covers credentials, persistence volume for MinIO data, and expand-capacity steps.

### US-S3-08 — Migrate existing FS blobs to MinIO
Historical one-shot tool (`migrate_fs_to_s3.py` / `fs_s3_migrator`) walked the old FS tree into MinIO. **Removed** after the S3-only cutover (no FS backend, no data back-compat).

### US-S3-09 — Archives, backups, maintenance on object storage
As a platform owner:
- zstd archives are stored as objects; transparent download still works;
- DB dumps land at `_meta/backups/` in the first disk bucket;
- PENDING / orphan `.tmp` cleanup works against object keys;
- quota reconcile unchanged (metadata-driven).

### US-S3-10 — Regression coverage
As a QA owner:
- unit tests for `S3StorageAdapter` against MinIO (testcontainer or moto/minio in CI);
- round-trip upload/download/delete for files, private (encrypted), photos, archived file;
- health endpoint under LOW_SPACE simulation;
- no contract change for existing `/api/files|photos|private|shared|admin` clients.

## Architecture Consistency Review

### Compatible with current architecture

| Principle / ADR | Verdict | Notes |
|---|---|---|
| ADR-001 Clean Architecture | **Compatible** | New adapter + MinIO client stay in Infrastructure; Application keeps ports. |
| ADR-002 Decorator encryption | **Compatible if US-S3-04 done** | Decorator must wrap the ABC, not hard-depend on a FS adapter. |
| Metadata in PostgreSQL | **Compatible** | ADR-006 half that remains: DB for listings/quotas/ACL. |
| Sticky `disk_id` placement | **Compatible** | Map `disk_id` → bucket/volume; do not rebalance old keys. |
| Streaming upload/download + SHA-256 | **Compatible** | S3 multipart / ranged get must preserve streaming semantics. |
| Public API / FE FileManager | **Compatible** | No FE contract change expected. |
| Config via `get_settings()` | **Compatible** | Requires new settings group + `env.example` sync. |

### Conflicts / required design decisions

| Area | Current state | Conflict | Required direction |
|---|---|---|---|
| ADR-006 | Blobs on local FS; S3 rejected as “heavier for home Docker” | Direct contradiction | New ADR: accept MinIO for this target; mark ADR-006 superseded for blob placement (or “FS mode retained as optional backend”). |
| TZ §4 layout | Explicit `/storage/diskN/...` tree | Object keys ≠ directories | Keep **logical** key layout isomorphic to today’s relative paths; document physical MinIO layout in ADR. |
| ADR-005 / DiskRouter | `statvfs` / `df` on mounts | No local mounts for blobs | Replace probes with MinIO/volume capacity API; keep strategy hook `DISK_STRATEGY` / most-free semantics. |
| `StorageAdapter.base_path: Path` | Part of ABC | FS leak in port | Port is logical keys; `S3StorageAdapter` has no `base_path`. |
| DI helpers | `Path.mkdir` in photos/shared/files deps | Assumes local FS | Prefixes via adapter `mkdir`. |
| Archive / Thumbnail / Backup | Direct `Path` I/O in places | Bypasses port | Staging in `/tmp`; persist via adapter. |
| FAMILY/ADMIN quota = “free disk” | **Resolved (v1.2):** per-user `limit_bytes` (default 100 MB), not MinIO free space. DiskRouter probes remain for write routing / admin health only. |
| Encrypted adapter | Imports / assumes plain FS tmp patterns | Risk of FS-only private path | US-S3-04; crypto framing identical. |
| E-STORAGE epic | Init layout via `init_storage.py` on disks | FS-centric ops story | Replaced by `minio-init` bucket bootstrap. |

### Layering checklist (DoD gate)

- [x] Domain has no MinIO/boto/aiobotocore imports.
- [x] Application services do not import `pathlib.Path` for blob I/O (logical `PurePosixPath` for key joins only is OK).
- [x] Only Infrastructure talks to MinIO SDK.
- [x] `EncryptedStorageAdapter` composes `StorageAdapter`, not a concrete FS type.
- [x] ADR-007 (or successor) accepted before marking epic Implemented.
- [x] `.env.example` lists every new setting.
- [x] Existing epic DoDs (files/photos/private/shared/archive) hold on S3 (service/adapter regression under moto).

### Parallelization matrix

```text
Wave 1 (parallel)     Wave 2 (serial)     Wave 3 (limited parallel)     Wave 4          Wave 5
┌─────────────┐       ┌───────────┐       ┌─────────────┐               ┌─────────┐    ┌─────────┐
│ US-S3-01 ADR│       │ US-S3-03  │       │ US-S3-04    │──┐            │ US-S3-09│    │ US-S3-10│
│ US-S3-02 cfg│──────►│ Adapter + │──────►│ Encrypted   │  ├──merge────►│ Archive │───►│ E2E     │
│ US-S3-07 ops│       │ ABC harden│       │ US-S3-05 DI │──┤            │ US-S3-08│    │ tests   │
└─────────────┘       └───────────┘       │ US-S3-06 rt │──┘            │ Migrate │    └─────────┘
                                          └─────────────┘               └─────────┘
```

| Story | Parallel with | Blocked by | Why |
|---|---|---|---|
| US-S3-01 | 02, 07 | — | Docs-only ADR |
| US-S3-02 | 01, 07 | — | Settings / env only |
| US-S3-07 | 01, 02 | — | Compose/README; no Python adapter yet |
| US-S3-03 | — | 02 (settings shape) | Needs S3 settings; owns adapter + ABC |
| US-S3-04 | 05*, 06* | 03 | Needs working S3 adapter port |
| US-S3-05 | 04*, 06* | 03 | Touches DI; coordinate file ownership with 04/06 |
| US-S3-06 | 04*, 05* | 02, 03 | DiskRouter/health; overlaps DI wiring |
| US-S3-09 | 08 | 05, 06 | Needs blob I/O via adapter everywhere |
| US-S3-08 | 09 | 03, 07 | Migration tool needs stable key layout + MinIO up |
| US-S3-10 | — | 04–09 | Full regression last |

\* Wave 3 stories may run as **three agents with disjoint file ownership**, or as **one agent** if merge risk is high:

- **04 owns:** `encrypted_adapter.py` + private tests  
- **05 owns:** `presentation/dependencies/*`, photo/thumbnail Path leaks, ABC `base_path` call sites  
- **06 owns:** `disk_router.py`, admin health, capacity settings wiring  

**Do not parallelize** 03 with 04/05/06 (same port). **Do not** start 08/09 before 05. **Do not** start 10 before core paths work on S3.

### Suggested implementation order

1. Wave 1: US-S3-01 + US-S3-02 + US-S3-07 (parallel)  
2. Wave 2: US-S3-03 (adapter + ABC harden)  
3. Wave 3: US-S3-04 + US-S3-05 + US-S3-06 (disjoint ownership or single agent)  
4. Wave 4: US-S3-09 + US-S3-08 (parallel after Wave 3)  
5. Wave 5: US-S3-10

## Definition of Done

- [x] ADR for MinIO blob backend accepted; ADR-006/005 impact documented ([ADR-007](../../adr/ADR-007-minio-blob-backend.md)).
- [x] `S3StorageAdapter` + settings + compose MinIO service.
- [x] Files / Photos / Private / Shared / Archive / Backup work on S3 (adapter + service-level coverage; Shared via same `FileService` + section adapter path).
- [x] Admin storage health reflects MinIO capacity (`HEALTHY` / `LOW_SPACE` / `UNAVAILABLE` via DiskRouter; see known gaps for capacity probe).
- [x] S3-only cutover: FS adapter, `STORAGE_BACKEND` / `STORAGE_ROOT`, `init_storage.py`, and FS→MinIO migrator removed.
- [x] Automated adapter + regression round-trip tests in CI (`test_s3_*`, `test_us_s3_*`, especially US-S3-10).
- [x] Logging covers upload/delete/migrate with `user_id` / `file_id` / `disk_id` / object key (no credentials).
- [x] Epics index status updated when complete.

## Known gaps

- **DiskRouter capacity:** when MinIO Admin API is unreachable, free-space math uses a documented **1 TiB** total fallback (`free ≈ total − used`). Real multi-volume drive totals from Admin API are preferred when available.
- **No full HTTP ASGI e2e** in US-S3-10: coverage is moto-backed unit/integration at adapter + application service level.
- **Shared section** is not a dedicated US-S3-10 scenario; it reuses `FileService` + DI section adapters.
