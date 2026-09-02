# Epics Index — HomeCloud

> Spec Driven Development backlog mapped to product areas.  
> Each epic folder has `init.md` with overview, stories, and Definition of Done.

| Epic | Folder | Status | Priority |
|---|---|---|---|
| Authentication & Sessions | [authentication](./authentication/init.md) | Implemented locally; **session/roles → [E-AUTHZ](./auth-service-roles/init.md)** | P0 |
| File Management | [file-management](./file-management/init.md) | Implemented (search stub) | P0 |
| Photos | [photos](./photos/init.md) | Implemented | P0 |
| Private Encrypted Storage | [private-storage](./private-storage/init.md) | Implemented | P0 |
| Keys Registry | [keys-registry](./keys-registry/init.md) | Implemented | P1 |
| Shared Folder | [shared-storage](./shared-storage/init.md) | Implemented | P1 |
| Admin Panel | [admin-panel](./admin-panel/init.md) | Partial; **role writes → E-AUTHZ** | P1 |
| Storage Infrastructure | [storage-infrastructure](./storage-infrastructure/init.md) | Implemented (strategy variants pending) | P1 |
| Archiving & Backup | [archiving-backup](./archiving-backup/init.md) | Backend implemented; FE ops limited | P2 |
| Move FS → S3 MinIO | [s3-minio-migration](./s3-minio-migration/init.md) | Implemented (1 TiB capacity fallback; no HTTP ASGI e2e) | P1 |
| Auth-Service roles via gRPC | [auth-service-roles](./auth-service-roles/init.md) | **Superseded by [E-GWUS](./gateway-user-service/init.md)** (gRPC deleted) | — |
| Gateway headers + User-Service + Common | [gateway-user-service](./gateway-user-service/init.md) | Implemented | P0 |

## How to work an epic

1. Read epic `init.md` + linked Master/API/Flow specs.
2. Split stories into PRs that keep API contract green.
3. Update docs when behavior changes; add ADR for intentional TZ deviations.
4. Meet epic DoD before marking complete.

## Status legend

- **Implemented** — core stories available in current codebase.
- **Partial** — usable but missing TZ items.
- **Planned** — not started.
