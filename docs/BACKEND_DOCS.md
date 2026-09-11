# HomeCloud Backend Documentation

## Overview

**HomeCloud** — самохостируемое сетевое хранилище (Home Cloud Storage), построенное на
Python 3.12 + FastAPI. Идентичность — заголовки шлюза + User-Service (без gRPC).
Данные — Common Postgres / Redis / MinIO (один bucket `storage`). Production `app`
отдаёт API и собранный SPA.

### Стек технологий

- Web Framework: FastAPI (async)
- ORM: SQLAlchemy 2.0 (async, asyncpg)
- БД: PostgreSQL
- Объектное хранилище: MinIO (S3 API)
- Кэш / сессии: Redis (aioredis)
- Конфигурация: pydantic-settings
- Шифрование: cryptography (AES-256-GCM)
- Сжатие: zstandard (уровень 22)
- Таски: APScheduler
- Логирование: loguru (structured JSON через python-json-logger)
- Управление миграциями: Alembic

---

## Архитектура (Clean Architecture)

```
app/
├── domain/              ← Сущности, правила, value objects
├── application/         ← Use cases, сервисы
├── infrastructure/      ← Адаптеры, БД, внешние сервисы
└── presentation/        ← FastAPI роутеры, схемы, мидлвары
```

### Правила зависимости

Domain -> ничего.
Application -> Domain.
Infrastructure -> Domain + Application.
Presentation -> Application + Infrastructure.

Код в нижележащих слоях НЕ зависит от вышележащих.

---

## Конфигурация

### Точка входа

Файл: `backend/config.py`

Конфигурация строится на `pydantic-settings`. Единая фабрика `get_settings()` (lru_cache)
возвращает singleton `Settings`, агрегирующий подуровни:

| Подуровень | ENV-переменные | Описание |
|---|---|---|
| `DatabaseSettings` | `DATABASE_URL` | Подключение PostgreSQL |
| `CacheDBSettings` | `REDIS_URL` | Подключение Redis |
| `StorageSettings` | `STORAGE_DISKS`, `DISK_STRATEGY`, `DISK_SPACE_CACHE_TTL`, `MIN_FREE_SPACE_MB` | Логические диски (1:1 с MinIO buckets) |
| `S3Settings` | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_USE_SSL`, `S3_BUCKET_PREFIX`, `S3_PATH_STYLE` | MinIO / S3 |
| `AuthSettings` | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `FRONTEND_URL`, `JWT_SECRET`, `SESSION_TTL_SECONDS`, `PRIVATE_SESSION_TTL_HOURS` | Аутентификация |
| `BusinessLogicSettings` | `PHOTO_BATCH_SIZE`, `THUMBNAIL_MAX_PX`, `DEFAULT_USER_QUOTA_MB` (alias `STRANGER_QUOTA_MB`), `ARCHIVE_DAYS_THRESHOLD` | Бизнес-логика |
| `AdminSettings` | `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Admin-аккаунт |
| `LoggingSettings` | `LOG_LEVEL`, `LOG_FILE_ENABLED`, `LOG_FILE_PATH`, `LOG_FILE_ROTATION`, `LOG_FILE_RETENTION` | Логирование |

### Логирование

Файл: `backend/app/infrastructure/logging_setup.py`

Настройка `setup_logging(settings)` удаляет стандартный sink loguru и добавляет кастомный
sink, сериализующий логи в JSON через `JsonFormatter`. Обязательные поля: `timestamp`,
`user_id`, `action`, `file_id`, `disk_id`, `result`, `error_code`.

---

## Domain Layer

### Сущности (Value Objects)

Файл: `backend/app/domain/entities/`

| Сущность | Поля | Примечание |
|---|---|---|
| `User` | id, email, password_hash, google_id, role, is_active, created_at | Frozen dataclass |
| `FileRecord` | id, user_id, disk_id, relative_path, original_name, size_bytes, mime_type, is_encrypted, section, status, checksum_sha256, created_at, last_accessed_at, is_archived, archive_path | Frozen dataclass |
| `DiskVolume` | id, bucket, priority, is_active | Frozen dataclass; `bucket` = `{S3_BUCKET_PREFIX}{id}` |

### Value Objects

Файл: `backend/app/domain/value_objects/`

**Role** (`StrEnum`): `STRANGER`, `FAMILY`, `ADMIN`
- `can_access_shared()` -> FAMILY, ADMIN
- `can_access_admin()` -> только ADMIN

**FileSection** (`StrEnum`): `PHOTOS`, `FILES`, `PRIVATE`, `SHARED`, `RESUMES` (Alembic `006`)

