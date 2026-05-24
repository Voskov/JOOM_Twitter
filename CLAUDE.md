# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (dev included)
uv sync --extra dev

# Run the app locally (requires .env or env vars)
uvicorn app.main:app --reload

# Run with Docker (recommended)
docker-compose up --build

# Run migrations
alembic upgrade head

# Rollback last migration
alembic downgrade -1

# Lint
ruff check app/ tests/

# Format
ruff format app/ tests/

# Type check
mypy app/

# Run all tests (requires Postgres on joon_twitter_test + Redis on DB 1)
pytest

# Run a single test file
pytest tests/test_auth.py -v

# Run a single test
pytest tests/test_auth.py::test_signup_success -v

# Run tests with Docker services
docker-compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres redis
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/joon_twitter_test pytest
```

## Architecture

### Request flow

Every authenticated request goes through `app/dependencies.py::get_current_user`:
1. Extract token from `Authorization: Bearer` header, then fall back to `access_token` httpOnly cookie
2. Decode JWT (HS256) via `app/core/security.py::decode_access_token`
3. Check `blacklist:{jti}` key in Redis — if present, token was revoked via SignOut
4. Load `User` from Postgres by `username` claim (`sub`)

All domain errors are raised as subclasses of `AppError` (in `app/core/exceptions.py`) and caught by a single FastAPI exception handler registered in `create_app()`. Never raise `HTTPException` directly — use `NotFoundError`, `ConflictError`, `UnauthorizedError`, etc.

### Layer responsibilities

| Layer | Path | Role |
|---|---|---|
| Routers | `app/routers/` | HTTP in/out only — no business logic, just call services |
| Services | `app/services/` | Business logic, DB queries, cache ops |
| Schemas | `app/schemas/` | Pydantic I/O models — never use ORM models in responses |
| DB models | `app/db/models.py` | SQLAlchemy ORM — no relationships defined; all JOINs are explicit in services |
| Core | `app/core/` | Cross-cutting: security (JWT/bcrypt), exceptions |

### Feed caching

`feed_service.py` implements a pull model with Redis JSON caching:
- `global_feed` key — first page (offset=0) of all messages, TTL 300s, deleted on every `PostMessage`
- `follow_feed:{user_id}` key — first page of followed-user posts per user, TTL 120s, deleted for all followers on `PostMessage`
- Pages beyond offset=0 always hit Postgres directly

When adding new feed types, follow the same pattern: check Redis at offset==0, populate on miss, invalidate on writes.

### Token invalidation

SignOut flow: router extracts the raw token, decodes it to get `jti`, calls `auth_service.sign_out` which writes `blacklist:{jti}` to Redis with TTL equal to the token's remaining lifetime. Every subsequent request via `get_current_user` checks this key before allowing access.

### Testing

Tests use real Postgres (`joon_twitter_test` DB) and real Redis (DB index 1). Fixtures in `conftest.py` override `get_db` and `get_redis_pool` dependencies via `app.dependency_overrides`. The session-scoped `test_engine` fixture runs `Base.metadata.create_all` on startup and `drop_all` on teardown — no Alembic needed for tests.

Feed tests that need to bypass the Redis cache use `offset=1` (cache only applies at `offset==0`).

`asyncio_mode = "auto"` is set in `pyproject.toml` — no `@pytest.mark.asyncio` decorator needed. Use `@pytest_asyncio.fixture` (not `@pytest.fixture`) for async fixtures.

### Environment variables

See `.env.example`. Key vars: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (use `openssl rand -hex 32`), `ACCESS_TOKEN_EXPIRE_MINUTES`.

The `Settings` class in `app/config.py` is a `pydantic-settings` `BaseSettings` — env vars override `.env` file values automatically.
