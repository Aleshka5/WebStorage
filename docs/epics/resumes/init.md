# Epic: Resumes (Job Applications)

> **ID:** E-RESUMES  
> **Status:** Implemented  
> **Specs:** [API Contract §9](../../API%20Contract.md), [Flow Spec §8](../../Flow%20Spec.md), [Data Models §7](../../Data%20Models.md), [ADR-010](../../adr/ADR-010-resumes-yaml-tree-on-s3.md), [FRONTEND_DOCS](../../FRONTEND_DOCS.md), [BACKEND_DOCS](../../BACKEND_DOCS.md)

## Overview

A four-level workspace for tracking job applications, built on the existing S3 storage stack — no new tables, no encryption:

```
Country  →  Company  →  Vacancy  →  Vacancy leaf (url + key/value fields + files)
```

Countries, companies and vacancies are **real S3 directories** created through the existing
`FileService.create_directory` / `rename_by_path` / delete paths. Every vacancy carries a
`meta.yaml` written through `FileService.overwrite_file` — the same technique
[E-KEYS](../keys-registry/init.md) uses for `Keys/keys.yaml`, minus the encrypted adapter.
Vacancy attachments are ordinary files served by the standard file endpoints and rendered by the
existing `<FileManager />`.

## Storage layout

Section root prefix: `users/{user_id}/resumes` (new `FileSection.RESUMES`).

```
users/{user_id}/resumes/
├── statuses.yaml                       ← per-user status list
└── {country}/
    └── {company}/
        └── {vacancy}/
            ├── meta.yaml               ← website_url, status_id, fields[]
            └── <attachments…>          ← normal files (FileManager UI)
```

### `statuses.yaml`

```yaml
statuses:
  - id: 7f4c1e0a9b2d4f6e8a1c3b5d7e9f0a2b   # uuid4 hex, stable across renames
    name: Applied
    color: "#38BDF8"
```

### `{country}/{company}/{vacancy}/meta.yaml`

```yaml
website_url: https://example.com/jobs/42     # optional; "" when unset
status_id: 7f4c1e0a9b2d4f6e8a1c3b5d7e9f0a2b # null when unset / detached
fields:
  - name: Salary
    value: 4000 EUR
  - name: Recruiter
    value: jane@example.com
```

## Locked decisions