**FileStatus** (`StrEnum`): `PENDING`, `COMMITTED`, `ARCHIVED`, `DELETED`
- `PENDING` -> файл в процессе загрузки; если старше 1 часа — чистится фоновой задачей

**StorageQuota** (`dataclass`): used_bytes, limit_bytes. Методы: `available_bytes()`,
`is_exceeded()`, `is_unlimited()`, `would_exceed()`. `limit_bytes = 0` means unlimited.

**ErrorCode** (`StrEnum`): `QUOTA_EXCEEDED`, `UNSUPPORTED_FORMAT`, `PRIVATE_SESSION_EXPIRED`,
`DISK_UNAVAILABLE`, `PATH_TRAVERSAL_DETECTED`, `FILE_NOT_FOUND`, `ACCESS_DENIED`,
`TOO_MANY_ATTEMPTS`, `EMAIL_ALREADY_EXISTS`, `INVALID_CREDENTIALS`, `UNAUTHORIZED`,
`USER_NOT_FOUND`, `INTERNAL_ERROR`

### Исключения

Файл: `backend/app/domain/exceptions.py`

| Исключение | Когда выбрасывается | HTTP-код |
|---|---|---|
| `StorageUnavailableError` | Нет доступных дисков | 503 |
| `EmailAlreadyExistsError` | Дубликат email при регистрации | 409 |
| `InvalidCredentialsError` | Неверный логин/пароль | 401 |
| `PathTraversalError` | Путь выходит за пределы директории | 400 |
| `QuotaExceededError` | Превышен лимит хранилища (содержит available_bytes) | 413 |
| `FileNotFoundError` | Файл/директория не найдены | 404 |
| `AccessDeniedError` | Нет прав доступа | 403 |
| `UnsupportedFormatError` | Неподдерживаемый формат файла | 400 |
| `PrivateSessionExpiredError` | Ключ шифрования истёк | 401 |
| `KeysValidationError` | Пустое/дублирующееся имя или значение ключа | 400 |
| `KeysYamlInvalidError` | `keys.yaml` невалиден или не mapping | 409 |
| `ResumeValidationError` | Имя узла / поля / статуса невалидно (несёт свой `error_code`) | 400 |
| `ResumeNodeExistsError` | Узел с таким именем уже есть среди соседей | 409 |
| `ResumeMetaInvalidError` | `meta.yaml` / `statuses.yaml` невалиден | 409 |
| `UserNotFoundError` | Пользователь не найден | 404 |
| `SelfRoleChangeError` | Админ пытается сменить свою роль | 403 |
| `SelfUserDeletionError` | Админ пытается удалить свой аккаунт | 403 |

---

## Infrastructure Layer

### Базы данных

Файл: `backend/app/infrastructure/database/models.py`

ORM-модели SQLAlchemy 2.0 (mapped_column):

**User** (users)
- id: UUID PK
- email: String(255) unique, index
- password_hash: String(255) nullable
- google_id: String(255) unique, index
- role: Enum(UserRole) default=STRANGER
- is_active: Boolean default=True
- created_at: DateTime TZ server_default=now
- relationships: file_records, quota_usage, upload_sessions

**FileRecord** (file_records)
- id: UUID PK
- user_id: UUID FK -> users.id CASCADE
- disk_id: String(64)
- relative_path: Text
- original_name: String(512)
- size_bytes: BigInteger
- mime_type: String(255)
- is_encrypted: Boolean default=False
- section: Enum(FileSection)
- status: Enum(FileStatus) default=PENDING
- checksum_sha256: String(64) nullable
- created_at, last_accessed_at: DateTime TZ
- is_archived: Boolean default=False
- archive_path: Text nullable
- index: ix_file_records_user_id

**UserQuotaUsage** (user_quota_usage)
- user_id: UUID PK FK
- total_bytes, limit_bytes (default 100 MiB), private_bytes, private_limit_bytes, photos_bytes: BigInteger
- updated_at: DateTime TZ onupdate=now

**UploadSession** (upload_sessions) — зарезервировано для chunked upload
- id, user_id, target_path, total_size, received_bytes, is_encrypted, expires_at

### Репозитории

Файл: `backend/app/infrastructure/database/repositories/`

**UserRepository** — CRUD пользователей
- get_by_email, get_by_google_id, get_by_id
- create(email, password_hash, google_id, role)
- link_google_id(user_id, google_id)
- update_role(user_id, role), set_active(user_id, is_active)
- list_for_admin(page, limit, role_filter, email_search) -> (rows, total)
- delete(user_id) -> bool

