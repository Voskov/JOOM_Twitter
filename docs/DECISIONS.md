# Architectural Decision Log

Honest notes on the choices made during design. Written as I would explain them to another engineer on the team, not as marketing copy.

---

## 1. Why FastAPI over Flask/Django?

FastAPI was the obvious call for a pure async JSON API.

Django is a batteries-included web framework designed around the MVC pattern and server-rendered HTML. We don't need the ORM abstraction (we're using SQLAlchemy directly), we don't need the admin panel, we don't need the template engine, and the Django async story — while improving — still has rough edges. It adds significant weight for zero benefit here.

Flask is lighter, but its async support is bolted on. `flask[async]` wraps async route handlers by running them in a thread pool, which defeats the point. You lose the concurrency benefits entirely.

FastAPI gives us:
- Native async/await support with no workarounds
- Pydantic v2 for request/response validation with zero boilerplate
- OpenAPI schema generated automatically from type hints — free Swagger UI with no extra code
- Dependency injection that composes cleanly (auth middleware, DB sessions)
- Type-hint-first design that works well with mypy

The only trade-off is that FastAPI is a younger project than Django or Flask, and some edge cases in the docs can be sparse. Not a problem at this scale.

---

## 2. Why async?

This is a social API that is heavily read-biased. At any realistic load, 90%+ of request time is spent waiting — waiting for a Postgres query to return, waiting for a Redis GET. The CPU is idle.

In a synchronous WSGI model (Flask, Django without async), each request holds an OS thread for its entire lifetime. Threads are not cheap: each one consumes around 1MB of stack memory and adds context-switch overhead to the OS scheduler. At 1,000 concurrent users, you need 1,000 threads — most of them sitting idle waiting for network I/O.

With an async event loop, a single thread handles all of that by interleaving coroutines. While coroutine A awaits a DB response, the event loop runs coroutine B. Practically, this means an async server can handle thousands of concurrent connections that a synchronous server would need hundreds of threads to manage.

The gains are not free — async code is harder to reason about, tracing stack traces through await chains is annoying, and any blocking call in a route handler stalls the entire event loop. We handle the one CPU-bound operation (bcrypt hashing) by offloading it to `asyncio.to_thread()`. Everything else — DB, Redis, JSON serialization — is genuinely I/O-bound and benefits from the async model.

---

## 3. Why JWT over session tokens?

Session tokens require a session store. Every authenticated request has to look up the session ID in that store (Postgres or Redis) to find out who the user is. That is an extra round-trip — or it means you're coupling your auth to a specific infrastructure component.

JWT is stateless. The token itself contains the user identity (`sub` claim). Any app server can validate it with just the secret key. No session store, no shared state across instances. This makes horizontal scaling trivial: add more app containers and they all validate tokens independently.

The obvious downside of stateless JWT is that you cannot instantly revoke a token. If a user signs out, the token remains valid until it expires. For a 1-hour TTL, that is up to an hour of exposure.

We address this with a Redis blacklist keyed by `jti` (JWT ID). Sign-out writes the jti to Redis with a TTL matching the token's remaining lifetime. Every auth check queries Redis for the jti before proceeding. This gives us real sign-out semantics without the overhead of a full session store — the blacklist is tiny (one key per active signed-out token) and is queried with a sub-millisecond Redis GET.

The net result: stateless by default (fast, horizontally scalable), with targeted revocation for sign-out (correct behavior without the full session-store cost).

---

## 4. Why Redis for the blacklist vs Postgres?

The blacklist check happens on every single authenticated request, before any application logic runs. It is on the hot path.

If we put the blacklist in Postgres, every request adds a `SELECT` to a `token_blacklist` table. At moderate load (500 req/s), that is 500 extra queries per second — queries that return one row and do almost nothing useful. Postgres query round-trip latency is typically 5–20ms. That adds 5–20ms to every request.

Redis GET latency is sub-millisecond (typically 0.1–0.5ms on localhost, 1–2ms over a local network). It is designed precisely for this kind of high-frequency key lookup.

Additionally, Redis TTL-based key expiry handles cleanup automatically. Expired token blacklist entries vanish on their own. In Postgres, we'd need a cron job or background task to purge old rows.

The only downside: Redis is a second infrastructure dependency. We already need it for feed caching, so this adds no new dependency. The call was easy.

---

## 5. Why pull model for feeds?

There are two classic approaches to feed generation:

**Fan-out on write (push model):** When a user posts, immediately write that post to the inbox of every one of their followers. GetFollowFeed becomes a simple inbox read.

**Fan-out on read (pull model):** When a user posts, just insert the message. When a follower requests their feed, JOIN follows→messages to build the feed at query time.

Fan-out on write sounds appealing because reads are O(1), but the write cost is brutal at scale. If a user has 100,000 followers and posts a message, that is 100,000 Redis writes (or DB inserts) synchronously on the write path. For a celebrity-tier account (1M+ followers), this is a major problem — it either slows posting to a crawl, or you need a complex async job queue with delivery guarantees.

