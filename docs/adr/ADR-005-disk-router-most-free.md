# ADR-005: DiskRouter selects disk by most free space

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** `DiskRouter`, `STORAGE_DISKS`, `MIN_FREE_SPACE_MB`, [ADR-007](./ADR-007-minio-blob-backend.md)
- **Note:** Probe implementation is MinIO/S3 (HeadBucket + object sizes + Admin API). Placement *strategy* is unchanged.

## Context

Home installs may add capacity over time. New writes must land on a volume with enough free space without migrating old objects.

## Decision

- Configure logical disks via `STORAGE_DISKS`. Each id maps to MinIO bucket `{S3_BUCKET_PREFIX}{disk_id}`.
- For writes, choose the bucket with the **most free space** among those with ≥ `MIN_FREE_SPACE_MB`.
- Cache free-space probes for `DISK_SPACE_CACHE_TTL` seconds.
- Keep `DISK_STRATEGY` in settings for future strategies; **runtime currently implements most-free only**.

No automatic rebalancing of existing objects.

## Consequences

### Positive

- Simple ops: add `STORAGE_DISKS` entry + `minio-init` bucket bootstrap + restart.
- Reduces “disk full” on a single volume/bucket.

### Negative / Trade-offs

- Uneven historical distribution remains.
- Strategy env can confuse operators until other strategies ship.
- If MinIO Admin API is down, total capacity falls back to a documented 1 TiB.

## Alternatives Considered

| Option | Why not (now) |
|---|---|
| Round-robin | May hit nearly-full disks |
| Single disk only | Blocks expansion story in TZ |
| DB-tracked volumes table | Extra migration; env list sufficient for v1 |
