# Chain of Thought — Engineering Decisions

This document captures the reasoning behind key decisions as I worked through the design. It is not a polished retrospective — it is closer to the actual mental process: what I considered, what I rejected, what trade-offs I was willing to live with, and where I know I left technical debt on the table.

---

## Starting point: reading the spec

The spec said "Twitter-like API", "high concurrency", "low latency on reads", and listed the core features. Three things jumped out immediately:

1. The workload is almost entirely I/O-bound and read-heavy. Social feeds are mostly reads.
2. The spec wanted feeds, which immediately raises the fan-out question.
3. "High concurrency" means the synchronous WSGI model is going to be a problem.

These three observations drove most of the architecture before I wrote a single line of code.

---

## Why FastAPI, and why not the obvious alternatives

I briefly considered Flask and Django before ruling them out.

**Flask** has been the default lightweight Python API framework for years, but its async story is a hack. `flask[async]` wraps `async def` route handlers by spinning them up in a thread pool via Quart under the hood. You write async code, but Flask secretly makes it synchronous again. You get the syntactic complexity of async without the concurrency benefits. That is strictly worse than just writing synchronous Flask. No.

**Django** is the nuclear option — it ships with an ORM, an admin interface, a template engine, a form system, and a user model. We need none of that. Django's async support has improved but still has subtleties around the ORM and middleware stack. The weight-to-utility ratio is terrible for a pure JSON API.

**FastAPI** wins on every axis that matters here:
- `async def` route handlers run directly on the event loop. No thread-wrapping.
- Pydantic v2 validation means request schemas are type-annotated Python classes. No serialization code to write.
- OpenAPI generation is automatic. The spec says "document your API" — FastAPI does this for free.
- The dependency injection system (`Depends()`) is genuinely good. DB sessions, Redis clients, and the current-user extraction all compose cleanly.

The one place I was cautious: FastAPI's documentation has gaps, especially for the async ORM patterns with SQLAlchemy 2.0. I expected to spend time debugging session lifecycle issues, and I did. That's the honest trade-off.

---

## The async decision was not a performance gamble

When people say "use async," it can sound like premature optimization — "make it fast before it's slow." That is not what happened here.

The spec explicitly asked for high concurrency. A synchronous server model has a hard ceiling: you cannot have more concurrent requests than you have threads. CPython's GIL makes CPU-bound parallelism worse, not better. The moment the bottleneck is I/O (and for a DB-backed API, it always is), threads become pure overhead.

The async model's costs are real, though:
- Debugging is harder. Stack traces through `await` chains are long.
- Any blocking call in a route handler freezes the entire event loop — not just that one request.
- Testing async code requires more boilerplate (`pytest-asyncio`, `AsyncClient`).

The one place I hit the blocking-call problem directly was bcrypt. `passlib.hash()` is CPU-bound and can take 300–400ms at work factor 12. Calling it directly inside an `async def` handler would block the event loop for hundreds of milliseconds on every sign-in. The fix is `asyncio.to_thread()`, which offloads the call to a thread pool executor without blocking the loop. This is the correct pattern and it works well.

---

## PostgreSQL and Redis together: why not one or the other

The question I asked myself early: do I need both? Can I get away with just Postgres?

**Redis only** (no Postgres): Not viable. Redis is an in-memory store. Persistence is possible but complex, and it is not designed for relational queries with JOINs, foreign key enforcement, or transactional writes. The social graph — who follows whom — is inherently relational. I need referential integrity. If a user is deleted, their follows and messages should cascade. Redis cannot do that.

**Postgres only** (no Redis): Technically possible, but painful. Without Redis:
- The JWT blacklist goes into a `token_blacklist` table. That table gets queried on every single authenticated request. At any real load, that is a very hot lookup on a table that grows monotonically until you add a cleanup job.
- Feed caching requires either a separate caching table (now you have invalidation logic in SQL) or accepting that every feed request hits Postgres cold.

