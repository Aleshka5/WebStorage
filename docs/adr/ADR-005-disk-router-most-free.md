# ADR-005: DiskRouter selects disk by most free space

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** `DiskRouter`, `STORAGE_DISKS`, `MIN_FREE_SPACE_MB`

## Context

Home installs may add disks over time. New writes must land on a volume with enough free space without migrating old files.

## Decision

- Configure disks via `STORAGE_DISKS` under `STORAGE_ROOT`.
- For writes, choose the mounted disk with the **most free space** among those with ≥ `MIN_FREE_SPACE_MB`.
- Cache free-space probes for `DISK_SPACE_CACHE_TTL` seconds.
- Keep `DISK_STRATEGY` in settings for future strategies; **runtime currently implements most-free only**.

No automatic rebalancing of existing objects.

## Consequences

### Positive

- Simple ops: mount + env + init_storage + restart.
- Reduces “disk full” on a single volume.

### Negative / Trade-offs

- Uneven historical distribution remains.
- Strategy env can confuse operators until other strategies ship.

## Alternatives Considered

| Option | Why not (now) |
|---|---|
| Round-robin | May hit nearly-full disks |
| Single disk only | Blocks expansion story in TZ |
| DB-tracked volumes table | Extra migration; env list sufficient for v1 |
