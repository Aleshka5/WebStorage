# Architectural Decision Records — Index

ADRs capture **irreversible or high-impact** decisions. Template: [adr-template.md](./adr-template.md).

| ID | Title | Status | Date |
|---|---|---|---|
| [ADR-001](./ADR-001-clean-architecture.md) | Clean Architecture layering for backend | Accepted | 2026-06 |
| [ADR-002](./ADR-002-storage-adapter-decorator.md) | Plain + Encrypted storage adapter decorator | Accepted | 2026-06 |
| [ADR-003](./ADR-003-jwt-httponly-cookie.md) | JWT in httpOnly cookie (not Bearer header) | Accepted | 2026-06 |
| [ADR-004](./ADR-004-private-key-redis-ttl.md) | Private encryption key in Redis with separate TTL | Accepted | 2026-06 |
| [ADR-005](./ADR-005-disk-router-most-free.md) | DiskRouter selects disk by most free space | Accepted | 2026-06 |
| [ADR-006](./ADR-006-metadata-db-blobs-fs.md) | Metadata in PostgreSQL; blobs on filesystem | Accepted | 2026-06 |

## When to add an ADR

- Changing auth model, encryption, or quota semantics.
- Introducing a new storage backend or breaking API rename.
- Deviating from TZ / Master Document intentionally.