Redis earns its place specifically because it is exceptional at two things: sub-millisecond key lookups (blacklist) and short-lived cached results (feeds). Using Postgres for these would work but would be significantly slower and more complex.

The combination of Postgres for truth and Redis for speed is a well-established pattern. I am not being clever here — I am using the right tool for each problem.

---

## JWT design: HS256, not RS256, and why the jti blacklist matters

**HS256 vs RS256.** For a single-service deployment, HS256 is correct. RS256 is asymmetric: the private key signs tokens, and any service with the public key can verify them. This matters when multiple independent services need to validate the same tokens — for example, a microservices architecture where a separate auth service issues tokens that the message service, feed service, and notification service all need to verify independently.

We have one service. There is no key distribution problem to solve. HS256 with a strong secret is simpler, has slightly lower CPU cost, and is perfectly secure when the secret is kept secret. If this evolved into a multi-service architecture, the migration path is clear: swap in RS256, distribute the public key, keep the rest of the token structure the same.

**The blacklist trade-off.** Stateless JWT is elegant: validate the signature, check the expiry, done. No shared state. But "stateless" means you cannot revoke a token. If a user signs out or a token is compromised, it stays valid until it naturally expires.

A one-hour TTL is a reasonable balance — the exposure window is bounded. But one hour is still one hour. Real sign-out semantics require the ability to invalidate a specific token.

The `jti` (JWT ID) claim is the handle. Each token gets a unique UUID at creation time. Sign-out writes `blacklist:{jti}` to Redis with a TTL equal to the token's remaining lifetime. Auth checks query Redis for the jti before processing the request.

The key insight is that the blacklist is tiny. It only contains jti entries for tokens that have been explicitly signed out but have not yet expired. Once the token's natural expiry passes, Redis automatically removes the blacklist entry (TTL expires). No cleanup job needed. The blacklist stays bounded to the number of "signed out but not yet expired" tokens, which is typically very small.

**Dual delivery (header + cookie).** I chose to support both `Authorization: Bearer` header and an `httpOnly` cookie. The header is standard for API clients and Swagger UI. The cookie is safer for browser applications — JavaScript cannot read `httpOnly` cookies, so XSS attacks cannot steal the token. Supporting both costs almost nothing and makes the API more flexible.

---

## Feed architecture: why I started with pull

Before writing the feed service, I sketched both approaches:

**Push (fan-out on write):** When a user posts, write that post to every follower's inbox. An inbox is a Redis sorted set keyed by `inbox:{user_id}`, with the message ID as the member and the creation timestamp as the score. GetFollowFeed becomes `ZREVRANGE inbox:{user_id} 0 49` — one O(log n) Redis command.

**Pull (fan-out on read):** When a user posts, insert the message into Postgres. GetFollowFeed runs a JOIN: messages JOIN follows on follower_id. Cache the result in Redis.

The pull model is the right call for a v1 for several reasons:

First, we do not know the follower count distribution. If the test evaluator creates a few users and follows relationships, follower counts will be small and the JOIN will be cheap. If the spec intended celebrity-scale accounts (100K followers), the push model becomes necessary. Starting with pull and adding push later is straightforward. Starting with push adds complexity upfront for a problem we may not have.

Second, the push model has a write amplification problem at high follower counts. Posting with 100,000 followers means 100,000 Redis writes on the critical path of the POST handler. You either block the response until they are done (terrible latency) or queue them asynchronously (now you need a job queue, delivery guarantees, and a way to handle the queue backing up). That is a lot of infrastructure for a feature we do not need yet.

Third, the JOIN query is not expensive at this scale. With proper indexes — specifically the composite index on `(user_id, created_at DESC)` and the follower-side index on `follows(follower_id)` — Postgres executes the JOIN efficiently. Add Redis caching on top and the DB barely gets touched on cache hits.

