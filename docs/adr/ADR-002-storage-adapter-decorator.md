# ADR-002: Encrypted storage adapter decorator

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** TZ §6.4, `StorageAdapter`, `S3StorageAdapter`, `EncryptedStorageAdapter`, FE `FileManager`

## Context

Files and Private sections share the same UX (list/upload/mkdir/…). Duplicating managers would violate DRY and diverge behavior.

## Decision

- Backend: `EncryptedStorageAdapter` decorates any `StorageAdapter` (AES-256-GCM + encrypted names). The inner adapter is `S3StorageAdapter` (MinIO).
- `FileService` depends on the adapter abstraction, not encryption details.
- Frontend: one `FileManager` with `mode: 'plain' | 'encrypted'` and `apiPrefix`.

## Consequences

### Positive

- Shared and Files reuse the plain (unencrypted) S3 path; Private swaps adapter/prefix only.
- Encryption bugs stay localized to one adapter.

### Negative / Trade-offs

- Debugging encrypted filenames requires unlock session.
- Object-store tools cannot inspect private trees meaningfully without the session key.

## Alternatives Considered

| Option | Why not |
|---|---|
| Separate PrivateFileService clone | Drift risk |
| Client-side-only encryption | Breaks server thumbnails/search future; harder multi-device |
