# Joon Twitter API

A Twitter-like backend REST API built with **FastAPI**, **SQLAlchemy 2 (async)**, **PostgreSQL**, and **Redis**.

---

## Tech stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.115+ |
| ORM | SQLAlchemy 2 (async) + asyncpg |
| Migrations | Alembic |
| Cache / Pub-Sub | Redis 7 (hiredis) |
| Auth | python-jose (JWT) + passlib (bcrypt) |
| Runtime | Python 3.13, uvicorn |
| Packaging | uv |

---

## Running with Docker Compose (recommended)

```bash
# 1. Copy the example env file and edit as needed
cp .env.example .env

# 2. Build and start all services
docker compose up --build

# 3. (Optional) Run Alembic migrations inside the app container
docker compose exec app alembic upgrade head
```

The API will be available at <http://localhost:8000>.  
Interactive docs (Swagger UI) are at <http://localhost:8000/docs>.  
ReDoc is at <http://localhost:8000/redoc>.

---

## Running locally with uv

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) installed
- A running PostgreSQL 16 instance
- A running Redis 7 instance

```bash
# 1. Install all dependencies (including dev)
uv sync

# 2. Copy env file and point to your local services
cp .env.example .env
# Edit DATABASE_URL and REDIS_URL to point at localhost

# 3. Apply database migrations
uv run alembic upgrade head

# 4. Start the development server with auto-reload
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Running tests

```bash
uv run pytest
```

Test files live in `tests/`. `pytest-asyncio` is configured in auto mode so
async test functions are discovered automatically.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/joon_twitter` | Async SQLAlchemy connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SECRET_KEY` | `change-me-in-production` | JWT signing secret — **always override in production** |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT lifetime in minutes (default 24 h) |

> Generate a strong secret key with: `openssl rand -hex 32`

---

## API docs

Once the server is running, open:

- **Swagger UI** — <http://localhost:8000/docs>
- **ReDoc** — <http://localhost:8000/redoc>
- **OpenAPI JSON** — <http://localhost:8000/openapi.json>
