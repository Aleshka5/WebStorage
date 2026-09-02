# HomeCloud

Self-hosted network storage. TLS and login live in front (host Caddy → Auth Gateway). This repo is the storage app: API + built SPA on `app:8000`.

## Requirements

- Podman (`podman compose`) or Docker Engine + Compose v2
- Node.js 20+ only if you run the Vite frontend locally
- **Common already up** (Postgres, Redis, MinIO on `common_network`)
- User-Service on `user_network`
- Auth-Service / gateway on `auth_network` (default name `deploy_auth`)

Operator order: Common → User-Service → Auth → WebStorage.

## One-time Common init

Do **not** start Common from this compose. Do **not** touch the `mlflow` bucket.

```bash
# Postgres (exec on the Common postgres container)
psql -U postgres -c "CREATE USER storage WITH PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE db_storage OWNER storage;"

# MinIO bucket
mc alias set local http://minio:9000 minioadmin minioadmin
mc mb --ignore-existing local/storage
```

Put the password in WebStorage `.env` as part of `DATABASE_URL`. Do not commit secrets.

## Configure and run

```bash
cp .env.example .env
# Set DATABASE_URL (storage@postgres/db_storage), S3_* (Common MinIO), AUTH_DOCKER_NETWORK if needed
podman compose up --build -d
```

`app` joins `common_network`, `user_network`, and `auth_network`. The gateway proxies to `http://app:8000`. This compose does not publish `:8000` as a second public edge.

On every `app` start, `alembic upgrade head` runs. Schema lives only in `db_storage`.

Production image serves the API **and** the built SPA from `/opt/spa` so one upstream works. The `frontend` compose service is local Vite only.

```bash
cd frontend
npm install
npm run dev
```

There is no in-app login or logout. Open the site through the hub. Identity headers come from the gateway (`X-User-Id`, `X-Storage-Role`). Hub `X-Auth-Role` is ignored.

## Environment

See `.env.example`. Names used by the app:

| Variable | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://storage:<password>@postgres:5432/db_storage` | Common Postgres |
| `REDIS_URL` | `redis://redis:6379` | Common Redis (vault `private_key:{auth_session}`) |
| `S3_ENDPOINT_URL` | `http://minio:9000` | Common MinIO |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` | Common MinIO root unless you set a dedicated key |
| `S3_BUCKET` | `storage` | Single bucket. Object keys: `users/{id}/…`, `shared/…`, `_meta/backups/` |
| `USER_SERVICE_URL` | `http://user_service:8000` | Directory; network trust only |
| `AUTH_COOKIE_NAME` | `auth_session` | Vault session id only |
| `AUTH_DOCKER_NETWORK` | `deploy_auth` | External network so the gateway can resolve `app` |

## Storage

One logical volume → bucket `storage`. Backups are objects `_meta/backups/` in that bucket. Do not create or touch `mlflow`. Old local `db` / `redis` / `minio` services and host bind mounts are gone; wipe previous data.

## Auth

- Public: `GET /health`
- Authenticated APIs: gateway headers. Missing user id → `401 UNAUTHORIZED`. Role lookup failure → `503 USER_SERVICE_UNAVAILABLE`. `BLOCKED` → `403 ACCESS_DENIED`.
- `GET /api/auth/me` → `{ user_id, email, role }` (storage role).
- `POST /api/auth/logout` → `410`. Do not clear cookies here.
- Admin list: `GET /api/admin/users` calls User-Service `GET /users` and joins local quota.

## Restoring from backup

Dumps are created daily at 02:00 (and via admin API) as `_meta/backups/db_backup_*.sql.zst` in bucket `storage`.

```bash
curl -b cookies.txt https://storage.filenkov.store/api/admin/backup/list
curl -b cookies.txt https://storage.filenkov.store/api/admin/backup/run
```

Restore into Common Postgres (`db_storage` as role `storage`) after stopping `app`. Backups older than 30 days are deleted on each new backup.

## Useful commands

```bash
podman compose ps
podman compose logs -f app
podman compose down
```

## Project structure

- `backend/` — FastAPI application (API + optional built SPA)
- `frontend/` — Vite SPA (local; production build is copied into the app image)
- `.env.example` — environment variable template
- `docs/epics/gateway-user-service/` — current identity / infra epic