**FileRepository** — CRUD файловых записей
- create(...), get_by_id, update_status(..., checksum_sha256, relative_path, original_name)
- get_committed_by_relative_path, get_downloadable_by_relative_path
- list_by_user_section, list_by_user_section_paginated (offset/limit, order by created_at DESC)
- count_by_user_section
- delete_all_by_user_section -> int
- list_candidates_for_archive(cutoff) — для архивации
- list_archived_records, mark_archived(file_id, archive_path)
- update_relative_path_prefix, heal_stale_relative_path
- list_active_under_prefix(user_id, section, prefix) — COMMITTED + ARCHIVED под директорией (рекурсивное удаление)
- get_uploaders_by_relative_paths_in_section

**QuotaRepository** — денормализованные квоты с атомарным инкрементом/декрементом
- get_by_user_id -> UserQuotaUsage (создаёт при отсутствии)
- increment(user_id, size_bytes, section)
- decrement(user_id, size_bytes, section)
- reset_private_usage(user_id)
- update_limit(user_id, limit_bytes)
- update_private_limit(user_id, limit_bytes)
- update_total_bytes(user_id, total_bytes)
- list_all_user_ids

### Storage Adapter Pattern

Файл: `backend/app/infrastructure/storage/`

#### Base: StorageAdapter (ABC)

Порт логических ключей (не filesystem Path). Защита от path traversal через `_safe_logical_key()`:
- `root_prefix`, `disk_id`, `disk_relative_prefix` (property)
- `_safe_logical_key(user_input)` -> нормализованный ключ без `..`
- list(path), read(path) -> AsyncIterator[bytes], write(path, data, size) -> checksum
- delete(path), mkdir(path), rename(old, new), exists(path)
- `encrypt_path(path)` -> str (identity по умолчанию)

Фабрики: `create_storage_adapter(disk_id, root_prefix)` и `build_disk_root_adapter(disk_id)`
всегда возвращают `S3StorageAdapter`. DI: `build_section_adapter` в
`presentation/dependencies/storage_factory.py`.

#### S3StorageAdapter

Единственная реализация блоб-хранилища. Объекты в MinIO bucket `{S3_BUCKET_PREFIX}{disk_id}`.
- Загрузка: streaming PutObject / multipart + SHA-256
- Чтение: streaming GetObject
- Список: ListObjectsV2; скрывает `.tmp` и dot-префиксы
- Ключи изоморфны логическим путям (`users/{user_id}/files/…`)

#### EncryptedStorageAdapter (Decorator)

Обёртка над любым `StorageAdapter` (сейчас всегда S3). Шифрование AES-256-GCM, имя шифруется
и кодируется в Base64URL.

Функции:
- `derive_encryption_key(passphrase, user_id)` -> 32-byte key (PBKDF2, SHA-256,
  100k итераций, salt=user_id.bytes)
- `encrypt_blob(key, plaintext)` -> iv + ciphertext
- `decrypt_blob(key, payload)` -> plaintext

Методы:
- `encrypt_name(name)` -> Base64URL строка
- `decrypt_name(enc_name)` -> original name
- `write(path, data, size)` -> шифрует потоково: iv(12B) + size(4B) + ciphertext
- `read(path)` -> декрипт потоково
- `list(path)` -> расшифровывает каждое имя; если не расшифровывается — пропускает
- `mkdir`, `delete`, `rename`, `exists` — транслируют операции через encrypt_path

Формат объекта: iv(12B) + chunk_size(4B BE) + ciphertext, повторяется для каждого
чанка.

### Маркерный файл

Файл: `.marker` в корне private-директории. Содержит `encrypt_blob(key, "HOMECLOUD_MARKER_V1")`.
Используется для валидации кодового слова: при unlock — расшифровать и сравнить.

### DiskRouter

Файл: `backend/app/infrastructure/disk_router.py`

Конфигурируется из `Settings.storage.*` и `Settings.s3.*`. Парсит `STORAGE_DISKS` (через
запятую) и создаёт `DiskVolume` с `bucket = {S3_BUCKET_PREFIX}{disk_id}` для каждого.

Ёмкость: `HeadBucket` (доступность), сумма `Size` объектов (used), MinIO Admin API
`GET /minio/admin/v3/info` (total). Если Admin API недоступен — fallback total = 1 TiB,
`free ≈ total − used`.

Методы:
- `get_write_disk()` -> DiskVolume с наибольшим свободным местом (≥ `MIN_FREE_SPACE_MB`)
- `get_free_space(disk_id)` -> int (кэшируется `DISK_SPACE_CACHE_TTL` секунд)
- `health_check()` -> dict[str, HEALTHY|LOW_SPACE|UNAVAILABLE]
- `get_all_disks()`, `get_disk_by_id(disk_id)`, `get_disk_space_stats(disk_id)`

