# HomeCloud

Self-hosted network storage with a web interface. Deployed via Docker.

## Requirements

- Docker Desktop (or Docker Engine + Docker Compose v2)
- Node.js 20+ (for local frontend execution only)
- Git

## Quick Start

### 1. Clone the repository

```bash
git clone <repository-url> WebStorage
cd WebStorage
```

### 2. Configure the environment

```bash
cp .env.example .env
```

In `.env`, you must set:

- `JWT_SECRET` — a random string (e.g., `openssl rand -hex 32`)
- `POSTGRES_PASSWORD` — PostgreSQL password (and update `DATABASE_URL` if you changed it)
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` — credentials for the first administrator

### 3. Run the backend

```bash
docker compose up --build -d
```

Verification: [http://localhost:8000](http://localhost:8000) — responds with `{"status": "ok", "version": "1.0"}`.

On every `app` container startup, `alembic upgrade head` is automatically executed.

### 4. Initialize storage and administrator

```bash
docker compose exec app python scripts/init_storage.py
docker compose exec app python scripts/init_db.py
```

`init_storage.py` creates the folder structure on disk (`users/`, `shared/`, `_meta/backups/`).
`init_db.py` creates the first user with the `ADMIN` role using `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

### 5. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

```bash
docker run -p 5173:5173 --add-host=host.docker.internal:host-gateway container_name
```

