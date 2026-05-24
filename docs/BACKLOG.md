# Backlog

Items not implemented due to time/scope. Ordered by priority.

## High Priority

- [ ] **BL-001** Fan-out on write for feeds — on PostMessage, write to per-user inbox sets in Redis. Eliminates JOIN on GetFollowFeed. Required when follower counts grow large.
- [ ] **BL-002** Cursor-based pagination — replace `offset` with `after_id` (UUID, ULID, or created_at cursor). Eliminates full-table scans for deep pages.
- [ ] **BL-003** Rate limiting — `slowapi` middleware. PostMessage: 10/min per user. SignIn: 5/min per IP. Prevents abuse and credential stuffing.

## Medium Priority

- [ ] **BL-004** Soft delete messages — `is_deleted BOOLEAN DEFAULT false` column, filter in all feed queries. Currently delete is not exposed but the schema should support it.
- [ ] **BL-005** Prometheus metrics — `/metrics` endpoint via `prometheus-fastapi-instrumentator`. Track: request latency p50/p95/p99, Redis cache hit rate, active connections.
- [ ] **BL-006** Full-text message search — `tsvector` column on `messages.content`, GIN index, `GET /search?q=...` endpoint.
- [ ] **BL-007** GET aliases for write endpoints — spec allows all calls as HTTP GETs for browser simplicity. Add `GET /feed/post?content=...` etc.
- [ ] **BL-008** Follow/unfollow cache invalidation — currently follow/unfollow does not invalidate `follow_feed:{user_id}`. TTL-expiry handles it (2min), but explicit invalidation improves consistency.

## Low Priority / Future

- [ ] **BL-009** WebSocket real-time feed — `GET /ws/feed` endpoint, push new messages to connected clients on PostMessage.
- [ ] **BL-010** Like / retweet — `likes` table (user_id, message_id, PK composite), `retweets` table. Feed to include like counts.
- [ ] **BL-011** Admin API — ban user (soft delete + blacklist all tokens), delete message, list reports.
- [ ] **BL-012** CI/CD pipeline — GitHub Actions: lint (ruff) → typecheck (mypy) → test (pytest) → build Docker → push to registry.
- [ ] **BL-013** HTTPS / TLS termination — nginx reverse proxy config or Caddy for automatic cert management.
- [ ] **BL-014** Horizontal scaling guide — Redis cluster config, Postgres read replicas, sticky sessions vs stateless JWT trade-off doc.
