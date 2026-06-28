# HomeCloud

Самохостируемое сетевое хранилище с веб-интерфейсом. Разворачивается через Docker.

## Требования

- Docker Desktop (или Docker Engine + Docker Compose v2)
- Node.js 20+ (только для локального запуска frontend)
- Git

## Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone <url-репозитория> WebStorage
cd WebStorage
```

### 2. Настроить окружение

```bash
cp .env.example .env
```

В `.env` обязательно задайте:

- `JWT_SECRET` — случайная строка (например, `openssl rand -hex 32`)
- `POSTGRES_PASSWORD` — пароль PostgreSQL (и обновите `DATABASE_URL`, если меняли пароль)
- `ADMIN_EMAIL` и `ADMIN_PASSWORD` — учётные данные первого администратора

### 3. Запустить backend

```bash
docker compose up --build -d
```

Проверка: [http://localhost:8000](http://localhost:8000) — ответ `{"status": "ok", "version": "1.0"}`.

При каждом старте контейнера `app` автоматически выполняется `alembic upgrade head`.

### 4. Инициализировать хранилище и администратора

```bash
docker compose exec app python scripts/init_storage.py
docker compose exec app python scripts/init_db.py
```

`init_storage.py` создаёт структуру папок на диске (`users/`, `shared/`, `_meta/backups/`).
`init_db.py` создаёт первого пользователя с ролью ADMIN из переменных `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

### 5. Запустить frontend

```bash
cd frontend
npm install
npm run dev
```

Интерфейс: [http://localhost:5173](http://localhost:5173). Войдите с email и паролем администратора.

---

## Создание первого администратора

Администратор создаётся однократно скриптом `init_db.py`:

```bash
docker compose exec app python scripts/init_db.py
```

Перед запуском задайте в `.env`:

| Переменная | Описание |
|---|---|
| `ADMIN_EMAIL` | Email администратора |
| `ADMIN_PASSWORD` | Пароль администратора |

Если пользователь с таким email уже существует, скрипт завершится без изменений. После создания войдите через форму входа на frontend или API `POST /api/auth/login`.

---

## Добавление нового диска

1. Создайте папку на хосте, например `./storage/disk2`.
2. Добавьте volume в `docker-compose.yml`:
   ```yaml
   - ./storage/disk2:/storage/disk2
   ```
3. В `.env` укажите `STORAGE_DISKS=disk1,disk2`.
4. Перезапустите контейнеры:
   ```bash
   docker compose down
   docker compose up -d
   ```
5. Инициализируйте структуру на новом диске:
   ```bash
   docker compose exec app python scripts/init_storage.py
   ```

Существующие файлы остаются на своих дисках; новые записи распределяются по свободному месту (`DiskRouter`).

Резервные копии метаданных БД сохраняются на первом диске из `STORAGE_DISKS` (по умолчанию `disk1`) в `_meta/backups/`.

---

## Восстановление из бэкапа

HomeCloud автоматически создаёт сжатые дампы PostgreSQL каждый день в 02:00 (и по запросу через API). Файлы хранятся в:

```
/storage/disk1/_meta/backups/db_backup_YYYY-MM-DD_HH-MM-SS.sql.zst
```

На хосте (при стандартном монтировании): `./storage/disk1/_meta/backups/`.

### Список бэкапов (API)

```bash
curl -b cookies.txt http://localhost:8000/api/admin/backup/list
```

Требуется авторизация с ролью ADMIN (cookie `access_token` после входа).

### Ручной запуск бэкапа

```bash
curl -b cookies.txt http://localhost:8000/api/admin/backup/run
```

### Восстановление базы данных

1. Остановите приложение (чтобы не было активных подключений):
   ```bash
   docker compose stop app
   ```

2. Распакуйте бэкап. На хосте с установленным `zstd`:
   ```bash
   zstd -d storage/disk1/_meta/backups/db_backup_2026-06-28_02-00-00.sql.zst -o /tmp/restore.sql
   ```

   Или внутри контейнера через Python:
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

3. Восстановите дамп в PostgreSQL:
   ```bash
   docker compose exec -T db psql -U homecloud -d homecloud < /tmp/restore.sql
   ```

   Если файл внутри контейнера `app`:
   ```bash
   docker compose exec -T app cat /tmp/restore.sql | docker compose exec -T db psql -U homecloud -d homecloud
   ```

4. Запустите приложение:
   ```bash
   docker compose start app
   ```

Бэкапы старше 30 дней удаляются автоматически при каждом новом бэкапе.

---

## Полезные команды

```bash
# статус контейнеров
docker compose ps

# логи backend (структурированный JSON)
docker compose logs -f app

# остановка
docker compose down

# остановка с удалением volumes БД и Redis
docker compose down -v
```

## Хранение данных

HomeCloud разделяет **файлы** и **метаданные**:

| Что | Где |
|---|---|
| Содержимое файлов (фото, документы, зашифрованные данные) | Файловая система на диске |
| Метаданные (имя, размер, путь, владелец, квота) | PostgreSQL |
| Сессии и кэш | Redis |
| Резервные копии метаданных БД | `{STORAGE_ROOT}/disk1/_meta/backups/` |

### Структура на диске

Корень хранилища в контейнере — `/storage` (на хосте по умолчанию `./storage/disk1` монтируется в `/storage/disk1`).

```
/storage/
└── disk1/
    ├── users/              ← личные файлы пользователей
    │   └── {user_id}/
    │       ├── photos/
    │       ├── files/
    │       └── private/
    ├── shared/             ← общая папка (FAMILY, ADMIN)
    └── _meta/backups/      ← резервные копии БД
```

### Настройка при запуске

Все параметры задаются в `.env` (шаблон — `.env.example`).

**Хранилище и диски:**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `STORAGE_ROOT` | `/storage` | Корень хранилища внутри контейнера |
| `STORAGE_DISKS` | `disk1` | Список активных дисков через запятую (`disk1,disk2`) |
| `DISK_STRATEGY` | `most_free_space` | Стратегия выбора диска для записи |
| `MIN_FREE_SPACE_MB` | `500` | Минимум свободного места на диске для записи |
| `DISK_SPACE_CACHE_TTL` | `30` | Кэш проверки свободного места, секунды |

**Квоты и архивирование:**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `STRANGER_QUOTA_MB` | `100` | Лимит хранилища для роли STRANGER |
| `ARCHIVE_DAYS_THRESHOLD` | `180` | Через сколько дней без доступа файл уходит в архив |

**Логирование:**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `LOG_FILE_ENABLED` | `false` | Запись логов в файл помимо stdout |
| `LOG_FILE_PATH` | `/var/log/homecloud/app.log` | Путь к файлу логов |
| `LOG_FILE_ROTATION` | `100 MB` | Ротация файла логов |
| `LOG_FILE_RETENTION` | `30 days` | Хранение старых файлов логов |

Логи выводятся в формате JSON с полями: `timestamp`, `level`, `user_id`, `action`, `file_id`, `disk_id`, `result`, `error_code`. Содержимое файлов, пароли и ключи шифрования не логируются.

## Структура проекта

- `backend/` — FastAPI-приложение
- `frontend/` — React-приложение
- `storage/disk1/` — монтируемый диск хранилища
- `.env.example` — шаблон переменных окружения

Подробнее: `ТЗ_v1.1_Сетевое_Хранилище.md`, план разработки: `План_разработки_HomeCloud.md`.