### SessionStore (Redis)

Файл: `backend/app/infrastructure/session_store.py`

Singleton через `get_session_store()`. Хранит:

| Префикс | Ключ | TTL |
|---|---|---|
| `private_key:` | {session_id} | PRIVATE_SESSION_TTL_HOURS |
| `oauth_state:` | {state} | 600s |
| `oauth_ticket:` | {ticket} | 120s |
| `auth_rate:` | {endpoint}:{ip} | window (60s) |
| `unlock_attempts:` | {user_id}:{ip} | 900s |

Методы:
- `set_private_key`, `get_private_key`, `delete_private_key`, `get_private_key_ttl`
- `increment_auth_requests(endpoint, ip, window)` -> (count, retry_after)
- `store_oauth_state`, `consume_oauth_state` (atomic delete)
- `store_oauth_ticket`, `consume_oauth_ticket` (getdel)
- `increment_unlock_attempts`, `reset_unlock_attempts`, `get_unlock_attempts`
  (max 5 attempts per 15 minutes per user+ip)

### ThumbnailService

Файл: `backend/app/infrastructure/thumbnail_service.py`

Использует Pillow (с pillow-heif для HEIC). Метод `generate(source, output, max_px)`:
- Поддерживаемые форматы: JPEG, PNG, WEBP, GIF, HEIF/HEIC
- EXIF transpose (исправление ориентации)
- Конвертация RGBA/P/LA -> RGB
- `image.thumbnail((max_px, max_px))`
- Сохранение в JPEG quality=85

### ArchiveManager

Файл: `backend/app/infrastructure/archive_manager.py`

Сжатие через zstandard (level 22). Методы:
- `compress(source, dest, compress_mode)` — валидирует mode (pre_encrypt | post_encrypt)
- `decompress(source, dest)`
- `compress_async`, `decompress_async` — обёртки через asyncio.to_thread

### GoogleOAuthClient

Файл: `backend/app/infrastructure/oauth_client.py`

Интеграция с Google OAuth 2.0. Методы:
- `get_auth_url(state)` -> URL (authorization endpoint, scope: openid email profile)
- `exchange_code(code, state)` -> GoogleUserInfo(email, google_id, name)
  - Token exchange -> userinfo endpoint

---

## Application Layer (Use Cases)

### AuthService

Файл: `backend/app/application/auth_service.py`

Зависимости: UserRepository, Settings.
- `register(email, password)` -> User (хеш через bcrypt/passlib)
- `login(email, password)` -> JWT token (HS256, payload: sub=user_id, exp)
- `login_or_create_google_user(google_user_info)` -> JWT token
  - Ищет по google_id -> login
  - Ищет по email -> если есть google_id -> login, иначе link
  - Нет записи -> создаёт STRANGER
- `get_user_from_token(token)` -> User | None (валидация + is_active check)

### FileService

Файл: `backend/app/application/file_service.py`

Универсальный сервис, работает с любым StorageAdapter.
Зависимости: adapter, quota_repo, file_repo, section, archive_manager (optional), disk_router (optional).

Методы:
- `list_directory(user_id, path)` -> list[FileNode]
  - Нормализует path, вызывает adapter.list
  - Для SHARED обогащает записи информацией об uploaders
  - Merge archived nodes
- `upload_file(user_id, path, filename, data, size, section)` -> FileRecord
  - Создаёт PENDING record
  - Записывает в .tmp/{file_id} -> rename -> COMMITTED
  - Инкремент quota, checksum SHA-256
  - Rollback при ошибке (удаляет tmp, удаляет record)
- `overwrite_file(user_id, path, filename, data, size, section)` -> FileRecord
  - Если FileRecord на этот path есть — пишет in-place, обновляет size/checksum, quota = delta
  - Если записи нет (файл удалили в FileManager) — создаёт одну новую, как `upload_file`
- `download_file(actor_id, file_id)` -> AsyncIterator[bytes]
  - Для архивных: stream_decompressed_archived
  - Иначе: adapter.read(section_path)
- `delete_file(actor_id, file_id, actor_role)` -> None
  - Проверка прав (свой файл или админ)
  - Для SHARED: можно удалять только свои файлы
  - Decrement quota, soft delete record
- `create_directory(user_id, path, name)` -> None
- `upload_zip_folder(...)` -> dict{files, dirs, total_bytes}
  - ZIP slip prevention, quota pre-check
  - Создаёт директорию по имени ZIP (stem)
  - Записывает каждый файл через tmp -> rename
