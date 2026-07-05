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
| `STRANGER_QUOTA_MB` | `100` | Storage limit for STRANGER role |
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
