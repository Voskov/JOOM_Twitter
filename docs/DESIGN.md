# Design Document — Twitter-like Backend API

## 1. Overview

This is a lightweight Twitter-like microblogging backend API. It allows users to register, post short messages (up to 140 characters), follow other users, and read feeds. The system is built for high concurrency and low latency on read-heavy workloads.

**Core capabilities:**

- User registration and authentication (JWT + httpOnly cookie)
- Posting messages capped at 140 characters
- Follow / unfollow other users
- Three feed views: global, following, and per-user
- Redis-backed feed caching with TTL-based invalidation
- Stateless auth with Redis-based token blacklist for sign-out

---

## 2. Architecture

### ASCII Diagram

```
                          ┌─────────────────────────────┐
                          │         HTTP Clients         │
                          │  (browsers, curl, Swagger)   │
                          └──────────────┬──────────────┘
                                         │ HTTPS
                                         ▼
                          ┌─────────────────────────────┐
                          │        FastAPI App           │
                          │  (async, uvicorn workers)    │
                          │                             │
                          │  Routes:                    │
                          │   /auth/*                   │
                          │   /messages                 │
                          │   /social/*                 │
                          │   /feed/*                   │
                          └──────┬──────────────┬───────┘
                                 │              │
                    asyncpg      │              │  redis-py (async)
                                 ▼              ▼
              ┌──────────────────────┐  ┌─────────────────┐
              │    PostgreSQL 16     │  │    Redis 7       │
              │                     │  │                  │
              │  Tables:            │  │  Keys:           │
              │   users             │  │   blacklist:{jti}│
              │   messages          │  │   global_feed    │
              │   follows           │  │   follow_feed:*  │
              └──────────────────────┘  └─────────────────┘
```

### Components

| Component | Role |
|-----------|------|
| **FastAPI** | Async HTTP framework. Handles routing, request validation (Pydantic v2), OpenAPI docs, dependency injection. |
| **PostgreSQL 16** | Source of truth for all persistent data: users, messages, follows. Accessed via SQLAlchemy 2.0 async ORM with asyncpg driver. |
| **Redis 7** | Two roles: (1) JWT blacklist for sign-out, (2) feed response cache to reduce Postgres load. |
| **uvicorn** | ASGI server running the FastAPI app. |
| **Docker Compose** | Orchestrates the app, Postgres, and Redis containers locally and in deployment. |

---

## 3. Async Design

The entire stack is async from top to bottom — FastAPI route handlers, SQLAlchemy sessions, Redis calls, and the ASGI server.

**Why async?**

This is a read-heavy social API. At any moment, the majority of active requests are waiting on I/O — a Postgres query result or a Redis GET — not doing CPU work. In a synchronous (thread-per-request) model, each blocked request holds a thread. Threads are expensive: each one costs ~1MB of stack memory and adds scheduler overhead. At 500 concurrent users, that is 500 threads blocked on I/O.

With async I/O, a single event loop thread can interleave thousands of in-flight requests. While one coroutine awaits a DB response, the event loop runs another. This gives near-linear concurrency scaling with a very small thread pool.

**Key async components:**

- `asyncpg` — PostgreSQL driver written specifically for Python's asyncio. It does not wrap a blocking C driver in a thread pool; it is natively non-blocking.
- `SQLAlchemy 2.0 async` — async engine and session support built on `asyncpg`.
- `redis-py` async client — uses asyncio under the hood; awaitable `.get()`, `.set()`, `.delete()`.
- FastAPI + uvicorn — FastAPI route functions declared with `async def` run directly on the event loop without thread handoff.

**When threads are still used:**

bcrypt password hashing is CPU-bound and blocks the event loop. That call is wrapped with `asyncio.to_thread()` to offload it to the thread pool executor, keeping the event loop free.

---

## 4. Authentication

### Mechanism

The API uses **stateless JWT** (JSON Web Tokens), signed with HS256. Every protected endpoint requires a valid, non-blacklisted JWT.

