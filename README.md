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

### Frontend (Vite + React)

Frontend не входит в `docker-compose`. Запуск отдельно:

```bash
cd frontend
npm install
npm run dev
```

Интерфейс: [http://localhost:5173](http://localhost:5173).

Через Docker (альтернатива):

```bash
docker build -t homecloud-frontend ./frontend
docker run --rm -p 5173:5173 homecloud-frontend
```

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

## Структура

- `backend/` — FastAPI-приложение
- `frontend/` — React-приложение
- `storage/disk1/` — монтируемый диск хранилища
- `.env.example` — шаблон переменных окружения

Подробнее: `ТЗ_v1.1_Сетевое_Хранилище.md`, план разработки: `План_разработки_HomeCloud.md`.
