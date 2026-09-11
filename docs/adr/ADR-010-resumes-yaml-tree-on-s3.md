# ADR-010 — Resumes tree as S3 directories + YAML metadata

> **Status:** Accepted  
> **Date:** 2026-09-02  
> **Epic:** [E-RESUMES](../epics/resumes/init.md)  
> **Supersedes:** —

## Context

The Resumes feature needs a four-level hierarchy — country → company → vacancy → leaf — where the
leaf holds a website URL, an ordered list of key/value fields, and a set of attached files. The
product constraint was explicit: build on the existing S3 storage stack and reuse the existing file
operations and interfaces wherever possible.

Three shapes were considered:

1. **S3 directories + YAML metadata.** Levels are real object prefixes; per-vacancy `meta.yaml` and a
   per-user `statuses.yaml` are written through `FileService.overwrite_file`.
2. **Relational tables.** `resume_country` / `resume_company` / `resume_vacancy` / `resume_status`
   in Postgres; S3 only for attachments.
3. **Hybrid.** Tree in S3, status list in Postgres for referential integrity.

The codebase already contains a working precedent for shape 1:
[`KeysRegistryService`](../../backend/app/application/keys_registry_service.py) stores an entire
registry as one YAML file inside the user's vault, bootstrapping the folder and file on first read
and charging quota as a size delta on overwrite.

## Decision

Adopt **shape 1**. The Resumes hierarchy is a directory tree under a new section prefix
`users/{user_id}/resumes`, and all metadata is YAML written through the existing `FileService`.

- New `FileSection.RESUMES` (Alembic `006` adds the value to the `file_section` native enum) so
  attachments get ordinary `file_records` rows, quota accounting and archive eligibility.
- `ResumeService` sits in the application layer over an injected `FileService`; it owns YAML
  parse/serialise/validate and nothing else. It creates no repositories and touches no adapter
  directly.
- Directory create / rename / recursive delete reuse `create_directory`, `rename_by_path` and a new
  `delete_directory_recursive`.
- Vacancy attachments are served by a file router built from the same handler set as `/api/files`,
  mounted at `/api/resumes/files` with a `RESUMES`-scoped `FileService`, so the frontend reuses
  `<FileManager />` verbatim.

## Consequences

**Positive**

- Zero new tables and zero new repositories; one enum migration.
- The whole tree is plain objects — visible in MinIO, covered by existing backup/restore, and
  portable without a database.
- `meta.yaml` overwrite-in-place reuses the E-KEYS quota-delta path, so repeated saves never
  accumulate `file_records` rows.
- The leaf file area is the production `FileManager` with the production endpoints, not a parallel
  implementation.

**Negative**

- Listing vacancies costs one S3 listing plus one `meta.yaml` read per vacancy. Acceptable at the
  expected scale (tens of vacancies per company); if it stops being acceptable, a per-company index
  file or a projection table is the escape hatch.
- No referential integrity for `status_id`. This is accepted deliberately: deleting a status
  detaches it, and dangling ids render as "No status" (E-RESUMES D6).
- No cross-tree server-side query. Status filtering is client-side within one company (D10).
- Concurrent saves to the same `meta.yaml` are last-write-wins, as with `keys.yaml` (D14).

## Alternatives rejected

- **Relational tables** — better querying and integrity, but it duplicates a hierarchy the object
  store already expresses, adds a migration plus four repositories, and contradicts the reuse
  constraint. Revisit only if cross-tree querying or sharing becomes a requirement.
- **Hybrid** — splits one feature's state across two stores for the sake of a list that is edited
  rarely and read once per page, and still costs a migration.