- `rename(actor_id, file_id, new_name)` -> FileRecord
- `download_directory_as_zip(actor_id, path)` -> AsyncIterator[bytes]
  - Рекурсивный walk -> BytesIO -> ZIP_DEFLATED -> chunk streaming

#### delete_directory_recursive(user_id, path) -> int

Удаляет директорию вместе с блобами, записями `file_records` и квотой, возвращает число
освобождённых байт. В отличие от `delete_by_path` не оставляет осиротевшие строки за удалённым
prefix. Забирает записи в статусах `COMMITTED` и `ARCHIVED`
(`FileRepository.list_active_under_prefix`); для архивных дополнительно удаляет zstd-блоб.
Используется только секцией `RESUMES` — поведение `/files`, `/shared`, `/private` не менялось.

### PhotoService

Файл: `backend/app/application/photo_service.py`

Зависимости: adapter, thumbnail_service, file_repo, quota_repo, disk_router, archive_manager.
- `upload_photo(user_id, filename, data, size)` -> FileRecord
  - Валидация формата (SUPPORTED_EXTENSIONS)
  - Запись в originals/{file_id}{ext}
  - Async task на генерацию preview (thumbnail)
- `list_photos(user_id, page, limit)` -> PhotosPage(items, total, has_next)
- `get_preview(user_id, file_id)` -> (content, media_type)
  - Если превью нет и файл архивный — serving decompressed original
- `get_original(user_id, file_id)` -> AsyncIterator[bytes]
- `stream_original(user_id, file_id)` -> (mime_type, filename, stream)
- `delete_photo(user_id, file_id)` -> None

### PrivateService

Файл: `backend/app/application/private_service.py`

Управление приватным разделом (ключ шифрования в Redis).
Зависимости: session_store, quota_repo, file_repo, settings.
- `unlock(user_id, session_id, passphrase)` -> bool
  - derive_encryption_key -> проверяет .marker
  - Если маркера нет — создаёт
  - Сохраняет ключ в Redis (base64url encoded)
- `lock(session_id)` -> None
- `reset_storage(user_id, session_id)` -> None (удаляет .marker, все записи, сброс квоты)
- `get_file_service(user_id, session_id)` -> FileService
  - Получает ключ из Redis -> EncryptedStorageAdapter(inner, key)
- `get_quota(user_id)` -> {private_bytes, private_limit_bytes}
- `get_session_status(session_id)` -> {active, expires_in_seconds}

### KeysRegistryService

Файл: `backend/app/application/keys_registry_service.py`

Тот же unlocked encrypted `FileService`, что и Private. Канонический путь: `Keys/keys.yaml`.

- `list_keys(user_id)` -> list[{name, value}]
  - Создаёт папку `Keys/` если нет
  - Если `keys.yaml` нет — пишет пустой mapping (`{}`) и один FileRecord
  - Парсит YAML (flat string map). Невалидный YAML / не mapping → `KeysYamlInvalidError`, файл не перезаписывается
  - Нестроковые скаляры на чтении приводятся к `str`; вложенные значения отклоняются
- `save_keys(user_id, keys)` -> list[{name, value}]
  - Trim; пустые имя/значение и дубликаты имён → `KeysValidationError`
  - Пишет весь файл через `FileService.overwrite_file` (`sort_keys=False`, `allow_unicode=True`)
  - Логи: user_id + число ключей, без значений

### ResumeService

Файл: `backend/app/application/resume_service.py`

Дерево вакансий поверх обычного (незашифрованного) `FileService` с секцией `RESUMES`
и root prefix `users/{user_id}/resumes`. См. [ADR-010](./adr/ADR-010-resumes-yaml-tree-on-s3.md).

Уровни — это реальные директории S3: `{country}/{company}/{vacancy}`. Метаданные — YAML,
записываемый через `FileService.overwrite_file` (та же техника, что и `Keys/keys.yaml`).

- `list_nodes(user_id, path)` -> ResumeTree
  - Уровень (`COUNTRY` | `COMPANY` | `VACANCY`) выводится из глубины пути
  - Возвращает только директории; для вакансий подмешивает `status_id` / `website_url` из `meta.yaml`
  - Битый `meta.yaml` в листинге не фатален: warning в лог, вакансия отдаётся с `status_id=None`