| # | Decision |
|---|---|
| D1 | Tree and metadata live on S3, not in Postgres. Directories are the hierarchy; `meta.yaml` / `statuses.yaml` are the metadata. No new tables. |
| D2 | Dedicated section prefix `users/{user_id}/resumes` and a new `FileSection.RESUMES` enum value (Alembic `006`). Resumes are **not** visible in the `/files` manager. |
| D3 | Not encrypted. Plain `S3StorageAdapter`, no private-vault unlock. |
| D4 | Access: **FAMILY and ADMIN only**, gated by the existing `check_role(Role.FAMILY, Role.ADMIN)` — same rule as `/shared`. STRANGER gets `403 ACCESS_DENIED` and no sidebar item. |
| D5 | Statuses are one **per-user global list**, seeded on first read with Applied / Interview / Offer / Rejected. Editing a status name or color updates every vacancy that references it (references are by `id`). |
| D6 | Deleting a status **detaches** it: vacancies referencing it render as “No status”. No blocking dialog, no cascade write. |
| D7 | `website_url` is an **optional** field on the vacancy leaf. A non-empty value without a scheme is normalised to `https://`. Empty is allowed and stored as `""`. |
| D8 | Rename and delete are supported at all three levels. Delete is **recursive and hard**: blobs, `file_records` rows and quota are all released. Confirm dialog names the node and its descendant count. |
| D9 | **No move** operation in this slice (a vacancy cannot be relocated between companies). |
| D10 | Status filtering lives on the **All vacancies** page (US-RES-08), not on the per-company vacancy list: filtering is only useful across the whole tree. It is client-side, multi-select, with an allow-list (**Show only**) / block-list (**Hide**) mode. An empty selection filters nothing in either mode. |
| D11 | Node names: trimmed, 1–128 chars, no `/` `\`, not `.`/`..`, unique among siblings case-insensitively, and not the reserved names `statuses.yaml` / `meta.yaml`. |
| D12 | Tree depth is fixed at 3. `POST /api/resumes/tree` rejects a create below a vacancy; folders *inside* a vacancy are created through the files router like any other folder. |
| D13 | Quota: resume files count toward the user’s total `limit_bytes` only (no per-section sub-limit). `statuses.yaml` and `meta.yaml` are charged like any other file, as a size delta on overwrite. |
| D14 | Metadata writes are last-write-wins whole-file replacements, like E-KEYS. No ETag, no partial patch. |
| D15 | Corrupt `meta.yaml` / `statuses.yaml` is reported, never silently overwritten on read (`409 RESUME_META_INVALID`). A corrupt `meta.yaml` is fatal only for `GET /vacancy`; in a **listing** it degrades to “No status” + empty URL and logs a warning, so one bad file cannot break a whole company page. |
| D16 | UI is English-only, matching the rest of the app. |
| D17 | Uniqueness is case-insensitive everywhere it applies: sibling node names, status names, and vacancy field names. |
| D18 | `PUT /vacancy` does not verify that `status_id` still exists — that is what lets a detached status (D6) round-trip. `POST /tree` does verify it, so a vacancy is never *created* with a bogus status. |
| D19 | The flat vacancy list lives at top-level `/vacancies`, not `/resumes/all`: a static child segment would silently shadow a country named “all”, and reserving a country name to protect a URL is worse than moving the route. |

## Goals

- Reuse `FileService`, `S3StorageAdapter` and `<FileManager />` unchanged wherever possible.
- Add exactly one new section prefix and zero new tables.
- Keep every existing `/files`, `/private`, `/shared` behaviour untouched.

## Non-goals

- Moving vacancies between companies (D9).
- Sharing a resume tree with another user.
- Server-side search across vacancies.
- Encryption of resume data.

## User Stories

### US-RES-01 — Open the Resumes section

**As** a FAMILY or ADMIN user, **I click “Resumes” in the sidebar** and land on `/resumes`.

**Acceptance**

- Sidebar shows `Resumes` (Briefcase icon) between `Keys Registry` and `Shared`, only for FAMILY/ADMIN.
- Route `/resumes` renders the countries page; first visit bootstraps `users/{id}/resumes/` and a seeded `statuses.yaml`.
- STRANGER: no sidebar item; direct navigation to `/resumes` shows the access-denied state and the API returns `403 ACCESS_DENIED`.
- Empty tree renders an empty state with **Add new country** centred at the top.

### US-RES-02 — Manage countries

**As** a user, **I add, rename and delete countries.**

**Acceptance**

- “Add new country” sits centred at the top while the list is empty, and moves to the end of the list once at least one country exists.
- Create sends `POST /api/resumes/tree` with `{ path: "", name }`; the country appears without a full reload.
- Duplicate name (case-insensitive) → `409 RESUME_NODE_EXISTS`, surfaced inline in the dialog.
- Invalid name (empty, `/`, `\`, `.`, `..`, >128 chars, reserved) → `400 RESUME_NAME_INVALID`.
- Rename via `PATCH /api/resumes/tree`; existing `rename_by_path` rewrites the `file_records` path prefix for everything beneath.
- Delete via `DELETE /api/resumes/tree?path=` after a confirm dialog naming the country and how many companies/vacancies go with it; blobs, records and quota are all released — including files that the archive job has already moved to zstd.
- Clicking a country navigates to `/resumes/:country`.

### US-RES-03 — Manage companies

**As** a user inside a country, **I add, rename and delete companies** the same way.

**Acceptance**

- Same button placement, validation, duplicate and delete semantics as US-RES-02, at `path = "{country}"`.
- Breadcrumb `Resumes / {country}`; each crumb navigates.
- Unknown country in the URL → `404 FILE_NOT_FOUND` and a “not found” state with a link back to `/resumes`.
- Clicking a company navigates to `/resumes/:country/:company`.

### US-RES-04 — Manage vacancies and their status

**As** a user inside a company, **I add vacancies, each with a status.**

**Acceptance**

- “Add a new vacancy” dialog takes a name and a status picked from the status list (status may be left unset).
- Create writes the vacancy directory **and** its `meta.yaml` with the chosen `status_id`, empty `website_url` and no fields.
- The vacancy list shows each vacancy’s name plus its status as a coloured dot + label; a detached or unset status renders as a neutral “No status”.
- Rename and delete behave as in US-RES-02/03. Rename preserves `meta.yaml` and attachments (directory rename).
- Clicking a vacancy navigates to the leaf page.

### US-RES-05 — Edit the status list

**As** a user, **I add, rename, recolor and delete statuses** in one editable list.

**Acceptance**

- Status editor reachable from the vacancies page; lists every status with a colour swatch.
- Add: name + colour. Empty name, duplicate name (case-insensitive) or a colour that is not `#RRGGBB` → `400 RESUME_STATUS_INVALID`.
- Rename/recolor edits in place; `id` never changes, so every vacancy keeps its reference.
- Delete removes the status from the list only. Vacancies keeping the dangling `status_id` render “No status”; no vacancy file is rewritten.
- Explicit **Save** writes the whole list via `PUT /api/resumes/statuses`. Save is disabled while pristine.
- First `GET /api/resumes/statuses` seeds Applied / Interview / Offer / Rejected if `statuses.yaml` is missing.

