# Epic: Admin Panel

> **ID:** E-ADMIN  
> **Status:** Partial  
> **Specs:** [Flow Spec §7](../../Flow%20Spec.md), [API Contract §9](../../API%20Contract.md)

## Overview

ADMIN-only management of users (role, private quota, block, delete) and storage overview. TZ also requires unblock and password-reset — backlog.

## Goals

- Safe self-protection (no self role change / self delete).
- Paginated user list with filters.
- Disk stats visibility.

## User Stories

### US-ADM-01 — List users
Pagination, role filter, email search.

### US-ADM-02 — Change role
STRANGER ↔ FAMILY ↔ ADMIN with confirmation UX as needed.

**Successor:** [E-AUTHZ US-AUTHZ-07](../auth-service-roles/init.md) — HomeCloud admin shows `storage_roles` as **immutable text** (no `<select>`). Change roles only in Auth-Service admin. List is live via gRPC `ListUsers`.

### US-ADM-03 — Set private limit
`private_limit_gb` persisted to quota table.

### US-ADM-04 — Block user
Sets `is_active=false`.

**Successor (E-AUTHZ):** account block must happen in Auth-Service (Validate returns `PermissionDenied` / `blocked`). Local `is_active` alone is not enough once sessions are hub-issued.

### US-ADM-05 — Delete user
Removes user + storage cleanup.

### US-ADM-06 — Storage dashboard
Disk total/used/free/status cards.

### US-ADM-07 — Unblock user (backlog)
Inverse of block — **not implemented**.

### US-ADM-08 — Password reset link (backlog)
Per TZ — **not implemented**.

### US-ADM-09 — Ops actions in UI (enhancement)
Trigger backup/archive/maintenance from admin UI (APIs exist).

## Definition of Done

- [x] Core user + storage UI/API.
- [x] Self-guard errors.
- [ ] Unblock + password reset.
- [ ] Optional ops UI.
- [ ] Admin API authorization tests.
