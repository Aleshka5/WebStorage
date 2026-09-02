# Epic: Storage Infrastructure

> **ID:** E-STORAGE  
> **Status:** Implemented (strategy variants pending)  
> **Specs:** [Design Spec §4.2](../../Design%20Spec.md), [ADR-005](../../adr/ADR-005-disk-router-most-free.md), [ADR-007](../../adr/ADR-007-minio-blob-backend.md)

## Overview

Logical multi-disk layout (MinIO buckets 1:1 with `STORAGE_DISKS`), DiskRouter write placement, quota denormalization, and health reporting.

## Goals

- Add a logical disk (bucket) without migrating old objects.
- Refuse writes when no disk has free space ≥ threshold.
- Keep metadata/object store consistent via maintenance jobs.

## User Stories

### US-STOR-01 — Init layout
`minio-init` creates one bucket per `STORAGE_DISKS` entry (+ `backups`). Logical prefixes (`users/`, `shared/`, `_meta/backups`) are created via `StorageAdapter.mkdir`.

### US-STOR-02 — Route new writes
Most-free-space selection among healthy buckets.

### US-STOR-03 — Expand capacity
Documented flow: add id to `STORAGE_DISKS` → restart so `minio-init` creates the bucket.

### US-STOR-04 — Health endpoint
Admin sees HEALTHY / LOW_SPACE / UNAVAILABLE.

### US-STOR-05 — Alternate strategies (backlog)
Implement round-robin / priority using `DISK_STRATEGY`.

### US-STOR-06 — Quota reconcile
Daily job repairs drift &gt; 1MB.

## Definition of Done

- [x] DiskRouter + env configuration (MinIO probes).
- [x] Bucket bootstrap + README expansion docs.
- [x] Health/stats admin APIs (`bucket` on DiskStat).
- [ ] Additional strategies behind `DISK_STRATEGY`.
- [ ] Integration test with two temp disks.
