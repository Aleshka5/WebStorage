# Epic: Shared Folder

> **ID:** E-SHARED  
> **Status:** Implemented  
> **Specs:** [Flow Spec §4](../../Flow%20Spec.md), [API Contract §5](../../API%20Contract.md)

## Overview

Common filesystem space for FAMILY and ADMIN using plain `FileManager` on `/api/shared`.

## Goals

- Hide nav and block API for STRANGER.
- Show uploader identity when available.
- Delete only own files unless ADMIN.

## User Stories

### US-SHARE-01 — Access control
FAMILY/ADMIN browse `/shared`; STRANGER redirected and API denied.

### US-SHARE-02 — Collaborate via uploads
Members upload/mkdir; others see files.

### US-SHARE-03 — Delete ACL
Non-owner non-admin delete → `ACCESS_DENIED`.

## Definition of Done

- [x] Router role check + FE guard/nav visibility.
- [x] Shared path layout under `{disk}/shared`.
- [x] ACL on delete.
- [ ] Tests for STRANGER 403 and delete ACL.