Pull model writes are O(1): insert the message, done. Reads require a JOIN, but at this scale (thousands of users, not millions), that JOIN is fast — especially with the composite index on `(user_id, created_at DESC)`. Redis caching further reduces how often we hit Postgres at all.

The pull model is simpler to implement correctly. For a v1 where we don't know follower count distribution, it is the right default. Fan-out on write is backlogged as BL-001 with clear upgrade criteria: implement it when follower counts grow large enough that cache miss rates drive unacceptable query latency.

---

## 6. Why not paginate with cursors?

Cursor-based pagination (keyset pagination) is better than offset pagination for most production use cases. Offset is O(n) — a `LIMIT 20 OFFSET 1000` query still reads and discards 1,000 rows. Cursor-based pagination with an indexed column (`WHERE created_at < :cursor ORDER BY created_at DESC LIMIT 20`) is O(1) regardless of page depth.

We used offset pagination for one reason: time. Implementing cursor pagination correctly — especially with the UUID/timestamp edge cases and the client-side state management — takes more care than offset. At the current scale (pages are shallow, datasets are small), offset pagination is not a performance problem.

This is technical debt, acknowledged and tracked as BL-002. The right fix is to switch `offset` to an `after_id` cursor, use ULIDs or a composite `(created_at, id)` cursor to handle ties, and update the response schema to include the next cursor. Not hard, just not prioritized here.

---

## 7. Why uv over Poetry?

Poetry was the standard choice for Python packaging for a few years, but it has accumulated known issues: slow dependency resolution on complex trees, inconsistent lock file behavior across platforms, and occasional resolver bugs that require manual workarounds.

`uv` is a Rust-based Python package manager from Astral (the team behind ruff). It is 10–100x faster than pip and pip-tools for installation, has a correct and deterministic resolver, and generates a `uv.lock` file that is compatible with pip if needed. It is backed by PyPA and is on a fast improvement trajectory.

For a new project in 2024+, `uv` is the better starting point. The toolchain (uv + ruff + mypy) is consistent, fast, and well-integrated. Running `uv run pytest` or `uv run ruff check .` is clean and reproducible.

---

## 8. Why composite PK on `follows`?

The `follows` table represents a relationship: user A follows user B. The natural primary key is the pair `(follower_id, followed_id)`. There is no meaningful reason to introduce a surrogate UUID primary key here.

The composite PK does two things at once:
1. Enforces uniqueness at the database level — a user cannot follow another user twice, even if the application has a bug.
2. Creates an index on `(follower_id, followed_id)`, which serves the `GetFollowFeed` JOIN (`WHERE follower_id = :uid`) efficiently.

We also add a separate index on `followed_id` alone, which is needed for the feed invalidation query ("who follows this user?"). The composite PK index covers `follower_id` lookups but not `followed_id` lookups due to column order.

Using a surrogate PK would require adding a separate unique constraint on `(follower_id, followed_id)` anyway, so the composite PK is strictly simpler.

---

## 9. Why VARCHAR(140) on `content`?

Content length is validated in the Pydantic request schema (`max_length=140`). That catches the bad input at the API layer. But defense in depth matters: what if someone bypasses the API and writes directly to the DB? What if a future migration removes the Pydantic validator? What if a background job inserts data with a bug?

Enforcing the 140-character limit at the `VARCHAR(140)` column level means the database itself will reject any content that exceeds the limit, regardless of how it got there. The DB constraint is the last line of defense.

This adds essentially zero overhead (VARCHAR enforces length at write time, no performance cost at read time) and prevents a whole class of silent data integrity bugs.

---

## 10. Security Considerations

A few non-obvious security decisions worth documenting explicitly:

**bcrypt work factor 12.** bcrypt is intentionally slow — that's the point. Work factor 12 means each hash takes ~250–400ms on modern hardware. That makes offline dictionary attacks and credential stuffing attacks computationally expensive. The downside is that sign-in is slower. This is the correct trade-off: user experience suffers by hundreds of milliseconds, but attacker economics break down. Work factor is configurable; bump to 13 or 14 as hardware gets faster.

**httpOnly cookie.** The session cookie is flagged `httpOnly`, which means JavaScript cannot read it. This closes the XSS-to-cookie-theft attack: even if an attacker injects JavaScript into the page, they cannot exfiltrate the session token. `samesite="lax"` prevents the cookie from being sent on cross-site requests (CSRF mitigation).

**HTTPS required in production.** The httpOnly cookie provides no protection if the token is sent over plaintext HTTP — a network observer can read it. TLS termination (nginx, Caddy, or a load balancer) is required before exposing this to the internet. Tracked as BL-013.

**No raw passwords stored or logged.** The raw password is used exactly once: passed to `bcrypt.checkpw()` at sign-in, or `bcrypt.hashpw()` at sign-up. It is never written to the database, never included in log output, never returned in an API response. This is a basic hygiene requirement but worth stating explicitly for audit purposes.

**JTI-based revocation.** See decision 3. The Redis blacklist ensures sign-out is real and tokens cannot be replayed after logout.