Interface: [http://localhost:5173](http://localhost:5173). Log in with administrator email and password.

---

## Creating the first administrator

The administrator is created once by the `init_db.py` script:

```bash
docker compose exec app python scripts/init_db.py
```

Before running, set these in `.env`:

| Variable | Description |
|---|---|
| `ADMIN_EMAIL` | Administrator email |
| `ADMIN_PASSWORD` | Administrator password |

If a user with such an email already exists, the script will finish without changes. After creation, log in through the frontend login form or via API `POST /api/auth/login`.

---

## Adding a new disk

1. Create a folder on the host, e.g., `./storage/disk2`.
2. Add a volume in `docker-compose.yml`:
   ```yaml
   - ./storage/disk2:/storage/disk2
   ```
3. In `.env`, specify `STORAGE_DISKS=disk1,disk2`.
4. Restart the containers:
   ```bash
   docker compose down
   docker compose up -d
   ```
5. Initialize the structure on the new disk:
   ```bash
   docker compose exec app python scripts/init_storage.py
   ```

Existing files remain on their respective disks; new entries are distributed based on available space (`DiskRouter`).

Metadata DB backups are saved on the first disk in `STORAGE_DISKS` (default `disk1`) at `_meta/backups/`.

---

## MinIO object storage

`docker compose` starts [MinIO](https://min.io/) alongside the app for the S3 migration epic. The filesystem mount on `app` remains; keep `STORAGE_BACKEND=fs` until the S3 adapter and data migration are ready.

### Start / console

```bash
docker compose up -d minio minio-init
# or the full stack:
docker compose up --build -d
```

| Endpoint | URL (host bind matches other services) |
|---|---|
| S3 API | `http://192.168.1.103:9000` (in-compose: `http://minio:9000`) |
| Web console | `http://192.168.1.103:9001` |

Log into the console with `S3_ACCESS_KEY` / `S3_SECRET_KEY` from `.env` (same values as MinIO root user/password).

### Environment variables

See `.env.example`. Names used by compose and (later) the app:

| Variable | Example | Description |
|---|---|---|
| `STORAGE_BACKEND` | `fs` | `fs` (default) or `s3` when the adapter is enabled |
| `S3_ENDPOINT_URL` | `http://minio:9000` | API URL from the app container |
| `S3_ACCESS_KEY` | `minioadmin` | Access key (= MinIO root user) |
| `S3_SECRET_KEY` | `minioadmin` | Secret key (= MinIO root password) |
| `S3_REGION` | `us-east-1` | Region (MinIO accepts any) |
| `S3_USE_SSL` | `false` | TLS to the endpoint |
| `S3_PATH_STYLE` | `true` | Path-style URLs (required for MinIO in Docker) |

Object **keys stay isomorphic** to today’s relative paths under `{STORAGE_ROOT}/{disk_id}/…` (e.g. `users/{user_id}/files/…`). Buckets map 1:1 to `STORAGE_DISKS` entries (`disk1`, …); DB dumps use a separate `backups` bucket.

### Bucket bootstrap

On each stack start, `minio-init` (MinIO Client `mc`) creates buckets idempotently:

- one bucket per name in `STORAGE_DISKS` (comma-separated);
- `backups` for metadata DB dumps (replaces `{disk}/_meta/backups/` when on S3).

Safe to re-run; existing buckets are left untouched (`mc mb --ignore-existing`).

### Persistence and capacity

- Data lives in the named Docker volume `minio_data` (see `docker-compose.yml`).
- **Expand capacity:** grow the host disk that backs Docker volumes, or migrate the volume to a larger disk (`docker volume` inspect → copy `/var/lib/docker/volumes/…`). Adding another logical “disk” means create a new MinIO bucket (add the name to `STORAGE_DISKS`, restart so `minio-init` creates it) — same sticky `disk_id` idea as FS mode.
- **FS-only:** comment out `minio`, `minio-init`, and `app.depends_on.minio` in `docker-compose.yml`.

### Migrate existing FS blobs to MinIO (US-S3-08)

One-shot / resumable tool: walks `{STORAGE_ROOT}/{disk_id}/…`, uploads objects with keys matching disk-relative paths into bucket `{S3_BUCKET_PREFIX}{disk_id}`, and checks `file_records.checksum_sha256` where present.

**Prerequisites**

1. MinIO up and buckets created (`docker compose up -d minio minio-init`).
2. Backup PostgreSQL (and keep FS trees until verify passes).
3. Keep `STORAGE_BACKEND=fs` until migration + verify succeed.
4. `.env` has correct `STORAGE_ROOT`, `STORAGE_DISKS`, and `S3_*` (via `get_settings()`).

**Commands** (from `backend/`, or `docker compose exec app python scripts/migrate_fs_to_s3.py …`):

```bash
cd backend

# Plan only (no S3 writes)
uv run python scripts/migrate_fs_to_s3.py --dry-run

# Upload (optional: --disk disk1); re-run skips same-size objects
uv run python scripts/migrate_fs_to_s3.py
uv run python scripts/migrate_fs_to_s3.py --disk disk1

# Verify S3 contents / DB checksums (no upload)
uv run python scripts/migrate_fs_to_s3.py --verify-only
```

**After a clean verify:** set `STORAGE_BACKEND=s3` in `.env` and restart the app. See also `python scripts/migrate_fs_to_s3.py --help`.

---

## Auth-Service gRPC

HomeCloud authenticates via the Auth hub (Google OAuth2, cookie `auth_session`) and calls gRPC `Validate` on every protected request. gRPC is Docker-internal only: `app` joins the Auth-Service `auth` network and dials `AUTH_GRPC_ADDR=api:9090`. Do not publish port 9090 on the host.

`AUTH_CALLER_HOST` is the **whitelist identity** sent inside `Validate` (after scheme/port/path strip). It is not the gRPC dial target. A log line with `caller_host=storage.filenkov.store grpc_code=UNAVAILABLE` means the channel to `AUTH_GRPC_ADDR` is down, not that the app tried to connect to that public hostname.

### Environment variables

See `.env.example`. JWT/Google vars stay until later stories retire local product sessions.

| Variable | Default | Description |
|---|---|---|
| `AUTH_GRPC_ADDR` | `api:9090` | Dial target from the `app` container (Auth-Service compose DNS) |
| `AUTH_CALLER_HOST` | `storage.filenkov.store` | Must match the Auth-Service whitelist **after** scheme/port/path strip |
| `AUTH_COOKIE_NAME` | `auth_session` | Must match Auth-Service `COOKIE_NAME` |
| `AUTH_GRPC_TIMEOUT_MS` | `2000` | Per-request gRPC deadline |
| `AUTH_LOGIN_URL` | hub Google OAuth with `return_to=https://storage.filenkov.store/` | Unauthenticated browser redirect; `return_to` host must be on the hub whitelist |
| `AUTH_LOGOUT_URL` | `http://api:8080` | LAN origin of Auth-Service HTTP for a later BFF logout |
| `AUTH_DOCKER_NETWORK` | `deploy_auth` | External compose network `{project}_auth`. From `Auth-Service/deploy` → `deploy_auth`; from the Auth-Service repo root → `auth-service_auth`. Confirm with `podman network ls` / `docker network ls`. |

### Cookie, network, Validate

- Cookie **domain** in production is `.filenkov.store` so `storage.filenkov.store` receives `auth_session`.
- gRPC is **internal only**. Auth-Service compose `expose`s `9090` but does not publish it (`0.0.0.0:8082->8080/tcp, 9090/tcp` means HTTP is on the host; gRPC is not). `app` attaches to `AUTH_DOCKER_NETWORK` so DNS name `api` resolves. Recreate `app` after changing networks (`podman compose up -d app` / `docker compose up -d app`).
- **No Validate cache** in WebStorage: session revoke / role change / block must take effect on the next request.
- Login `return_to` must be an exact whitelist host (`storage.filenkov.store` in prod).

### DEV caveat (localhost whitelist)

With Auth-Service `AUTH_DEV_HTTP=true`, `localhost` / `127.0.0.1` are copied from **hub** fields (`google_email`, `name`, `roles`), **not** storage fields (`id`, `storage_roles`). Local Validate as `AUTH_CALLER_HOST=localhost` will not get FAMILY/`id`. Either:

- keep `AUTH_CALLER_HOST=storage.filenkov.store` (and send that host / Caddy `Host` header), or
- set Auth-Service `WHITELIST_JSON` so `localhost` projects storage fields (`id`, `google_email`, `name`, `storage_roles`).

### Migrate HomeCloud users to Auth-Service UUIDs

One-shot operator tool (US-AUTHZ-11 / ADR-008): match local `users.email` to Auth `google_email` and rewrite the local UUID so `users/{user_id}/` prefixes, `file_records`, `user_quota_usage`, and `upload_sessions` stay valid.

Password-only users with no Google email in the Auth export are **reported and left unmatched** — they cannot silent-login after cutover. Local `role` is **not** copied into Auth-Service; set `storage_roles` in hub admin.

**Input file** (no live gRPC required; Auth-Service `ListUsers` can feed this later): JSON list or `{"users":[...]}` or CSV with `id,google_email`.

```json
[{"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "google_email": "family@example.test"}]
```

**Commands** (from `backend/`, or `docker compose exec app python scripts/migrate_local_users_to_auth.py …`). Backup PostgreSQL and blobs first.

```bash
cd backend

# Dry-run (default): plan remaps, hashed unmatched emails, no DB/storage writes
uv run python scripts/migrate_local_users_to_auth.py /path/to/auth-users.json

# Optional: print unmatched emails in plaintext (never passwords)
uv run python scripts/migrate_local_users_to_auth.py /path/to/auth-users.json --list-emails

# Write DB remaps and rename users/{old_id} → users/{new_id} on each disk
uv run python scripts/migrate_local_users_to_auth.py /path/to/auth-users.json --apply
```

- **FS** (`STORAGE_BACKEND=fs`): renames `{STORAGE_ROOT}/{disk_id}/users/{old_id}` on each `STORAGE_DISKS` entry.
- **S3** (`STORAGE_BACKEND=s3`): copies then deletes object keys under `users/{old_id}/` via `S3StorageAdapter.rename` (list + copy). Dry-run logs the keys.

Settings come from `get_settings()` (`DATABASE_URL`, `STORAGE_*`). Duplicate email matches are skipped with an error (fix the export and re-run).

---

## Restoring from backup

HomeCloud automatically creates compressed PostgreSQL dumps every day at 02:00 (and upon API request). Files are stored in:

```
/storage/disk1/_meta/backups/db_backup_YYYY-MM-DD_HH-MM-SS.sql.zst
```

On the host (with standard mounting): `./storage/disk1/_meta/backups/`.

### List backups (API)

```bash
curl -b cookies.txt http://localhost:8000/api/admin/backup/list
```

Requires `ADMIN` role authorization (cookie `access_token` after login).

### Manual backup trigger

```bash
curl -b cookies.txt http://localhost:8000/api/admin/backup/run
```

### Database restoration

1. Stop the application (to avoid active connections):
   ```bash
   docker compose stop app
   ```

2. Decompress the backup. On a host with `zstd` installed:
   ```bash
   zstd -d storage/disk1/_meta/backups/db_backup_2026-06-28_02-00-00.sql.zst -o /tmp/restore.sql
   ```

   Or inside the container using Python:
   ```bash
   docker compose run --rm app python -c "
   import zstandard as zstd
   from pathlib import Path
   src = Path('/storage/disk1/_meta/backups/db_backup_2026-06-28_02-00-00.sql.zst')
   dst = Path('/tmp/restore.sql')
   dst.write_bytes(zstd.ZstdDecompressor().decompress(src.read_bytes()))
   print('Decompressed to', dst)
   "
   ```

3. Restore the dump into PostgreSQL:
   ```bash
    docker compose exec -T db psql -U homecloud -d homecloud < /tmp/restore.sql
   ```

   If the file is inside the `app` container:
   ```bash
   docker compose exec -T app cat /tmp/restore.sql | docker compose exec -T db psql -U homecloud -d homecloud
   ```

4. Start the application:
   ```bash
   docker compose start app
   ```

Backups older than 30 days are automatically deleted with each new backup.

---

## Useful commands

```bash
# container status
docker compose ps

# backend logs (structured JSON)
docker compose logs -f app

# stop
docker compose down

# stop and remove DB and Redis volumes
docker compose down -v
```

## Data storage

HomeCloud separates **files** and **metadata**:

| What | Where |
|---|---|
| File content (photos, documents, encrypted data) | Filesystem on disk |
| Metadata (name, size, path, owner, quota) | PostgreSQL |
| Sessions and cache | Redis |
| Database metadata backups | `{STORAGE_ROOT}/disk1/_meta/backups/` |

### Disk structure

Storage root in the container is `/storage` (on host, `./storage/disk1` is mounted to `/storage/disk1` by default).

```
/storage/
└── disk1/
    ├── users/              ← user private files
    │   └── {user_id}/
    │       ├── photos/
    │       ├── files/
    │       └── private/
    ├── shared/             ← shared folder (FAMILY, ADMIN)
    └── _meta/backups/      ← DB backups
```

### Startup configuration

All parameters are set in `.env` (template — `.env.example`).

**Storage and disks:**

| Variable | Default | Description |
|---|---|---|
| `STORAGE_ROOT` | `/storage` | Storage root inside the container |
| `STORAGE_DISKS` | `disk1` | List of active disks separated by commas (`disk1,disk2`) |
| `DISK_STRATEGY` | `most_free_space` | Strategy for choosing disk for writing |
| `MIN_FREE_SPACE_MB` | `500` | Minimum free space on disk required for writing |
| `DISK_SPACE_CACHE_TTL` | `30` | Cache duration for checking free space, seconds |

**Quotas and archiving:**

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_USER_QUOTA_MB` | `100` | Default total quota for a new user (all roles). `STRANGER_QUOTA_MB` is a deprecated alias |
| `ARCHIVE_DAYS_THRESHOLD` | `180` | Number of days without access before file is archived |

**Logging:**

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FILE_ENABLED` | `false` | Log writing to file in addition to stdout |
| `LOG_FILE_PATH` | `/var/log/homecloud/app.log` | Path to log file |
| `LOG_FILE_ROTATION` | `100 MB` | Log file rotation size |
| `LOG_FILE_RETENTION` | `30 days` | Retention of old log files |

Logs are output in JSON format with fields: `timestamp`, `level`, `user_id`, `action`, `file_id`, `disk_id`, `result`, `error_code`. File contents, passwords, and encryption keys are not logged.

## Project structure

- `backend/` — FastAPI application
- `frontend/` — React application
- `storage/disk1/` — mountable storage disk
- `.env.example` — environment variable template

## Deploying to Kubernetes

### 1. Prepare the environment

Copy the env file and fill in the real values:

```bash
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, JWT_SECRET, ADMIN_EMAIL/PASSWORD, etc.
```

### 2. Create the Kubernetes Secret (passwords only)

ConfigMap `app-config` (non-sensitive settings) is already in `deployment.yaml`.
Secret `app-secrets` (passwords, tokens) is generated from `.env`:

```bash
chmod +x generate-secret.sh
./generate-secret.sh .env <namespace>
```

Or manually:

```bash
# Only secrets — config goes through ConfigMap in deployment.yaml
kubectl create secret generic app-secrets \
  --from-literal=POSTGRES_USER="$(grep '^POSTGRES_USER=' .env | cut -d= -f2-)" \
  --from-literal=POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)" \
  --from-literal=DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)" \
  --from-literal=JWT_SECRET="$(grep '^JWT_SECRET=' .env | cut -d= -f2-)" \
  --from-literal=ADMIN_EMAIL="$(grep '^ADMIN_EMAIL=' .env | cut -d= -f2-)" \
  --from-literal=ADMIN_PASSWORD="$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-)" \
  --from-literal=GOOGLE_CLIENT_ID="$(grep '^GOOGLE_CLIENT_ID=' .env | cut -d= -f2-)" \
  --from-literal=GOOGLE_CLIENT_SECRET="$(grep '^GOOGLE_CLIENT_SECRET=' .env | cut -d= -f2-)" \
  --dry-run=client -o yaml | kubectl apply -n <namespace> -f -
```

### 3. Apply the remaining resources

```bash
kubectl apply -f deployment.yaml
```

`deployment.yaml` contains: `ConfigMap` (non-sensitive settings), PVCs, Deployments, Services.
The `app` and `db` pods read from both: `configMapRef: app-config` + `secretRef: app-secrets`.

### 4. Build and push images

```bash
# Replace with your registry
docker build -t your-registry/webstorage-app:latest -f backend/Dockerfile .
docker build -t your-registry/webstorage-frontend:latest -f frontend/Dockerfile .
docker push your-registry/webstorage-app:latest
docker push your-registry/webstorage-frontend:latest

kubectl rollout restart deployment/app
kubectl rollout restart deployment/frontend
```

### 5. Verify

```bash
kubectl get pods
kubectl logs -l app=app -f
```
