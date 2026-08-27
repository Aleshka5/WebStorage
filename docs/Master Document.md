# HomeCloud — Master Design Document

> **Status:** Active  
> **Version:** 1.0  
> **Product:** HomeCloud (WebStorage)  
> **Type:** Self-hosted personal network storage  
> **Source TZ:** [`ТЗ_v1.1_Сетевое_Хранилище.md`](../ТЗ_v1.1_Сетевое_Хранилище.md)

This document is the **single source of truth** for Spec Driven Development. All other specs refine details; when they conflict, this Master Document and the TZ win until an ADR records an intentional deviation.

---

## 1. Purpose

HomeCloud is a Docker-deployable home cloud that gives a family (and invited strangers) a web UI for photos, files, encrypted private storage, and a shared folder — with role-based quotas and admin controls.

---

## 2. Spec Map

| Document | Role |
|---|---|
| [Product Brief.md](./Product%20Brief.md) | Problem, solution, goals, non-goals |
| [Design Spec.md](./Design%20Spec.md) | Architecture, modules, adapters, integrations |
| [API Contract.md](./API%20Contract.md) | REST endpoints, schemas, errors |
| [Data Models.md](./Data%20Models.md) | Domain & persistence models |
| [Flow Spec.md](./Flow%20Spec.md) | User flows, UI states, interactions |
| [Test Spec.md](./Test%20Spec.md) | Backend + frontend test strategy |
| [adr/](./adr/index.md) | Architectural Decision Records |
| [epics/](./epics/init.md) | Epics and user stories |
| [BACKEND_DOCS.md](./BACKEND_DOCS.md) | Implementation reference (backend) |
| [FRONTEND_DOCS.md](./FRONTEND_DOCS.md) | Implementation reference (frontend) |

---

## 3. Product Snapshot

| Item | Value |
|---|---|
| Name | HomeCloud |
| Backend | Python 3.12 + FastAPI (Clean Architecture) |
| Frontend | React 18 + TypeScript + Vite + Tailwind |
| Data | PostgreSQL 16 (metadata), filesystem (blobs), Redis (sessions/cache) |
| Auth | Email/password + Google OAuth; JWT in httpOnly cookie. **Planned (E-AUTHZ):** Google via Auth-Service; cookie `auth_session`; roles from gRPC `storage_roles`. |
| Roles | `STRANGER`, `FAMILY`, `ADMIN` |
| Sections | Photos, Files, Private (encrypted), Shared, Admin |
| Deploy | Docker Compose; optional Kubernetes |

---

## 4. Role Matrix (authoritative)

| Capability | STRANGER | FAMILY | ADMIN |
|---|:---:|:---:|:---:|
| Register / login / OAuth | ✅ | ✅ | ✅ |
| Own photos / files / private | ✅ | ✅ | ✅ |
| Shared folder | ❌ | ✅ | ✅ |
| Admin panel | ❌ | ❌ | ✅ |
| Change roles / private quotas | ❌ | ❌ | ✅ (roles: Auth-Service after E-AUTHZ; private quota stays HomeCloud) |

**Quotas**

- **All roles:** total limit = per-user `limit_bytes` (default `DEFAULT_USER_QUOTA_MB` = 100); admin can raise/lower per user; `0` = unlimited. Private sublimit from admin (`private_limit_bytes`; `0` = no sublimit). User quota is not free disk / MinIO capacity.

---

## 5. UI Surface (authoritative)

| Route | Audience | Backend prefix |
|---|---|---|
| `/auth` | Guests | `/api/auth` |
| `/photos` | All authenticated | `/api/photos` |
| `/files` | All authenticated | `/api/files` |
| `/private` | All authenticated (+ unlock) | `/api/private` |
| `/shared` | FAMILY, ADMIN | `/api/shared` |
| `/admin` | ADMIN | `/api/admin` |

Sidebar shows Shared only for FAMILY/ADMIN and Admin only for ADMIN. Quota bar lives in expanded sidebar (`GET /api/quota/me`).

---

## 6. Architecture Principles

