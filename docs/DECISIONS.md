# Architectural Decision Log

Decision records for the key engineering choices made during implementation. Each record captures the context, the choice, and the trade-offs accepted. Written in ADR-lite format.

---

## Decision 1: Async runtime (FastAPI + asyncio) over synchronous frameworks

**Status:** Accepted

**Context:**
The spec called for high concurrency on a read-heavy social API. The dominant bottleneck for this workload is I/O: Postgres queries and Redis calls. In a synchronous WSGI model, each in-flight request holds an OS thread for its entire duration — including the time spent blocked waiting for I/O. Threads are expensive (~1MB stack each), and the GIL prevents true CPU parallelism in CPython. The thread-per-request ceiling is a real constraint at scale.

Flask was rejected because its async support (`flask[async]`) wraps `async def` handlers in a thread pool — you get the syntactic cost of async without the concurrency benefit. Django carries significant weight (ORM, admin, templates, user model) that adds zero value for a pure JSON API.

**Decision:**
Use FastAPI with `async def` route handlers running on an asyncio event loop (uvicorn ASGI server). All I/O — Postgres via `asyncpg`, Redis via `redis-py` async client — is genuinely non-blocking. The one CPU-bound operation (bcrypt password hashing) is offloaded to the thread pool executor via `asyncio.to_thread()`.

**Consequences:**
- A single event loop thread can interleave thousands of concurrent in-flight requests, all blocked on I/O simultaneously.
- Debugging is harder: stack traces through `await` chains are long and can be confusing.
- Any accidentally blocking call in a route handler (e.g., a synchronous DB driver) stalls the entire event loop, not just one request. Requires discipline in library selection.
- Test infrastructure requires async-aware tooling: `pytest-asyncio` and `httpx.AsyncClient` instead of `TestClient`.

---

## Decision 2: JWT authentication with dual delivery (header + httpOnly cookie)

**Status:** Accepted

**Context:**
The API needs to authenticate requests. Session tokens require a session store — every request must look up the session ID to find the user identity, adding a mandatory round-trip. Stateless JWTs carry the identity in the token itself and can be validated by any app server using just the secret key, with no shared state. This makes horizontal scaling straightforward.

Browser clients have a different security concern: storing a JWT in `localStorage` or `sessionStorage` exposes it to JavaScript, making it vulnerable to XSS attacks. An `httpOnly` cookie is inaccessible to JavaScript, closing that attack vector.

**Decision:**
Use stateless JWT (HS256) with dual delivery: `Authorization: Bearer <token>` header for API clients and Swagger UI, and an `httpOnly; SameSite=Lax` cookie for browser clients. The auth middleware checks the header first and falls back to the cookie. Either one is sufficient to authenticate a request.

HS256 was chosen over RS256 because this is a single-service deployment. RS256's asymmetric key distribution is valuable when multiple independent services need to verify the same tokens without sharing a secret. With one service, HS256 is simpler and computationally cheaper with equivalent security given a strong secret.

**Consequences:**
- Stateless validation means no session store lookup overhead on the primary auth path.
- Horizontal scaling is trivial: any app instance can validate any token.
- Browser clients get XSS-resistant token storage via httpOnly cookies at no extra implementation cost.
- Cookie delivery requires HTTPS in production (httpOnly provides no protection over plaintext HTTP). Tracked as BL-013.
- HS256 requires the secret key to remain secret. If the key is compromised, all tokens issued with it are compromised. Key rotation requires re-issuing all active tokens.

---

## Decision 3: Redis for JWT token blacklist, not Postgres

**Status:** Accepted

**Context:**
Stateless JWT cannot natively revoke a token before its expiry. Sign-out must have real semantics: a signed-out token cannot be reused, even within its TTL window. The `jti` (JWT ID) claim — a unique UUID embedded in each token at issuance — provides the handle for revocation.

The blacklist check runs on every single authenticated request, before any application logic. It is on the hot path. The question is where to store it.

**Decision:**
Store the blacklist in Redis as `blacklist:{jti}` keys with a TTL equal to the token's remaining lifetime. On sign-out, compute the remaining lifetime from the token's `exp` claim and call `SETEX blacklist:{jti} <remaining_seconds> "1"`. On every auth check, call `GET blacklist:{jti}` and reject if the key exists.

Redis GET latency is sub-millisecond (0.1–0.5ms locally, 1–2ms over a network). Postgres query round-trip is 5–20ms even for a trivial index lookup. At 500 req/s, using Postgres for this lookup would add 2,500–10,000ms of cumulative latency per second across all requests.

