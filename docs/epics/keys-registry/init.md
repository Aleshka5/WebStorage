# Epic: Keys Registry

> **ID:** E-KEYS  
> **Status:** Implemented  
> **Specs:** [API Contract §7](../../API%20Contract.md), [Flow Spec §6a](../../Flow%20Spec.md), [FRONTEND_DOCS](../../FRONTEND_DOCS.md), [BACKEND_DOCS](../../BACKEND_DOCS.md)

## Overview

A passphrase-gated registry of named secrets stored as a single encrypted YAML file in the user’s private vault. Same unlock session as Private (`auth_session` + Redis vault key). No second passphrase.

Canonical path (private root): `Keys/keys.yaml` → object key `users/{user_id}/private/Keys/keys.yaml`.

## Locked decisions

| # | Decision |
|---|---|
| D1 | Path is always `Keys/keys.yaml` at the private root. |
| D2 | YAML is a flat `name: value` string map. No groups, no extra metadata. |
| D3 | API returns full values (vault already unlocked). UI masks display (`abcd...wxyz` or `••••` if length ≤ 8). Copy uses the full in-memory value. |
| D4 | First GET after unlock bootstraps: create `Keys/` if missing; create empty `keys.yaml` if missing. Empty file = empty mapping. |
| D5 | `Keys/` and `keys.yaml` are normal private files (visible in `/private` FileManager; user may delete/rename/download). |
| D6 | Keys UI: **add**, **delete**, **copy**. No edit of existing name or value. Empty and duplicate names rejected. |
| D7 | Sidebar: top-level item (not under Private). Order: Photos, Files, Private, Keys Registry, Shared, Admin. Route `/keys`. |
| D8 | Explicit Save only. Last write wins (no ETag). Save writes the whole YAML file. |
| D9 | Same private session as Private; all roles including STRANGER. English UI labels. |
| D10 | Invalid existing YAML / not a mapping → error; GET must not overwrite. |
| D11 | Private reset wipes the keys file (normal private file; no special-case). |

## Goals

- Store API keys and similar secrets in the existing private vault.
- One FileRecord for `keys.yaml`; overwrite in place (quota charged as size delta).
- Never log secret values.

## User Stories

### US-KEYS-01 — Unlock and open registry

**As** a signed-in user (any storage role except BLOCKED), **I open Keys Registry** and unlock with the same private passphrase.

**Acceptance**

- Route `/keys`; sidebar label `Keys Registry` (Key icon).
- Same unlock modal + `usePrivateSession` as Private. Cancel → `/files`.
- After unlock, `GET /api/private/keys` bootstraps `Keys/` and empty `keys.yaml` if needed.
- `401 PRIVATE_SESSION_EXPIRED` reopens the unlock modal (existing window event).
- Missing vault cookie → `401 UNAUTHORIZED` (same as other private routes).

### US-KEYS-02 — List, add, delete, copy, save

**As** an unlocked user, **I manage keys** in a dedicated page.

**Acceptance**

- List shows name + masked value. Copy puts the full value on the clipboard.
- Add: name + value; reject empty (after trim) and duplicate names in UI and API.
- Delete is local until Save.
- Save (`PUT /api/private/keys`) writes the full list. Disabled when pristine.
- Empty state: “No keys yet” + add action.
- No in-place edit of an existing name or value.

### US-KEYS-03 — YAML file is a normal private file

**As** a user, **I see `Keys` in Private** FileManager and can delete/rename/download it there.

**Acceptance**

- After first GET, Private listing includes `Keys/` and `keys.yaml`.
- If the user deletes the file in FileManager, the next GET recreates an empty mapping; the next Save recreates one FileRecord.
- Repeated Save does **not** create a second FileRecord for the same path; quota is charged as the size delta.

### US-KEYS-04 — Invalid YAML is not clobbered

**As** a user who edited `keys.yaml` by hand, **I get an error** if the file is not a flat string map.

**Acceptance**

- GET on invalid YAML or a non-mapping → `409 KEYS_YAML_INVALID`. File is not rewritten.
- PUT validation: empty name/value → `400`; duplicate names → `400`.

## Definition of Done

- [x] Epic + API / Flow / FE / BE / Test spec updates.
- [x] `GET`/`PUT /api/private/keys` on the unlocked private vault.
- [x] In-place overwrite (one FileRecord; quota delta).
- [x] Keys Registry page + sidebar root item.
- [x] Backend tests for session, bootstrap, round-trip, validation, invalid YAML, no duplicate records.
