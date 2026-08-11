# Epic: File Management

> **ID:** E-FILES  
> **Status:** Implemented (search stub)  
> **Specs:** [Flow Spec §3](../../Flow%20Spec.md), [API Contract §4](../../API%20Contract.md), [ADR-002](../../adr/ADR-002-storage-adapter-decorator.md)

## Overview

Personal file manager for authenticated users: browse, upload (incl. ZIP), download (file/folder), mkdir, rename, delete — with quota enforcement.

## Goals

- One `FileManager` UX for plain storage.
- Atomic-ish upload via PENDING → COMMITTED.
- Path traversal protection.

## User Stories

### US-FILES-01 — Browse & navigate
Breadcrumbs + folder listing with sort by name/size/date.

### US-FILES-02 — Upload files
Drag-drop / button; progress; quota `413` when exceeded.

### US-FILES-03 — Upload ZIP
Extract archive into current path; report counts/bytes.

### US-FILES-04 — Download
Single file stream; folder as ZIP.

### US-FILES-05 — Folder ops
Create (validated name), rename, delete with confirm.

### US-FILES-06 — Search (reserved)
`GET /api/files/search` returns `501` until implemented.

## Definition of Done

- [x] CRUD + ZIP behaviors on `/files`.
- [x] Quota and traversal errors surfaced.
- [x] Logging on upload/delete with `user_id` / `file_id` context.
- [ ] Search implemented or explicitly deferred via ADR.
- [ ] Automated API tests for upload/delete/quota.
