# Product Brief — HomeCloud

> **Status:** Active  
> **Audience:** Product + engineering  
> **Related:** [Master Document.md](./Master%20Document.md)

---

## 1. Problem

Households and small groups need a private place for photos and documents without relying solely on commercial cloud providers. Typical pain points:

- Photos and files scattered across phones and PCs.
- Sensitive documents should stay encrypted and inaccessible without a passphrase.
- Family members need a shared space; guests need tightly limited quotas.
- Self-hosting must be deployable on a home PC via Docker without complex ops.

---

## 2. Solution

**HomeCloud** is a self-hosted network storage appliance with a web UI:

- Personal **Files** and **Photos** per user.
- **Private** section with AES-256-GCM encryption driven by a user passphrase (never stored in DB).
- **Shared** folder for FAMILY and ADMIN.
- **Admin** panel for roles, private quotas, blocking, and storage health.
- MinIO object storage (S3 API) with metadata in PostgreSQL and sessions in Redis.

Deploy model: Docker Compose (app + PostgreSQL + Redis + MinIO + frontend); optional Kubernetes manifests in repo.

---

## 3. Target Users

| Persona | Needs |
|---|---|
| Home owner / ADMIN | Full control, user management, disk overview, backups |
| Family member (FAMILY) | Generous storage, shared folder, private vault |
| Guest / stranger (STRANGER) | Small hard quota, own files/photos/private only |

---

## 4. Goals

1. Reliable self-hosted file & photo management over HTTPS-capable deploy.
2. Clear role model with enforceable quotas.
3. Encrypted private vault with session-scoped keys and short TTL.
4. Extensible bucket layout without migrating existing objects.
5. Clean Architecture codebase ready for Spec Driven iteration.

---

## 5. Non-Goals (v1 / current)

- Public multi-tenant SaaS or billing.
- Real-time collaboration (OT/CRDT editors).
- Mobile native apps (responsive web only).
- Full-text / semantic search of file contents (endpoint reserved, `501`).
- Chunked/resumable TUS uploads (schema reserved).
- End-to-end client-side encryption for non-private sections.
- Password reset via email link (TZ item; not shipped).
- Guaranteed zero-downtime multi-node HA.

---

## 6. Success Criteria

| Criterion | Signal |
|---|---|
| Usable home deploy | `docker compose up` + MinIO bucket init + login as ADMIN |
| Role isolation | STRANGER cannot hit `/api/shared` or `/api/admin` |
| Quota enforcement | Uploads fail with `QUOTA_EXCEEDED` when over limit |
| Private safety | Wrong passphrase rejected; key absent after lock/TTL |
| Photo UX | First batch loads with thumbnails; lightbox shows original |
| Ops | Daily DB backup + archive job run without manual steps |

---

## 7. Constraints

- Python 3.12+, FastAPI, React+TS, PostgreSQL, Redis, MinIO.
- Config only through `pydantic-settings` / `get_settings()`.
- Package manager: **uv** (backend); frontend uses npm/pnpm tooling in `frontend/`.
- Logging via loguru; structured JSON; no sensitive payloads in logs.
- Prefer extending adapters/services over breaking public API contracts.

---

## 8. Out of Scope UI Polish (unless requested)

Marketing landing pages, multi-theme design systems, offline PWA, internationalization beyond existing locale hooks.

---

## 9. Open Product Decisions

| Topic | Current stance |
|---|---|
| Unblock after block | Not implemented — backlog |
| Password reset | Not implemented — backlog |
| TLS termination | External (Caddy/ingress) preferred over app-level |
| Search | Reserved stub until epic prioritizes it |
