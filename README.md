# HomeCloud

Самохостируемое сетевое хранилище с веб-интерфейсом. Разворачивается через Docker.

## Требования

- Docker Desktop (или Docker Engine + Docker Compose v2)
- Node.js 20+ (только для локального запуска frontend)

## Первый запуск

```bash
cp .env.example .env
```

В `.env` задайте как минимум `JWT_SECRET` и `POSTGRES_PASSWORD` (и обновите `DATABASE_URL`, если меняли пароль БД).

### Backend (FastAPI + PostgreSQL + Redis)

```bash
docker compose up --build
```

Фоновый режим:

```bash
docker compose up --build -d
```

Проверка API: [http://localhost:8000](http://localhost:8000) — ответ `{"status": "ok", "version": "1.0"}`.

При каждом старте контейнера `app` автоматически выполняется `alembic upgrade head`.

### Frontend (Vite + React)

Frontend не входит в `docker-compose`. Запуск отдельно:

```bash
cd frontend
npm install
npm run dev
```

Интерфейс: [http://localhost:5173](http://localhost:5173).

Через Docker (альтернатива; backend должен быть доступен на порту 8000 хоста):

```bash
docker build -t homecloud-frontend ./frontend
docker run --rm -p 5173:5173 homecloud-frontend
```

Proxy Vite по умолчанию направляет `/api` на `http://host.docker.internal:8000` (для контейнера).
При локальном `npm run dev` используется `http://localhost:8000`.
Переопределение: `API_PROXY_TARGET=http://app:8000 npm run dev`.

## Полезные команды

```bash
# статус контейнеров
docker compose ps

# логи backend
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

При первом запуске создайте структуру папок:

```bash
docker compose exec app python scripts/init_storage.py
```

Какой диск выбрать для новой записи, решает `DiskRouter`: среди доступных дисков выбирается тот, где больше всего свободного места. В метаданных файла сохраняется `disk_id`, поэтому чтение всегда идёт с правильного диска, даже если позже добавите второй.

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

**Первый администратор** (создаётся скриптом `init_db.py`):

| Переменная | Описание |
|---|---|
| `ADMIN_EMAIL` | Email администратора |
| `ADMIN_PASSWORD` | Пароль администратора |

### Добавление второго диска

1. Создайте папку на хосте, например `./storage/disk2`.
2. Добавьте volume в `docker-compose.yml`:
   ```yaml
   - ./storage/disk2:/storage/disk2
   ```
3. В `.env` укажите `STORAGE_DISKS=disk1,disk2`.
4. Перезапустите контейнеры и выполните `init_storage.py`.

Существующие файлы остаются на своих дисках; новые записи распределяются по свободному месту.

## Структура

- `backend/` — FastAPI-приложение
- `frontend/` — React-приложение
- `storage/disk1/` — монтируемый диск хранилища
- `.env.example` — шаблон переменных окружения

Подробнее: `ТЗ_v1.1_Сетевое_Хранилище.md`, план разработки: `План_разработки_HomeCloud.md`.