**Token delivery — dual channel:**

| Channel | How it's sent |
|---------|--------------|
| **Bearer header** | `Authorization: Bearer <token>` — standard for programmatic clients, Swagger UI |
| **httpOnly cookie** | `session=<token>` — set on sign-in, cleared on sign-out; safer for browser clients (not accessible to JavaScript) |

The auth middleware checks the `Authorization` header first, then falls back to the cookie. Either one is sufficient.

### JWT Claims

```
{
  "sub": "<user_uuid>",
  "jti": "<random uuid>",   // unique token ID
  "exp": <unix timestamp>,
  "iat": <unix timestamp>
}
```

`jti` (JWT ID) is a random UUID generated at sign-in. It is the handle used for blacklisting.

### Sign-in Flow

```
POST /auth/signin
  → validate credentials (bcrypt check)
  → generate JWT with jti
  → return token in response body + set httpOnly cookie
```

### Sign-out Flow

```
POST /auth/signout
  → decode JWT (get jti + exp)
  → SET blacklist:{jti} = "1" EX <remaining TTL seconds>
  → clear httpOnly cookie
```

### Request Authentication Flow

```
Every authenticated request:
  1. Extract token from header or cookie
  2. Verify JWT signature + expiry
  3. GET blacklist:{jti} from Redis
     → if key exists: 401 Unauthorized (token revoked)
     → if key absent: proceed
  4. Load user from sub claim
```

### Security Properties

- Tokens are short-lived (configurable TTL, default 1 hour). Even if a token leaks, exposure window is bounded.
- Sign-out is real: the `jti` blacklist entry in Redis ensures the old token cannot be reused, even before it expires.
- The blacklist key TTL matches the token's remaining lifetime — Redis automatically purges expired entries, so the blacklist doesn't grow unbounded.
- Passwords are hashed with bcrypt at work factor 12. The raw password is never stored or logged.

---

## 5. Feed Architecture

### Pull Model (Fan-out on Read)

The system uses a **pull model**: when a user requests their following feed, the server queries Postgres at read time.

```sql
-- GetFollowFeed (simplified)
SELECT m.*
FROM messages m
JOIN follows f ON f.followed_id = m.user_id
WHERE f.follower_id = :current_user_id
ORDER BY m.created_at DESC
LIMIT 20 OFFSET :offset;
```

**Why pull?** See DECISIONS.md entry 5. Short version: fan-out on write is expensive at high follower counts. Pull is correct and simple at this scale.

### Three Feed Endpoints

| Endpoint | Source | Cache key | TTL |
|----------|--------|-----------|-----|
| `GET /feed/global` | All messages, `ORDER BY created_at DESC` | `global_feed` | 300 seconds |
| `GET /feed/following` | JOIN follows→messages for current user | `follow_feed:{user_id}` | 120 seconds |
| `GET /feed/{username}` | Messages by specific user | _(not cached)_ | — |

### Redis Caching Strategy

**Global feed cache (`global_feed`):**

- Cached as a JSON string of the first page of results.
- Any `POST /messages` invalidates this key immediately (Redis `DEL`).
- Why: the global feed changes with every post. Short TTL alone would mean up to 5 minutes of stale data; explicit invalidation keeps it fresh.

**Following feed cache (`follow_feed:{user_id}`):**

- Per-user cache. Key includes the requesting user's UUID.
- Invalidated when any user they follow posts a message. On `POST /messages`, we look up the poster's followers and delete their `follow_feed:{follower_id}` keys.
- TTL is 2 minutes as a backstop. Explicit invalidation is best-effort; BL-008 tracks making this more robust.

**Cache miss path:**

```
Request → Check Redis → miss → Query Postgres → Serialize → SET in Redis (with TTL) → Return
```

**Cache hit path:**

```
Request → Check Redis → hit → Deserialize → Return
```

### Trade-offs vs Fan-out on Write

