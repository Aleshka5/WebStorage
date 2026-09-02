# ADR-006: Metadata in PostgreSQL; blobs on filesystem

- **Status:** Superseded (blob placement) by [ADR-007](./ADR-007-minio-blob-backend.md). Metadata-in-PostgreSQL remains valid.
- **Date:** 2026-06-01
- **Related:** `file_records`, storage layout, backup jobs, [ADR-007](./ADR-007-minio-blob-backend.md)

## Context

Need listing, quotas, archive state, and admin views without putting large binaries in the database.

## Decision (historical)

- Store **metadata** (users, file records, quota usage) in PostgreSQL.
- Store **file bytes** (and encrypted blobs, thumbnails, archives) on disk volumes.
- Back up DB daily with `pg_dump` + zstd to `{disk}/_meta/backups/`.
- File content durability is the filesystem/volume responsibility.

Blob placement is **no longer** filesystem volumes. See ADR-007: MinIO is the only blob store; backup objects live at `_meta/backups/` inside the first disk bucket.

## Consequences

### Positive

- Streaming large downloads without DB bloat.
- Natural multi-disk expansion.
- Quotas/search indexes feasible on metadata.

### Negative / Trade-offs

- Risk of metadata/blob drift (mitigated by PENDING cleanup + reconcile).
- Restore requires both DB dump and blob store data.

## Alternatives Considered

| Option | Why not (at the time) |
|---|---|
| Blobs in PostgreSQL | Poor large-object streaming; backup size |
| S3-only | Then considered heavier for home Docker; **superseded by ADR-007** |