TTL-based key expiry means Redis automatically purges blacklist entries when the corresponding token would have expired anyway. No cleanup job is needed. The blacklist stays bounded to the number of tokens that have been explicitly revoked but not yet naturally expired — typically a very small set.

**Consequences:**
- Auth check overhead is sub-millisecond rather than 5–20ms.
- No cleanup job or background worker needed to purge expired blacklist entries.
- Redis is a second infrastructure dependency. Mitigated by the fact that Redis is already required for feed caching, so no new dependency is introduced.
- If Redis becomes unavailable, the blacklist check fails. The auth middleware must decide: fail open (allow potentially revoked tokens) or fail closed (reject all authenticated requests). Currently configured to fail closed — a Redis outage makes auth checks fail, which is safer.

---

## Decision 4: Pull model (fan-out on read) for following feed

**Status:** Accepted

**Context:**
Delivering a user's following feed requires knowing all users they follow and fetching their recent messages. Two classic approaches exist:

**Fan-out on write (push):** When a user posts, immediately write that post to each follower's inbox (a Redis sorted set or Postgres table per user). GetFollowFeed reads the inbox directly — O(1) per read.

**Fan-out on read (pull):** When a user posts, insert the message into the messages table. GetFollowFeed JOINs follows→messages at query time — O(followers × messages) in the worst case, mitigated by indexes and caching.

Push is appealing for read performance but front-loads the cost onto the write path. A user with 100,000 followers posting a message triggers 100,000 inbox writes synchronously. Either the POST handler blocks until all writes complete (terrible latency), or you queue them asynchronously (requires a job queue, delivery guarantees, dead-letter handling, and a way to surface delivery failures).

**Decision:**
Implement pull (fan-out on read). On POST /messages, insert the message and invalidate relevant Redis cache keys. GetFollowFeed executes a single SQL JOIN with Postgres, cached in Redis per user with a 2-minute TTL.

At current scale (small-to-medium follower counts, no celebrity accounts), the JOIN is fast with appropriate indexes. Redis caching further reduces how often the JOIN runs at all. The write path remains O(1).

**Consequences:**
- POST /messages is simple and fast: one Postgres insert, cache invalidation, done.
- GetFollowFeed requires a JOIN on cache miss. With the composite index on `follows(follower_id)` and `messages(user_id, created_at DESC)`, this is acceptably fast at MVP scale.
- At large follower counts (>10K per user), cache miss rate can spike and JOIN latency becomes a problem. Upgrade trigger is defined: when p95 GetFollowFeed latency exceeds threshold, implement fan-out on write with async inbox population. Backlogged as BL-001.
- Follow/unfollow does not invalidate the cached following feed immediately (BL-008). TTL expiry handles eventual consistency.

---

## Decision 5: Redis caching strategy for feeds

**Status:** Accepted

**Context:**
Feed queries are the most expensive database operations: they involve JOINs, ordering by timestamp, and return multiple rows. These queries run on every feed request. At any meaningful load, hitting Postgres cold on every feed request would saturate the DB quickly.

The question is not whether to cache feeds, but how: what to cache, for how long, and how to invalidate.

**Decision:**
Cache two feed types in Redis:

**Global feed** (`global_feed` key, 300s TTL): All messages ordered by creation time. This is the most expensive query (full messages table scan) and the most shared (identical result for all users). Explicit invalidation on every POST /messages ensures the cache stays fresh. The 300s TTL is a backstop for bugs or missed invalidations.

**Per-user following feed** (`follow_feed:{user_id}` key, 120s TTL): Personalized per user. Invalidated when any user they follow posts a message — on POST, the posting user's followers are queried and their cache keys are deleted. TTL is shorter (2 minutes) because per-user caches are harder to invalidate exhaustively.

Per-user message feeds (`GET /feed/{username}`) are not cached. They are lower-traffic profile views, the query is simpler (no JOIN), and the invalidation logic would add complexity for marginal gain.

Cache misses fall back to Postgres. Cache hits return the serialized JSON directly without touching Postgres.

**Consequences:**
- Cold-start penalty: the first request after a cache miss hits Postgres. Subsequent requests within the TTL window are served from Redis.
- Global feed is typically fresh within seconds of a new post (explicit invalidation). Following feed can be up to 2 minutes stale on cache miss, or if invalidation is incomplete (BL-008).
- Cache keys are stored as JSON-serialized arrays. Serialization/deserialization overhead is small relative to the Postgres query cost it replaces.
- Only the first page (offset=0) is cached. Paginated requests always hit Postgres. This is intentional — deep page caching would require per-offset keys, multiplying cache storage.