- `create_node(user_id, path, name, status_id=None)` -> ResumeNode
  - Глубже вакансии → `ResumeValidationError(RESUME_DEPTH_INVALID)`
  - Имя: trim, 1–128 символов, без `/` `\`, не `.`/`..`, не `meta.yaml` / `statuses.yaml`
  - Дубликат среди соседей (case-insensitive) → `ResumeNodeExistsError`
  - На уровне вакансии дополнительно пишет `meta.yaml`; `status_id` проверяется по списку статусов
- `list_all_vacancies(user_id)` -> list[VacancyListItem]
  - Обход всего дерева: страна → компания → вакансия, плюс чтение `meta.yaml` на вакансию
  - Порядок: страна, компания, название вакансии (case-insensitive)
  - Битый `meta.yaml` не ломает список: вакансия отдаётся с `status_id=None`
- `rename_node(user_id, path, new_name)` -> ResumeNode — через `FileService.rename_by_path`,
  который переписывает prefix у всех вложенных `file_records`
- `delete_node(user_id, path)` — через `FileService.delete_directory_recursive`
- `get_vacancy(user_id, path)` / `save_vacancy(...)` -> VacancyMeta
  - `website_url` необязателен; непустое значение без схемы сохраняется как `https://<value>`
  - Имена полей: trim, непустые, уникальные без учёта регистра → `RESUME_FIELD_INVALID`
  - `status_id` на запись **не** проверяется — так переживает открепление статуса
  - Битый `meta.yaml` на чтении → `ResumeMetaInvalidError`, файл не перезаписывается
- `list_statuses(user_id)` / `save_statuses(user_id, statuses)` -> list[ResumeStatus]
  - Первый GET сидит `statuses.yaml`: Applied `#38BDF8`, Interview `#A78BFA`, Offer `#34D399`, Rejected `#F87171`
  - `id` — uuid4 hex, генерируется сервером при отсутствии и не меняется при переименовании
  - Пустое/дублирующееся имя (case-insensitive) или цвет не `#RRGGBB` → `RESUME_STATUS_INVALID`
  - Удаление статуса не переписывает ни одну вакансию: висячий `status_id` отдаётся как есть

### AdminService

Файл: `backend/app/application/admin_service.py`

- `list_users(page, limit, role_filter, email_search)` -> {items, total}
- `update_role(admin_id, target_id, new_role)` -> User (self-check)
- `update_user_quota(admin_id, target_id, principals, limit_mb=, private_limit_gb=)` -> None
- `block_user(admin_id, target_id)` -> None
- `delete_user(admin_id, target_id)` -> None (удаляет файлы на всех дисках)
- `get_storage_stats()` -> {disks: [{id, bucket, total_bytes, used_bytes, free_bytes, status}]}

### ArchiveService

Файл: `backend/app/application/archive_service.py`

Фоновая служба архивации.
- `run_daily_archive()` -> ArchiveReport(processed, skipped, errors)
  - Кандидаты: last_accessed_at < cutoff, COMMITTED, not archived
  - Для каждого: compress (pre_encrypt / post_encrypt) -> delete original -> mark ARCHIVED
  - Кэширует last_stats (ArchiveStats)
- `get_stats()` -> ArchiveStats
- `resolve_archive_path(record)`, `temp_decompress_path(record)`

### MaintenanceService

Файл: `backend/app/application/maintenance_service.py`

Фоновое обслуживание.
- `cleanup_pending_records()` — удаляет PENDING записи старше 1 часа
- `cleanup_tmp_dirs()` — удаляет .tmp директории
- `reconcile_quotas()` — сверяет total_bytes с SUM(file_records)

### BackupService

Файл: `backend/app/application/backup_service.py`

- `run_db_backup()` -> Path (выполняет pg_dump через subprocess)
- `list_backups()` -> list[BackupEntry]

---

## Presentation Layer

### Entry Point

Файл: `backend/main.py`

Инициализация FastAPI с lifespan:
1. APScheduler стартует 5 фоновых задач:
   - `db_backup` @ 02:00
   - `daily_archive` @ 03:00
   - `cleanup_pending` every 1h
   - `cleanup_tmp` every 1h
   - `reconcile_quotas` @ 04:00
2. Регистрирует exception handlers
3. Добавляет AuthRateLimitMiddleware
4. Включает все routers

Endpoints: GET / (status), GET /health

### Routers

Все роутеры используют prefix /api/{feature}.

#### AuthRouter (`/api/auth`)

| Method | Path | Описание |
|---|---|---|
| POST | /register | Регистрация (email + password). 201 + UserResponse |
| POST | /login | Вход. Устанавливает httpOnly cookie. 200 + UserResponse |
| GET | /google | Редирект на Google OAuth. 307 |
| GET | /google/callback | OAuth callback. Consumes state, exchanges code, stores ticket. 307 |
| GET | /google/session | Session bridge. Consumes ticket, sets cookie. 307 -> /files |
| POST | /logout | Удаляет cookie. 204 |
| GET | /me | Текущий пользователь. 200 + UserResponse |

#### ResumeRouter (`/api/resumes`)

