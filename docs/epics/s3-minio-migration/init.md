# Epic: Move from PC File System to S3 MinIO

> **ID:** E-S3MINIO  
> **Status:** Partial  
> **Specs:** [Design Spec §4](../../Design%20Spec.md), [ADR-001](../../adr/ADR-001-clean-architecture.md), [ADR-002](../../adr/ADR-002-storage-adapter-decorator.md), [ADR-005](../../adr/ADR-005-disk-router-most-free.md), [ADR-006](../../adr/ADR-006-metadata-db-blobs-fs.md), [ADR-007](../../adr/ADR-007-minio-blob-backend.md), TZ §4 / §9  
> **Supersedes (partially):** ADR-006 blob placement; ADR-005 free-space probes when backend = MinIO

## Overview

Replace local multi-disk filesystem blob storage (`{STORAGE_ROOT}/{disk_id}/…`) with **self-hosted MinIO** (S3-compatible object storage), while keeping PostgreSQL metadata, Clean Architecture ports, and the existing public HTTP/API contracts.

Application services (`FileService`, `PhotoService`, `PrivateService`, …) must continue to depend on `StorageAdapter` (and related ports), not on `Path` / `aiofiles` / `statvfs`.

## Goals

- Persist all blob bytes (files, photos, private ciphertext, archives, optional DB dump objects) in MinIO.
- Keep metadata, quotas, ACL, encryption, and API behavior unchanged for clients.
- Preserve multi-volume expandability semantics via MinIO buckets/prefixes (or multiple MinIO volumes), without migrating historical object keys when capacity is added.
- Make `EncryptedStorageAdapter` decorate **any** `StorageAdapter` (FS or S3), not only `PlainStorageAdapter`.
- Introduce an explicit ADR that supersedes ADR-006’s “blobs on filesystem” decision for the MinIO deployment mode.
- Support a controlled migration path from existing FS trees into MinIO.

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
- endpoint, access/secret keys, region, secure flag, default bucket(s), optional path-style;
- backend selector (e.g. `STORAGE_BACKEND=fs|s3`) for transition;
- mirror all new vars in `.env.example` / `env.example`.

### US-S3-03 — S3StorageAdapter implements StorageAdapter
As a backend developer, I have `S3StorageAdapter` in Infrastructure that implements the same port as `PlainStorageAdapter`:
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
- free-space / health from MinIO / underlying volume metrics (not `statvfs` on `STORAGE_ROOT`);
- sticky placement: existing records keep their `disk_id`; no auto-rebalance;
- admin `GET /api/admin/storage/health` still returns `HEALTHY | LOW_SPACE | UNAVAILABLE`.

### US-S3-07 — Compose / ops: MinIO in the stack
As an operator, `docker-compose` (and docs) run MinIO + create buckets/policies needed by HomeCloud; README covers credentials, persistence volume for MinIO data, and expand-capacity steps.

### US-S3-08 — Migrate existing FS blobs to MinIO
As an operator with data on PC disks, I run a documented one-shot (or resumable) migration:
- walk `{STORAGE_ROOT}/{disk_id}/…`;
- upload objects with stable keys matching `relative_path` / section layout;
- verify checksums against `file_records.checksum_sha256` where present;
- switch `STORAGE_BACKEND=s3` only after verification;
- support dry-run and progress logging (loguru, no secrets).

### US-S3-09 — Archives, backups, maintenance on object storage
As a platform owner:
- zstd archives are stored as objects; transparent download still works;
- DB dumps land in a dedicated bucket/prefix (replacing `{disk}/_meta/backups/`);
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
| ADR-002 Decorator encryption | **Compatible if US-S3-04 done** | Decorator must wrap the ABC, not hard-depend on `PlainStorageAdapter` FS details. |
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
| `StorageAdapter.base_path: Path` | Part of ABC | FS leak in port | Evolve port to backend-agnostic root (string prefix / URI); update Plain adapter accordingly. |
| DI helpers | `Path.mkdir` in photos/shared/files deps | Assumes local FS | Create prefixes via adapter `mkdir` / ensure-prefix helper. |
| Archive / Thumbnail / Backup | Direct `Path` I/O in places | Bypasses port | Route through adapter or extract `BlobStore` used by both FS and S3. |
| FAMILY/ADMIN quota = “free disk” | Sum of local free space | Must redefine against MinIO usable capacity | Admin stats + quota ceiling from object-storage free space. |
| Encrypted adapter | Imports / assumes plain FS tmp patterns | Risk of FS-only private path | US-S3-04; keep crypto framing identical. |
| E-STORAGE epic | Init layout via `init_storage.py` on disks | FS-centric ops story | Either extend init to ensure buckets or add MinIO bootstrap job; link epics. |

### Layering checklist (DoD gate)

- [x] Domain has no MinIO/boto/aiobotocore imports.
- [x] Application services do not import `pathlib.Path` for blob I/O (logical `PurePosixPath` for key joins only is OK).
- [x] Only Infrastructure talks to MinIO SDK.
- [x] `EncryptedStorageAdapter` composes `StorageAdapter`, not `PlainStorageAdapter` concrete type (except tests).
- [x] ADR-007 (or successor) accepted before marking epic Implemented.
- [x] `.env.example` lists every new setting.
- [x] Existing epic DoDs (files/photos/private/shared/archive) still hold on `STORAGE_BACKEND=s3` (service/adapter regression under moto; see known gaps).

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
- [x] Files / Photos / Private / Shared / Archive / Backup work with `STORAGE_BACKEND=s3` (adapter + service-level coverage; Shared via same `FileService` + section adapter path).
- [x] Admin storage health reflects MinIO capacity (`HEALTHY` / `LOW_SPACE` / `UNAVAILABLE` via DiskRouter; see known gaps for capacity probe).
- [x] FS→MinIO migration tool documented and checksum-verified (`backend/scripts/migrate_fs_to_s3.py` + tests).
- [x] Automated adapter + regression round-trip tests in CI (`test_s3_*`, `test_us_s3_*`, especially US-S3-10).
- [x] Logging covers upload/delete/migrate with `user_id` / `file_id` / `disk_id` / object key (no credentials).
- [x] Epics index status updated when complete.

## Known gaps (honest Partial)

- **DiskRouter capacity:** when MinIO Admin API is unreachable, free-space math uses a documented **1 TiB** total fallback (`free ≈ total − used`). Real multi-volume drive totals from Admin API are preferred when available.
- **No full HTTP ASGI e2e** in US-S3-10: coverage is moto-backed unit/integration at adapter + application service level (no `httpx`/`AsyncClient` against `/api/files|photos|private|shared|admin`). Public HTTP contracts were not changed.
- **Shared section** is not a dedicated US-S3-10 scenario; it reuses `FileService` + DI section adapters already covered by US-S3-05/US-S3-10 files path.