---

## Decision 6: UUID primary keys

**Status:** Accepted

**Context:**
Postgres supports integer sequences (SERIAL, BIGSERIAL) and UUIDs as primary key types. Integer sequences are smaller (4 or 8 bytes vs 16 bytes for UUID), have predictable sort order, and generate sequential values that are friendlier to B-tree index performance (no fragmentation from random insertion order).

UUIDs have different properties: they are globally unique without coordination between nodes, they do not leak record counts or creation order, and they work well as identifiers in distributed systems where IDs may be generated outside the database.

**Decision:**
Use `UUID` primary keys for all three tables (users, messages, follows). UUIDs are generated by the application layer (`uuid.uuid4()`) before insertion.

The `sub` claim in the JWT payload carries the username (not the UUID) for human readability in tokens, but internal system references (session checks, feed queries, cache keys) all use the UUID.

**Consequences:**
- No information leakage: `user_id` values in API responses do not reveal how many users exist or approximate creation order.
- Random UUID insertion causes B-tree index fragmentation over time as pages split to accommodate out-of-order keys. At current scale, this is not measurable. At very large scale, ULIDs (time-ordered UUIDs) would be worth the migration.
- 16-byte PKs vs 8-byte BIGINT adds storage overhead on all FK columns. Minor at this scale.
- No coordination required between distributed nodes if IDs are ever generated outside the DB. Currently moot but provides headroom.

---

## Decision 7: Alembic for schema migrations

**Status:** Accepted

**Context:**
The database schema needs to evolve over time. Creating tables manually from SQL scripts or using `Base.metadata.create_all()` directly in the application startup both work for development but fail in production scenarios: they have no way to apply incremental changes to an existing database without destroying and recreating it.

**Decision:**
Use Alembic for schema version management. Migration scripts live in `alembic/versions/`. Each script has `upgrade()` and `downgrade()` functions. `alembic upgrade head` applies all pending migrations; `alembic downgrade -1` rolls back the last one.

The initial migration creates all three tables with their columns, indexes, and foreign key constraints. Subsequent migrations will add columns, add indexes, or modify constraints incrementally without touching existing data.

Alembic integrates naturally with SQLAlchemy: it can compare the in-memory model state (SQLAlchemy ORM classes) against the current DB state and generate migration scripts automatically (`alembic revision --autogenerate`). These auto-generated scripts are reviewed before committing — autogenerate does not always get it right, especially for custom index options.

**Consequences:**
- Database state is reproducible and auditable. Every schema change is a versioned, reversible script.
- Deployments are explicit: `alembic upgrade head` must be run (either in a pre-start hook or manually) when deploying a version with schema changes.
- The `alembic_version` table in Postgres tracks the current migration head. Concurrent deployments running migrations simultaneously can cause lock contention — mitigated by running migrations from a single deployment job, not from every app instance on startup.
- Autogenerate cannot detect all changes (e.g., renamed columns look like drop + add). Migrations require review.

---

## Decision 8: uv for packaging and dependency management

**Status:** Accepted

**Context:**
Python packaging tooling has been fragmented for years. The legacy choice (pip + requirements.txt) has no dependency resolver and produces non-reproducible installs. Poetry was the dominant modern choice but has accumulated known issues: slow resolution on complex trees, cross-platform lock file inconsistencies, and occasional resolver bugs requiring manual intervention.

A new project in 2024–2025 has a better option.

**Decision:**
Use `uv` for all packaging operations: dependency installation (`uv sync`), running project scripts (`uv run pytest`, `uv run alembic`), and lock file generation. The `pyproject.toml` defines dependencies; `uv.lock` pins the exact resolved versions.

`uv` is written in Rust and is 10–100x faster than pip or pip-tools for installation. Its resolver is correct and deterministic. The `uv.lock` file is cross-platform. The toolchain is completed by `ruff` (linting and formatting, also from Astral) and `mypy` (type checking).

**Consequences:**
- `uv sync` installs the full environment in seconds, even on a clean machine. CI install times drop significantly compared to pip.
- `uv.lock` ensures that every developer and every CI run uses the exact same dependency versions. "Works on my machine" dependency divergence is eliminated.
- `uv` is newer than Poetry or pip. Some edge cases may have less community documentation. Not a practical problem at this project's dependency complexity.
- The `uv run` wrapper handles virtual environment activation transparently. No need to manually activate a venv before running commands.
- If `uv` becomes unavailable or unsupported, `pyproject.toml` is standard PEP 517 and compatible with pip. The lock file is uv-specific but the project is not otherwise locked in.
