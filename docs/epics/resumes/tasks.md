# E-RESUMES implementation tasks

Source of truth: [init.md](./init.md) and [ADR-010](../../adr/ADR-010-resumes-yaml-tree-on-s3.md).

### T1 — Specs

- [x] Epic `init.md` + this `tasks.md`
- [x] ADR-010 (S3 directories + YAML metadata)
- [x] API Contract §9 (`/api/resumes/*`, `/api/resumes/files/*`, error codes)
- [x] Data Models (`FileSection.RESUMES`, YAML document shapes)
- [x] Flow Spec (four pages, dialogs, status editor, unsaved-changes rule)
- [x] FRONTEND_DOCS (routes, sidebar, pages, FileManager props)
- [x] BACKEND_DOCS (`ResumeService`, router, recursive delete)
- [x] Test Spec rows
- [x] Master Document UI-surface row + epics index row

### T2 — Backend domain & storage

- [x] `FileSection.RESUMES` in the domain enum and the ORM enum
- [x] Alembic `006_add_resumes_file_section.py` (`ALTER TYPE file_section ADD VALUE 'RESUMES'`, non-transactional)
- [x] `user_resumes_root_prefix()` in `storage_factory.py`
- [x] Error codes: `RESUME_NAME_INVALID`, `RESUME_NODE_EXISTS`, `RESUME_DEPTH_INVALID`, `RESUME_STATUS_INVALID`, `RESUME_FIELD_INVALID`, `RESUME_META_INVALID` + domain exceptions + handler registration
- [x] `FileService.delete_directory_recursive` — blobs, `file_records` rows and quota released together
- [x] `FileRepository.list_committed_under_prefix` (or reuse) for the recursive delete

### T3 — Backend application & presentation

- [x] `ResumeService`: bootstrap, tree list/create/rename/delete, vacancy meta get/save, statuses get/save, YAML parse + validation
- [x] `get_resume_service` / `get_resumes_file_service` DI, both behind `check_role(FAMILY, ADMIN)`
- [x] `resume_router` — `/api/resumes/tree`, `/vacancy`, `/statuses`
- [x] `/api/resumes/files/*` file router built from the shared handler set
- [x] Register both routers in `main.py`
- [x] Structured loguru logging on every business path

### T4 — Frontend

- [x] `<FileManager />` optional `basePath` + `hiddenNames` props (no behaviour change without them)
- [x] `resumesApi.ts` client
- [x] Sidebar `Resumes` item (FAMILY/ADMIN) + four routes in `router.tsx`
- [x] Countries page, companies page, vacancies page (status filter), vacancy leaf page
- [x] Shared node-list component (add/rename/delete dialogs) used by all three tree levels
- [x] Status editor modal
- [x] Error strings for the new codes

### T5 — Tests

- [x] STRANGER → `403`; FAMILY/ADMIN → `200` on every resumes route
- [x] First `GET /statuses` seeds four statuses; second GET does not reseed or wipe
- [x] Create/rename/delete at country, company and vacancy level
- [x] Duplicate sibling name → `409`; invalid and reserved names → `400`; depth-4 create → `400`
- [x] Vacancy `meta.yaml` PUT/GET round-trip; URL scheme normalisation; empty URL allowed
- [x] Duplicate/empty field name → `400`; field order preserved
- [x] Status delete detaches (vacancy still reads, status resolves to null)
- [x] Invalid `meta.yaml` / `statuses.yaml` → `409`, file not overwritten
- [x] Repeated meta save keeps exactly one `FileRecord` and charges the size delta
- [x] Recursive delete removes records and decrements quota
- [x] `/api/resumes/files` upload/list/delete round-trip under a vacancy

### T6 — Flat vacancy list (US-RES-08)

- [x] `ResumeService.list_all_vacancies` + shared `_list_child_dirs` helper (reused by `list_nodes`)
- [x] `GET /api/resumes/vacancies` + `VacancyListItem` / `VacancyListResponse` schemas
- [x] `AllVacanciesPage` at top-level `/vacancies` inside `ResumesGuard`
- [x] Entry point on the countries page + back link
- [x] API Contract §9, Flow Spec, Data Models, BACKEND_DOCS, FRONTEND_DOCS, Master Document
- [x] Tests: role gate, empty tree, ordering across countries/companies, empty branches skipped, URL normalisation, corrupt meta degradation

### T7 — Status filter moved to All vacancies

- [x] `VacancyStatusFilter` component: multi-select + Show only / Hide modes + Clear + match count
- [x] Filter wired into `AllVacanciesPage` (dangling `status_id` treated as "No status")
- [x] Filter removed from `ResumeCompanyPage`; `filterItem` prop dropped from `ResumeTreeSection`
- [x] "All vacancies" link added to the company page toolbar
- [x] Flow Spec / FRONTEND_DOCS / epic D10 + US-RES-08 updated
