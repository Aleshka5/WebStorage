# Epic: Storage Infrastructure

> **ID:** E-STORAGE  
> **Status:** Implemented (strategy variants pending)  
> **Specs:** [Design Spec §4.2](../../Design%20Spec.md), [ADR-005](../../adr/ADR-005-disk-router-most-free.md), [ADR-006](../../adr/ADR-006-metadata-db-blobs-fs.md)

## Overview

Multi-disk filesystem layout, DiskRouter write placement, init scripts, quota denormalization, and health reporting.

## Goals

- Add disk without migrating old files.
- Refuse writes when no disk has free space ≥ threshold.
- Keep metadata/FS consistent via maintenance jobs.

## User Stories

### US-STOR-01 — Init layout
`init_storage.py` creates users/shared/_meta trees per disk.

### US-STOR-02 — Route new writes
Most-free-space selection among healthy disks.

### US-STOR-03 — Expand capacity
Documented flow: mount volume → `STORAGE_DISKS` → restart → init.

### US-STOR-04 — Health endpoint
Admin sees HEALTHY / LOW_SPACE / UNAVAILABLE.

### US-STOR-05 — Alternate strategies (backlog)
Implement round-robin / priority using `DISK_STRATEGY`.

### US-STOR-06 — Quota reconcile
Daily job repairs drift &gt; 1MB.

## Definition of Done

- [x] DiskRouter + env configuration.
- [x] Init + README expansion docs.
- [x] Health/stats admin APIs.
- [ ] Additional strategies behind `DISK_STRATEGY`.
- [ ] Integration test with two temp disks.
