# ADR-007: MinIO (S3 API) as blob backend

- **Status:** Accepted
- **Date:** 2026-08-11
- **Deciders:** team
- **Related:** Epic [E-S3MINIO](../epics/s3-minio-migration/init.md), TZ §4 / §9, [ADR-001](./ADR-001-clean-architecture.md), [ADR-002](./ADR-002-storage-adapter-decorator.md), [ADR-005](./ADR-005-disk-router-most-free.md), [ADR-006](./ADR-006-metadata-db-blobs-fs.md) (superseded for blob placement)

## Context

ADR-006 placed blob bytes on local filesystem volumes under `{STORAGE_ROOT}/{disk_id}/…` and rejected S3 as heavier for a home Docker target. TZ §4 specifies the same physical directory tree (`/storage/diskN/users/...`).

HomeCloud still needs multi-volume expandability, sticky placement, streaming I/O, and Clean Architecture ports (`StorageAdapter` + encrypted decorator), but operators now want **self-hosted MinIO** as the durable blob store. Local mounts remain useful during migration and as an optional backend.

Constraints:

- Metadata, quotas, ACL, encryption framing, and public HTTP/API contracts must not change for clients.
- Application services must not depend on `Path` / `aiofiles` / `statvfs` for blobs (ADR-001).
- `EncryptedStorageAdapter` must decorate any `StorageAdapter` (ADR-002), not only FS.
- Capacity routing keeps “most free / healthy volume” semantics (ADR-005) without auto-rebalancing existing objects.
- Logical locators stay `FileRecord.disk_id` + `relative_path`.

## Decision

1. **Blob backend:** Persist file bytes (files, photos, private ciphertext, archives, optional DB dump objects) in **self-hosted MinIO** via the **S3 API**. Supported product target is MinIO; the infrastructure port speaks generic S3.
2. **Metadata:** Keep users, `file_records`, quotas, and ACL in **PostgreSQL** (unchanged half of ADR-006).
3. **Logical key isomorphism:** Object keys preserve today’s relative layout under a disk root, e.g. `users/{user_id}/files/...`, `users/{user_id}/photos/...`, `users/{user_id}/private/...`, `shared/...`, `_meta/backups/...`. The logical path remains isomorphic to `{disk_id}/…` relative paths; only the physical store changes from FS to object storage.
4. **Volume mapping:** Map each configured `disk_id` to a MinIO **bucket** (or a named volume / dedicated prefix). New writes pick a healthy mapped volume with enough free space; existing records keep their `disk_id` (**sticky placement**; **no auto-rebalance**).
5. **Dual backend transition:** Select implementation with `STORAGE_BACKEND=fs|s3`. `fs` retains the ADR-006 layout for local/dev and migration; `s3` is the MinIO path. Switch to `s3` only after verified migration (epic US-S3-08).
6. **Layering:** MinIO/S3 client lives in Infrastructure as `S3StorageAdapter` implementing the same port as the plain FS adapter. Domain/Application remain backend-agnostic.

### Intentional deviation (TZ §4 / ADR-006)

| Source | Stated | This ADR |
|---|---|---|
| TZ §4 | Physical tree `/storage/diskN/...` on mounted disks | **Logical** tree isomorphic to those relative paths; physical durability is MinIO buckets/objects |
| ADR-006 | Blobs on filesystem; S3 rejected for home Docker | Blobs on MinIO when `STORAGE_BACKEND=s3`; FS retained as optional/transitional backend |

Metadata-in-PostgreSQL from ADR-006 remains valid and is **not** superseded.

## Consequences

### Positive

- Object storage durability and ops model without changing client APIs or metadata schema.
- Capacity expansion = add MinIO volume/bucket mapping + config; no rewrite of historical object keys.
- Same adapter port enables FS→S3 migration and encrypted private vault on either backend.
- Streaming upload/download and SHA-256 verification remain feasible via S3 multipart / ranged get.

### Negative / Trade-offs

- **Backup:** DB dumps move from `{disk}/_meta/backups/` on local mounts to a dedicated bucket/prefix; restore requires PostgreSQL dump **and** MinIO data volume(s), not only host disk trees.
- **Health / capacity:** Admin `GET /api/admin/storage/health` (`HEALTHY | LOW_SPACE | UNAVAILABLE`) and DiskRouter must use MinIO / underlying volume metrics instead of `statvfs` on `STORAGE_ROOT` (ADR-005 probe mechanism evolves; sticky most-free policy stays).
- **Dual backend:** Operators must keep `STORAGE_BACKEND`, credentials, and bucket maps consistent; mis-switch before migration causes missing objects.
- Ops surface grows (MinIO service, credentials, bucket bootstrap) vs pure bind-mount disks.

### Follow-ups

- Settings / `env.example` for MinIO + `STORAGE_BACKEND` (US-S3-02).
- `S3StorageAdapter` + port hardening (US-S3-03); decrypt decorator decoupling (US-S3-04).
- DiskRouter / health against MinIO capacity (US-S3-06); compose MinIO (US-S3-07).
- FS→MinIO migration with checksum verify (US-S3-08); archives/backups/maintenance on objects (US-S3-09).

## Alternatives Considered

| Option | Why not |
|---|---|
| Keep FS-only (ADR-006 as-is) | Does not meet operator goal of MinIO-backed durability and epic E-S3MINIO |
| Blobs in PostgreSQL | Poor large-object streaming; backup size (rejected in ADR-006; still rejected) |
| AWS S3 / multi-cloud as primary | Out of scope; MinIO is the supported self-hosted target; generic S3 API is the port |
| Flat key scheme (`file_id` only) | Breaks isomorphism with TZ layout, migration from existing trees, and section prefixes |
| Auto-rebalance across buckets | Violates sticky `disk_id` placement; costly and risky for home installs |
| Metadata in object tags / MinIO only | Loses relational quotas, search, ACL; contradicts ADR-006 metadata half |

## Notes

- Locator contract unchanged: `disk_id` + `relative_path` → resolve bucket (or volume prefix) + object key.
- Epic: [docs/epics/s3-minio-migration/init.md](../epics/s3-minio-migration/init.md) (US-S3-01 … US-S3-10).
- ADR-005 remains accepted for placement *strategy*; free-space probe implementation is backend-specific under this ADR when `STORAGE_BACKEND=s3`.