1. **Clean Architecture** — Domain ← Application ← Infrastructure / Presentation; no upward deps.
2. **SOLID / DRY / KISS / YAGNI** — one responsibility per module; shared `FileManager` + storage adapters.
3. **Config** — only via `get_settings()` (`pydantic-settings`); keep `env.example` / `.env.example` in sync.
4. **Logging** — loguru structured JSON; never log passwords, passphrases, or encryption keys.
5. **Security** — path-traversal guards; rate limits on auth/unlock; private key only in Redis with short TTL.

Key reuse pattern: `FileService` + `PlainStorageAdapter` / `EncryptedStorageAdapter`; frontend `<FileManager mode apiPrefix />`.

---

## 7. Storage Layout

```
{STORAGE_ROOT}/{disk_id}/
├── users/{user_id}/
│   ├── photos/{originals,previews}/
│   ├── files/
│   └── private/          ← AES-256-GCM; .marker for key validation
├── shared/
└── _meta/backups/        ← DB dumps (first disk)
```

Metadata lives in PostgreSQL (`users`, `file_records`, `user_quota_usage`). Blob bytes live on disk. Disk selection for writes: most free space among healthy disks with ≥ `MIN_FREE_SPACE_MB`.

---

## 8. Cross-Cutting Behaviors

| Concern | Behavior |
|---|---|
| Auth session | JWT HS256 in cookie `access_token`; TTL `SESSION_TTL_SECONDS`. **Planned (E-AUTHZ):** Auth-Service Redis session cookie `auth_session`; Validate on every request; TTL = Auth-Service `SESSION_TTL`. |
| Private session | Derived AES key in Redis; TTL `PRIVATE_SESSION_TTL_HOURS` (sliding); expired → `PRIVATE_SESSION_EXPIRED` without full logout |
| Upload lifecycle | `PENDING` → commit → `COMMITTED`; stale PENDING (>1h) cleaned by job |
| Archiving | Daily zstd for files idle > `ARCHIVE_DAYS_THRESHOLD` days; transparent read |
| Backups | Daily `pg_dump` + zstd; 30-day retention |
| Errors | `{ "detail": { "error_code", "message", ... } }` |

---

## 9. Implemented vs TZ Gaps

Treat as backlog unless an ADR says otherwise.

| Item | Status |
|---|---|
| Password reset (temp link) | Not implemented |
| Unblock user | Block only (`is_active=false`) |
| HTTPS in default compose | Compose exposes HTTP; TLS via Caddy/K8s optional |
| Client Zod validation | Custom validators, no Zod |
| File search | `501 NOT_IMPLEMENTED` |
| `DISK_STRATEGY` variants | Env present; runtime uses most-free-space |
| UploadSession / TUS | Table reserved; unused |
| ZIP upload / folder download | Implemented (beyond TZ) |
| Private reset after unlock lockout | Implemented (beyond TZ) |
| Admin backup/archive/maintenance APIs | Backend yes; limited/no FE UI |

---

## 10. Definition of Done (product-level)

A change is ready when:

1. Behavior matches this Master Document + relevant epic DoD.
2. API/Data/Flow specs updated if contracts or UX change.
3. New/changed env vars appear in `.env.example` and Settings.
4. Logging added for new business paths (loguru levels per project rules).
5. Role and quota rules preserved unless ADR documents a change.
6. No secrets committed; no passphrase/key logging.

---

## 11. How to Use This Docs Set (SDD)

1. Start from **Product Brief** (why) → **epic** (what slice).
2. Implement against **Design Spec** + **API Contract** + **Data Models**.
3. Verify UX with **Flow Spec**; coverage with **Test Spec**.
4. Record irreversible choices in **adr/**.
5. Keep **Master Document** and TZ aligned; prefer ADR over silent drift.

---

## 12. Related Entry Points

| Path | Notes |
|---|---|
| `backend/main.py` | FastAPI app, routers, schedulers |
| `backend/config.py` | Settings hierarchy |
| `frontend/src/router.tsx` | SPA routes and guards |
| `docker-compose.yml` | Local/prod-like stack |
| `.env.example` | Env template |