Доступ: `check_role(FAMILY, ADMIN)`. STRANGER / BLOCKED → 403 `ACCESS_DENIED`.

| Method | Path | Описание |
|---|---|---|
| GET | /tree | Дочерние узлы по `path`. 200 + ResumeTreeResponse |
| POST | /tree | Создание узла `{path, name, status_id?}`. 201 + ResumeNode |
| PATCH | /tree | Переименование `{path, new_name}`. 200 + ResumeNode |
| DELETE | /tree | Рекурсивное удаление (`path` query). 204 |
| GET | /vacancies | Плоский список всех вакансий дерева. 200 + VacancyListResponse |
| GET | /vacancy | Метаданные вакансии (`path` query). 200 + VacancyMeta |
| PUT | /vacancy | Сохранение метаданных. 200 + VacancyMeta |
| GET | /statuses | Список статусов (сидится при первом чтении). 200 |
| PUT | /statuses | Замена всего списка статусов. 200 |

Файлы вакансии — `/api/resumes/files/*`, полный контракт FileRouter, секция `RESUMES`.
Роутер собирается фабрикой `build_file_router` (`routers/file_router_factory.py`), а не третьей
рукописной копией обработчиков; `/api/files` и `/api/shared` при этом не трогались.

#### FileRouter (`/api/files`)

| Method | Path | Описание |
|---|---|---|
| GET | / | Список файлов/папок (path query param) |
| POST | /upload | Загрузка файла (multipart). 201 + FileRecordResponse |
| POST | /upload-zip | Загрузка ZIP-папки. 201 + ZipUploadResponse |
| GET | /download | Скачивание файла (path query). StreamingResponse |
| GET | /download-folder | Скачивание директории (ZIP). StreamingResponse |
| DELETE | / | Удаление файла/папки (path query). 204 |
| POST | /mkdir | Создание папки. 201 |
| PATCH | /rename | Переименование. 200 + FileRecordResponse или 204 |
| GET | /search | 501 NOT_IMPLEMENTED |

#### PrivateRouter (`/api/private`)

Полностью повторяет FileRouter, но использует EncryptedStorageAdapter.
Дополнительные эндпоинты:

| Method | Path | Описание |
|---|---|---|
| POST | /unlock | Разблокировка (passphrase). Rate limited (5 attempts / 15 min) |
| POST | /lock | Заблокировать (удалить ключ). 204 |
| POST | /reset | Сброс приватного хранилища (только после rate limit). 204 |
| GET | /quota | Квота приватного раздела |
| GET | /session | Статус сессии (active, expires_in_seconds) |
| GET | /keys | Keys Registry: bootstrap `Keys/keys.yaml`, вернуть `{ keys: [{name, value}] }` |
| PUT | /keys | Keys Registry: валидация + overwrite всего YAML. 200 + тот же payload |

#### SharedRouter (`/api/shared`)

Повторяет FileRouter для общего раздела. Видимость: FAMILY + ADMIN.
Все операции показывают uploader для каждого файла.

#### PhotoRouter (`/api/photos`)

| Method | Path | Описание |
|---|---|---|
| GET | / | Список фото (paginated: page, limit). 200 + PhotoListResponse |
| POST | /upload | Загрузка фото. 201 + PhotoItemResponse |
| GET | /{id}/preview | Превью. 200 + image/jpeg |
| GET | /{id}/original | Оригинал (stream). 200 |
| DELETE | /{id} | Удаление фото. 204 |

#### QuotaRouter (`/api/quota`)

| Method | Path | Описание |
|---|---|---|
| GET | /me | Квота текущего пользователя. 200 + QuotaResponse |

Лимит: `user_quota_usage.limit_bytes` для всех ролей (по умолчанию `DEFAULT_USER_QUOTA_MB` = 100; `0` = без ограничения). Не свободное место диска.

#### AdminRouter (`/api/admin`)

Защищено middleware `check_role(Role.ADMIN)`.

| Method | Path | Описание |
|---|---|---|
| GET | /users | Список пользователей (paginated, filters). 200 + UserListResponse |
| PATCH | /users/{id}/role | Смена роли. 200 + UpdateRoleResponse |
| PATCH | /users/{id}/quota | Общий лимит (`limit_mb`) и/или приватный (`private_limit_gb`). 204 |
| POST | /users/{id}/block | Блокировка. 204 |
| DELETE | /users/{id} | Удаление пользователя. 204 |
| GET | /storage | Статистика дисков (`id`, `bucket`, total/used/free, status). 200 + StorageStatsResponse |
| GET | /storage/health | Health check дисков. 200 + StorageHealthResponse |
| GET | /archive/run | Запуск архивации. 200 + ArchiveReportResponse |
| GET | /archive/stats | Статистика архивации. 200 + ArchiveStatsResponse |
| GET | /maintenance/run | Запуск обслуживания. 200 + MaintenanceRunResponse |
| GET | /maintenance/stats | Статистика обслуживания. 200 + MaintenanceStatsResponse |
| GET | /backup/run | Резервное копирование БД. 200 + BackupRunResponse |
| GET | /backup/list | Список бэкапов. 200 + BackupListResponse |

