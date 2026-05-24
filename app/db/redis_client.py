from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

redis_pool: aioredis.Redis | None = None


async def get_redis_pool() -> aioredis.Redis:
    global redis_pool
    if redis_pool is None:
        redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return redis_pool


async def close_redis_pool() -> None:
    global redis_pool
    if redis_pool is not None:
        await redis_pool.aclose()
        redis_pool = None
