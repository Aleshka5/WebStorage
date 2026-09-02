FROM node:20-alpine AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV PYTHONPATH=/app
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends libheif1 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./

COPY --from=frontend /frontend/dist /opt/spa

COPY backend/scripts/entrypoint.py /entrypoint.py

ENV PATH="/opt/venv/bin:$PATH"
ENV SPA_STATIC_DIR=/opt/spa

ENTRYPOINT ["/opt/venv/bin/python", "/entrypoint.py"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
