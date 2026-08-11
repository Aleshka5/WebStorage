# Epic: Private Encrypted Storage

> **ID:** E-PRIVATE  
> **Status:** Implemented  
> **Specs:** [Flow Spec §6](../../Flow%20Spec.md), [API Contract §7](../../API%20Contract.md), [ADR-004](../../adr/ADR-004-private-key-redis-ttl.md)

## Overview

Passphrase-protected vault with server-side AES-GCM, encrypted names, marker validation, Redis session key, and FileManager in `encrypted` mode.

## Goals

- Passphrase never persisted in DB/cookie/logs.
- Private TTL independent of JWT.
- Private sub-quota enforced.
- Rate-limit unlock; optional destructive reset.

## User Stories

### US-PRIV-01 — Unlock modal
Enter passphrase → unlock → see files; cancel → `/files`.

### US-PRIV-02 — Encrypted CRUD
Same file ops as Files via `/api/private/*` while session active.

### US-PRIV-03 — Session expiry UX
`PRIVATE_SESSION_EXPIRED` reopens unlock without full logout.

### US-PRIV-04 — Private quota bar
Show `private_bytes` / `private_limit_bytes`.

### US-PRIV-05 — Lockout reset
After `TOO_MANY_ATTEMPTS`, user may reset vault (wipe) deliberately.

### US-PRIV-06 — Explicit lock control (enhancement)
Expose FE action calling `POST /api/private/lock`.

## Definition of Done

- [x] Unlock/lock/session/quota/reset APIs.
- [x] Marker + PBKDF2 + AES-GCM adapter.
- [x] FE unlock + encrypted FileManager.
- [ ] Dedicated lock button in UI (optional).
- [ ] Automated tests for wrong key, expiry, reset.
