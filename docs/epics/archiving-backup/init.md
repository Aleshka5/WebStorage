# Epic: Archiving & Backup

> **ID:** E-ARCHIVE  
> **Status:** Backend implemented; FE ops limited  
> **Specs:** [Design Spec §5](../../Design%20Spec.md), [API Contract §9](../../API%20Contract.md), README restore section

## Overview

Reduce disk footprint by zstd-archiving idle files and protect metadata via scheduled PostgreSQL dumps.

## Goals

- Transparent read of archived content.
- `pre_encrypt` / `post_encrypt` modes for private vs plain.
- Daily backup with 30-day retention.
- Admin can trigger/list backups and archive runs via API.

## User Stories

### US-ARC-01 — Daily archive job
Files idle &gt; `ARCHIVE_DAYS_THRESHOLD` → archived; status `ARCHIVED`.

### US-ARC-02 — Transparent download
Download path restores/streams content without user action.

### US-ARC-03 — DB backup job
02:00 `pg_dump` → zstd under `_meta/backups`.

### US-ARC-04 — Manual backup
ADMIN `GET /api/admin/backup/run` + list.

### US-ARC-05 — Maintenance cleanup
PENDING and `.tmp` cleaners hourly.

### US-ARC-06 — Admin UI for ops (enhancement)
Expose archive/backup buttons and last-run stats in AdminPage.

## Definition of Done

- [x] Scheduler jobs registered in app.
- [x] Archive/backup/maintenance admin APIs.
- [x] Restore documented in README.
- [ ] Admin UI for ops.
- [ ] Automated test that archive+download round-trips a sample file.