The upgrade path is clear and tracked in BACKLOG.md as BL-001: when p95 GetFollowFeed latency exceeds a threshold due to large follower counts and cache miss spikes, implement inbox-based fan-out. The current design does not block that migration.

---

## Redis caching: what I cached, what I did not, and why

Not everything should be cached. Caching adds stale data risk and invalidation complexity. I chose two things to cache:

**Global feed.** This is the most expensive query (full table scan on messages ordered by `created_at`) and also the most shared (every authenticated user gets the same result). Invalidation is simple: any new post invalidates the key. TTL is 5 minutes as a backstop, but explicit invalidation on every POST means the global feed is nearly always fresh.

**Per-user following feed.** This is the most expensive personalized query — JOIN across follows and messages for a specific user. Cached per `user_id` with a 2-minute TTL. Invalidation happens when any user they follow posts a message: on POST, look up the poster's followers and delete their cache keys. This is a best-effort invalidation — there is a gap if the follower lookup itself is slow or if we miss some followers. The 2-minute TTL is the fallback for correctness.

**What I did not cache:**
- Per-user message feeds (`GET /feed/{username}`) — these are typically low-traffic profile views. Not worth the invalidation complexity.
- User lookups by username — these are already served by a unique indexed column. Postgres is fast enough.
- Auth checks — the Redis blacklist lookup is itself the fast path. There is nothing to cache over.

The caching strategy is deliberately conservative. It addresses the two highest-frequency, most expensive queries, and no more.

---

## Pagination: offset was a conscious trade-off

Offset pagination is wrong in production. `LIMIT 20 OFFSET 1000` causes Postgres to read and discard 1,000 rows, no matter how good the index is. Deep pages are slow. Concurrent inserts can cause records to shift between pages, leading to duplicates or gaps.

The right approach is keyset pagination: `WHERE created_at < :cursor ORDER BY created_at DESC LIMIT 20`. This is O(1) regardless of page depth because it uses the index as an entry point rather than counting rows.

