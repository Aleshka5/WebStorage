# E-KEYS implementation tasks

Source of truth: [init.md](./init.md).

### T1 — Specs

- [x] Epic `init.md` + this `tasks.md`
- [x] API Contract: `GET`/`PUT /api/private/keys` + error codes
- [x] Flow Spec: Keys Registry unlock + list/add/delete/copy/save
- [x] FRONTEND_DOCS: route, sidebar, page
- [x] BACKEND_DOCS: service, bootstrap, in-place YAML write
- [x] Test Spec: new cases
- [x] Epics index row

### T2 — Backend

- [x] PyYAML via `uv`
- [x] Error codes + domain exceptions + handlers
- [x] `FileService.overwrite_file` (update-in-place, quota delta)
- [x] `KeysRegistryService` (bootstrap, parse, validate, save)
- [x] `GET`/`PUT /api/private/keys` on the private router
- [x] Structured loguru logs without secret values

### T3 — Frontend

- [x] Sidebar root item + `/keys` route
- [x] `KeysRegistryPage` (unlock gate, list, add, delete, copy, save)
- [x] API client + error-message strings

### T4 — Tests

- [x] GET without unlock → `401 PRIVATE_SESSION_EXPIRED`
- [x] GET after unlock creates `Keys/` + empty `keys.yaml`
- [x] PUT/GET round-trip
- [x] Empty name/value and duplicate names
- [x] Invalid YAML → error, GET does not overwrite
- [x] Overwrite does not create a second FileRecord
- [x] Second GET does not wipe existing keys
