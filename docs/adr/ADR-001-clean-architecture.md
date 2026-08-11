# ADR-001: Clean Architecture layering for backend

- **Status:** Accepted
- **Date:** 2026-06-01
- **Related:** TZ §6, `backend/app/{domain,application,infrastructure,presentation}`

## Context

HomeCloud must stay maintainable while growing features (private crypto, multi-disk, admin ops). Mixing HTTP, SQL, and FS logic would block testing and Spec Driven iteration.

## Decision

Organize the backend into four layers with a strict dependency rule:

- **Domain** — entities, enums, exceptions (no framework imports).
- **Application** — services/use cases orchestrating domain + ports.
- **Infrastructure** — SQLAlchemy, Redis, FS adapters, schedulers.
- **Presentation** — FastAPI routers, schemas, dependencies.

Configuration is centralized in `Settings` / `get_settings()` (pydantic-settings).

## Consequences

### Positive

- Business rules testable without ASGI.
- Storage encryption can wrap adapters without changing `FileService` API.
- Clear home for new epics.

### Negative / Trade-offs

- More files/boilerplate than a flat FastAPI app.
- Contributors must learn layer boundaries.

## Alternatives Considered

| Option | Why not |
|---|---|
| Layered MVC in routers | Hard to reuse File vs Private |
| Hexagonal with many interfaces | Overkill vs TZ KISS |