| | Pull (current) | Fan-out on Write (backlog BL-001) |
|---|---|---|
| Read cost | JOIN query per request (mitigated by cache) | O(1) inbox read from Redis |
| Write cost | O(1) — just insert the message | O(followers) — write to N inboxes |
| Complexity | Low | Higher — needs inbox data structure, delivery guarantees |
| When it breaks | When follower counts grow large (>10K) and cache miss rate is high | When posting latency matters (celebrity problem) |

**Upgrade trigger:** When p95 `GetFollowFeed` latency exceeds acceptable thresholds at scale, or when a single user's follower count routinely causes slow JOIN queries. Backlogged as BL-001.

---

## 6. Database

### Schema

**`users`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key, generated |
| `username` | VARCHAR(50) | Unique, indexed |
| `password_hash` | TEXT | bcrypt hash |
| `created_at` | TIMESTAMPTZ | Server default |

**`messages`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `user_id` | UUID | FK → `users.id` ON DELETE CASCADE |
| `content` | VARCHAR(140) | Enforced at DB level |
| `created_at` | TIMESTAMPTZ | Server default |

**`follows`**

| Column | Type | Notes |
|--------|------|-------|
| `follower_id` | UUID | FK → `users.id` ON DELETE CASCADE |
| `followed_id` | UUID | FK → `users.id` ON DELETE CASCADE |
| `created_at` | TIMESTAMPTZ | Server default |
| _(PK)_ | `(follower_id, followed_id)` | Composite, enforces uniqueness |

### Index Rationale

| Index | Table | Columns | Purpose |
|-------|-------|---------|---------|
| Implicit PK index | `users` | `id` | User lookup by UUID on every auth check |
| Unique index | `users` | `username` | Sign-in lookup, follow-by-username lookup |
| Composite index | `messages` | `(user_id, created_at DESC)` | `GET /feed/{username}` — fetch a user's messages in reverse-chron order without a full table scan |
| Global feed index | `messages` | `created_at DESC` | `GET /feed/global` — sort all messages by time efficiently |
| Follow lookup (follower) | `follows` | `follower_id` | `GetFollowFeed` JOIN — find all users that a given user follows |
| Follow lookup (followed) | `follows` | `followed_id` | Find all followers of a user (needed for feed cache invalidation) |
| Composite PK | `follows` | `(follower_id, followed_id)` | Enforces no duplicate follows; also serves as the follower-side index |

---

## 7. How to Run

### Prerequisites

- Docker and Docker Compose installed
- `.env` file in the project root (see below)

### Environment Variables

Create a `.env` file:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/joon
REDIS_URL=redis://redis:6379/0
SECRET_KEY=your-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

### Start the stack

```bash
docker compose up --build
```

This starts three containers:
- `app` — FastAPI on port 8000
- `db` — PostgreSQL 16 on port 5432
- `redis` — Redis 7 on port 6379

### Access the API

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **Base URL:** http://localhost:8000

### Stop the stack

```bash
docker compose down
```

To also wipe volumes (clears the database):

```bash
docker compose down -v
```

---

## 8. How to Test

### Run all tests

```bash
uv run pytest
```

### Run with verbose output

```bash
uv run pytest -v
```

### Run a specific test file

```bash
uv run pytest tests/test_auth.py -v
```

### What is tested

- **Auth flows** — sign-up, sign-in, sign-out, token blacklist enforcement, duplicate username rejection
- **Message posting** — happy path, over-140-char rejection, unauthenticated rejection
- **Follow / unfollow** — follow a user, unfollow, follow self rejection, duplicate follow rejection
- **Feed endpoints** — global feed returns messages, following feed returns only followed users' posts, per-user feed scoped correctly
- **Redis cache** — cache hit/miss behavior, invalidation on post
- **Error cases** — 401 on missing/expired/blacklisted token, 404 on unknown username, 422 on bad input

Tests use an in-memory SQLite database (via SQLAlchemy async) and a mock Redis client to avoid requiring live infrastructure.