### US-RES-06 — The vacancy leaf page

**As** a user, **I open a vacancy** and edit its website URL, its key/value fields and its files.

**Acceptance**

- Leaf shows: breadcrumb, vacancy name, status selector, `Website URL` input, an editable key/value list, and a `<FileManager />` scoped to the vacancy folder.
- URL is optional; a non-empty value without a scheme is saved as `https://<value>`. When set, it renders as an external link.
- Fields behave like the Keys Registry list — add, edit, delete — but values are plain text and fully visible, with no masking and no encryption.
- Field names: trimmed, non-empty, unique per vacancy case-insensitively → otherwise `400 RESUME_FIELD_INVALID`. Field order is preserved.
- Explicit **Save** writes `meta.yaml` via `PUT /api/resumes/vacancy?path=`; disabled while pristine; unsaved-changes warning on navigate away.
- Corrupt `meta.yaml` → `409 RESUME_META_INVALID` on read; the page shows the error and the file is not overwritten.

### US-RES-07 — Vacancy files

**As** a user on the leaf page, **I upload, download, rename and delete files** for that vacancy.

**Acceptance**

- The embedded `<FileManager apiPrefix="/api/resumes/files" basePath="{country}/{company}/{vacancy}" />` supports the full existing contract: list, upload (incl. ZIP), download file, download folder as ZIP, mkdir, rename, delete.
- `meta.yaml` is hidden from the listing (`hiddenNames`) so the file area shows only user content.
- Breadcrumbs inside the manager are rooted at the vacancy, never above it; paths above `basePath` are unreachable.
- Quota errors surface as `413 QUOTA_EXCEEDED` exactly as on `/files`; the sidebar quota bar refreshes after upload/delete.
- Deleting the vacancy removes its attachments and their quota.

### US-RES-08 — Flat list of every vacancy

**As** a user, **I open one page listing every vacancy I have created**, with its country, company,
name and status, and jump from any of them to the matching page.

**Acceptance**

- Route `/vacancies` (top level, same FAMILY/ADMIN gate), reached from an **All vacancies** button on
  the countries page; the page links back with **Back to countries**.
- `GET /api/resumes/vacancies` returns every vacancy across the whole tree, ordered by country, then
  company, then vacancy name (case-insensitive). Empty countries and companies contribute nothing.
- Table columns: Country, Company, Vacancy, Status. Country/Company/Vacancy are links to
  `/resumes/:country`, `/resumes/:country/:company` and the vacancy leaf respectively.
- Status renders with the same badge as the vacancies page; an unset or dangling `status_id` shows
  "No status".
- Status filter: multi-select over every status plus a "No status" option, with two modes —
  **Show only** (allow-list) and **Hide** (block-list). Selecting nothing filters nothing in either
  mode. A dangling `status_id` is filtered as "No status", matching how it renders. The control
  reports "Showing X of Y vacancies"; filtering everything out shows an empty state with **Clear
  filter**.
- A vacancy whose `meta.yaml` is corrupt is still listed (status null, empty URL) rather than failing
  the whole page — same degradation as the tree listing (D15).
- Empty tree → "No vacancies yet" + a link to `/resumes`.

## Definition of Done

- [x] Epic + ADR-010 + API / Data / Flow / FE / BE / Test spec updates + epics index row.
- [x] `FileSection.RESUMES` + Alembic `006` enum migration.
- [x] `ResumeService` (tree, vacancy meta, statuses) over `FileService`, no new repositories.
- [x] `FileService.delete_directory_recursive` releasing blobs, records and quota.
- [x] `/api/resumes/*` router + `/api/resumes/files/*` sub-router, both `check_role(FAMILY, ADMIN)`.
- [x] `<FileManager />` gains optional `basePath` / `hiddenNames`; `/files`, `/private`, `/shared` unchanged.
- [x] Four SPA routes, sidebar item, status editor, leaf page.
- [x] Backend tests green: role gate, bootstrap, CRUD at each level, validation, status detach, meta round-trip, invalid YAML, recursive delete quota, file router reuse.
- [x] Flat vacancy list endpoint + page, with tests for ordering, empty branches, corrupt meta and the role gate.