I chose offset pagination for one reason: time. Cursor pagination requires:
1. A stable, unique ordering column (or composite: `(created_at, id)` to handle ties)
2. A cursor encoding scheme (usually base64 of the last row's values)
3. Updated response schema to include `next_cursor`
4. Updated client API for "give me the page after this cursor"

None of this is hard, but it is not small either. Given the scope of the test and the dataset sizes involved (small test data, shallow pages), offset pagination performs identically to cursor pagination. The technical debt is acknowledged and in the backlog as BL-002.

---

## Schema choices that were deliberate

**UUID primary keys over auto-increment integers.** This was not the default — I chose it. UUID PKs have well-known costs: they are 16 bytes instead of 4 or 8, UUIDs are random so they cause index fragmentation (sequential ULIDs would be better), and they are harder to read in logs and debug sessions.

The benefits: no information leakage (an integer ID leaks your growth rate — "user 5000" tells you you have about 5000 users), better safety for distributed systems where multiple DB nodes might generate IDs independently, and simpler inter-service references. For a stateless JWT system, the `sub` claim holds the user UUID directly — no need to look up an integer ID.

At this scale, the fragmentation concern is negligible. If this scaled to billions of rows, ULIDs would be worth the migration.

**`server_default=func.now()` for `created_at`.** The creation timestamp is set by the database, not the application. This ensures that even if multiple application instances insert records simultaneously, the timestamps are consistent with the DB server's clock, not potentially diverging application-server clocks.

**`ON DELETE CASCADE` on foreign keys.** If a user is deleted, their messages and follows are automatically removed by the database. No application-level cleanup code needed. This is correct by default — dangling rows in `messages` pointing to a deleted user would break queries.

---

## Testing strategy: real test DB, not mocks

The test configuration connects to a real PostgreSQL instance (configurable via `TEST_DATABASE_URL`) and a real Redis instance. It creates the schema fresh for each test session and drops it at teardown.

I considered two alternatives:

**Mock everything.** Mock the DB session, mock Redis. Tests run fast and have no infrastructure dependency. The problem: you end up testing that your mocks behave the way you expect, not that your actual SQL queries return the right results. A subtle query bug — wrong JOIN condition, missing index hint — would pass mocked tests and fail in production.

**SQLite in-memory for the DB.** Faster than a real Postgres instance, no external dependency. The problem: SQLite is not Postgres. SQLite does not support `UUID` column types, does not enforce `VARCHAR(N)` length constraints, has different `TIMESTAMPTZ` handling, and does not support all Postgres index operators. A bug in a Postgres-specific query would not surface in SQLite tests.

Using a real Postgres test database is slower and requires infrastructure, but it is the only way to be confident that the queries actually work. The `docker-compose.test.yml` file sets up the test database alongside the test run for exactly this reason.

For Redis, the same logic applies: a mock Redis would not catch TTL behavior, key encoding bugs, or connection pool exhaustion. The real Redis is used.

**httpx AsyncClient over TestClient.** FastAPI provides a synchronous `TestClient` that runs the ASGI app synchronously. It works, but it does not exercise the async code paths realistically. `httpx.AsyncClient` with `ASGITransport` drives the ASGI interface asynchronously, which tests the actual concurrency behavior of the route handlers.

---

## What I would do differently at 10x scale

These are not hypothetical — they are the natural next steps if this became a real service under real load.

**Read replicas.** All three feed endpoints are reads against Postgres. At scale, reads dominate writes heavily (probably 20:1 or more for a social feed). A read replica (Postgres streaming replication) would let the app route feed queries to the replica and reserve the primary for writes. SQLAlchemy 2.0 supports multiple engine bindings per session.

**Message queue for fan-out.** Once BL-001 is implemented (fan-out on write), the write path needs to be asynchronous to avoid blocking POST /messages while writing to thousands of inboxes. A queue (Redis Streams, RabbitMQ, or Kafka at the extreme) decouples the insert from the fan-out. The API responds immediately; a worker pool processes the fan-out asynchronously. This introduces at-least-once delivery semantics and requires idempotent inbox writes.

**Cursor pagination.** BL-002. Required before any real traffic because offset pagination degrades with page depth and is incorrect under concurrent inserts.

**Connection pooling at the proxy layer.** SQLAlchemy's async engine maintains a connection pool, but for high concurrency, a proxy like PgBouncer in transaction mode dramatically reduces Postgres connection overhead. Postgres connections are expensive (~5MB each); PgBouncer multiplexes many application connections onto a small pool of real Postgres connections.

**Structured logging and distributed tracing.** At scale, you need to trace a request across the event loop, through the DB query, through the Redis call, and back. JSON-structured logs with a correlation ID (set in middleware, threaded through all log calls) make this tractable. OpenTelemetry with a Jaeger or Tempo backend for trace visualization.

**Rate limiting.** BL-003. The PostMessage endpoint at 10/min per user and the SignIn endpoint at 5/min per IP are the most critical. `slowapi` middleware integrates with FastAPI and uses Redis as the counter backend.

---

## Honest assessment of what was left out

The spec asked for a lot in the time available. Things I know are incomplete:

- **Follow feed invalidation on follow/unfollow** (BL-008): If you follow a new user, your `follow_feed` cache still shows the old result for up to 2 minutes. The TTL handles eventual consistency, but it is not ideal.
- **Cursor pagination** (BL-002): Offset pagination is wrong at scale. It works fine for this evaluation.
- **Rate limiting** (BL-003): No limit on how many messages a user can post, or how many sign-in attempts from an IP. A production API would need this immediately.
- **HTTPS** (BL-013): The httpOnly cookie provides no protection over plaintext HTTP. TLS termination is required for production.

These are not architectural sins — they are scope trade-offs. The backlog exists to make the trade-offs explicit rather than pretending they do not exist.