### Schemas

Файл: `backend/app/presentation/schemas/`

Pydantic схемы для запросов и ответов:

| Файл | Схемы |
|---|---|
| auth.py | LoginRequest, RegisterRequest, UserResponse |
| files.py | FileNodeResponse, FileRecordResponse, MkdirRequest, RenameRequest, ZipUploadResponse |
| private.py | UnlockRequest, UnlockResponse, PrivateQuotaResponse, PrivateSessionResponse, KeyItem, KeysListResponse, KeysPutRequest |
| photos.py | PhotoItemResponse, PhotoListResponse |
| quota.py | QuotaResponse |
| admin.py | UpdateRoleRequest, UpdateRoleResponse, UpdateUserQuotaRequest, UserAdminViewResponse,
UserListResponse, DiskStatResponse, StorageHealthResponse, ArchiveReportResponse,
ArchiveStatsResponse, MaintenanceRunResponse, MaintenanceStatsResponse, ReconcileReportResponse,
BackupRunResponse, BackupEntryResponse, BackupListResponse |

### Middleware

**AuthRateLimitMiddleware** (`backend/app/presentation/middleware/rate_limit.py`)

Ограничивает POST /api/auth/login: 10 запросов / 60 секунд на IP.
Ограничивает POST /api/auth/register: 5 запросов / 60 секунд на IP.
Реализовано через Redis (INCR + EXPIRE).

**check_role** (`backend/app/presentation/middleware/check_role.py`)

Depends-функция. `check_role(Role.ADMIN)` — проверяет роль текущего пользователя.
Возвращает 403 ACCESS_DENIED при несоответствии.

### Exception Handlers

Файл: `backend/app/presentation/exception_handlers.py`

Регистрирует обработчики для всех domain-исключений. Все ответы возвращают JSON:
`{"detail": {"error_code": "...", "message": "..."}}` с соответствующим HTTP-кодом.
Unhandled exceptions возвращают 500 INTERNAL_ERROR.

### Dependencies

Файл: `backend/app/presentation/dependencies/`

Фабрики для DI через FastAPI Depends():
- auth.py: get_auth_service, get_current_user, get_google_oauth_client, get_quota_repository
- files.py: get_file_service
- photos.py: get_photo_service
- private.py: get_private_service, get_private_file_service
- admin.py: get_admin_service, get_disk_router
- archive.py: get_archive_service
- archive_providers.py: get_archive_disk_router, get_archive_manager
- backup.py: get_backup_service
- maintenance.py: get_maintenance_service
- shared.py: get_shared_file_service
- resumes.py: get_resumes_file_service, get_resume_service

### DB Session

Файл: `backend/app/infrastructure/database/session.py`
- async_session_factory: SQLAlchemy async session maker
- get_async_session: Depends-фабрика

---

## Логические ключи объектов (MinIO)

Каждый `STORAGE_DISKS` id — отдельный bucket `{S3_BUCKET_PREFIX}{disk_id}`.
Ключи изоморфны прежним относительным путям (без физического дерева на диске приложения).

```
bucket {S3_BUCKET_PREFIX}disk1/
├── _meta/backups/                 ← pg_dump + zstd
├── shared/
│   └── ...
└── users/{user_id}/
    ├── photos/
    │   ├── originals/{uuid}.{ext}
    │   └── previews/{uuid}_thumb.jpg
    ├── files/
    │   ├── .tmp/{uuid}            ← незакоммиченные загрузки
    │   └── ...
    ├── resumes/
    │   ├── statuses.yaml          ← список статусов пользователя
    │   └── {country}/{company}/{vacancy}/
    │       ├── meta.yaml          ← website_url, status_id, fields[]
    │       └── ...                ← вложения вакансии
    └── private/
        ├── .marker                ← encrypt_blob("HOMECLOUD_MARKER_V1", key)
        └── ...                    ← AES-GCM; имена Base64URL
```

Архивированные объекты: `{original_key}.zst` (zstandard).
Зашифрованные архивы: `{original_key}.enc.zst`.
Временные файлы архивации/превью живут в `/tmp` процесса, затем пишутся через адаптер.
