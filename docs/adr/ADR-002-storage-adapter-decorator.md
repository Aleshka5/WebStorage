# ADR-002: Plain + Encrypted storage adapter decorator

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** TZ §6.4, `PlainStorageAdapter`, `EncryptedStorageAdapter`, FE `FileManager`

## Context

Files and Private sections share the same UX (list/upload/mkdir/…). Duplicating managers would violate DRY and diverge behavior.

## Decision

- Backend: `EncryptedStorageAdapter` decorates `PlainStorageAdapter` (AES-256-GCM + encrypted names).
- `FileService` depends on the adapter abstraction, not encryption details.
- Frontend: one `FileManager` with `mode: 'plain' | 'encrypted'` and `apiPrefix`.

## Consequences

### Positive

- Shared and Files reuse plain path; Private swaps adapter/prefix only.
- Encryption bugs stay localized to one adapter.

### Negative / Trade-offs

- Debugging encrypted filenames requires unlock session.
- Some FS-level tools cannot inspect private trees meaningfully.

## Alternatives Considered

| Option | Why not |
|---|---|
| Separate PrivateFileService clone | Drift risk |
| Client-side-only encryption | Breaks server thumbnails/search future; harder multi-device |
