from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis
from app.db.models import User, Message, Follow
from app.schemas.message import MessageOut, FeedResponse
from app.core.exceptions import NotFoundError

GLOBAL_FEED_KEY = "global_feed"
GLOBAL_FEED_TTL = 300  # 5 min
FOLLOW_FEED_TTL = 120  # 2 min

def _follow_feed_key(user_id: object) -> str:
    return f"follow_feed:{user_id}"

def _serialize_items(rows: list[tuple[Message, str]]) -> list[MessageOut]:
    return [
        MessageOut(id=msg.id, username=username, content=msg.content, created_at=msg.created_at)
        for msg, username in rows
    ]

async def get_user_feed(
    username: str,
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> FeedResponse:
    user_result = await db.execute(select(User).where(User.username == username))
    if user_result.scalar_one_or_none() is None:
        raise NotFoundError(f"User '{username}' not found")

    stmt = (
        select(Message, User.username)
        .join(User, Message.user_id == User.id)
        .where(User.username == username)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()
    items = [MessageOut(id=r[0].id, username=r[1], content=r[0].content, created_at=r[0].created_at) for r in rows]
    return FeedResponse(items=items, total=len(items))

async def get_follow_feed(
    current_user: User,
    db: AsyncSession,
    redis: aioredis.Redis,
    limit: int = 50,
    offset: int = 0,
) -> FeedResponse:
    cache_key = _follow_feed_key(current_user.id)
    if offset == 0:
        cached = await redis.get(cache_key)
        if cached:
            items_data = json.loads(cached)
            items = [MessageOut(**item) for item in items_data[:limit]]
            return FeedResponse(items=items, total=len(items))

    stmt = (
        select(Message, User.username)
        .join(User, Message.user_id == User.id)
        .join(Follow, Follow.followed_id == Message.user_id)
        .where(Follow.follower_id == current_user.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()
    items = [MessageOut(id=r[0].id, username=r[1], content=r[0].content, created_at=r[0].created_at) for r in rows]

    if offset == 0:
        serializable = [item.model_dump(mode="json") for item in items]
        await redis.setex(cache_key, FOLLOW_FEED_TTL, json.dumps(serializable))

    return FeedResponse(items=items, total=len(items))

async def get_global_feed(
    db: AsyncSession,
    redis: aioredis.Redis,
    limit: int = 50,
    offset: int = 0,
) -> FeedResponse:
    if offset == 0:
        cached = await redis.get(GLOBAL_FEED_KEY)
        if cached:
            items_data = json.loads(cached)
            items = [MessageOut(**item) for item in items_data[:limit]]
            return FeedResponse(items=items, total=len(items))

    stmt = (
        select(Message, User.username)
        .join(User, Message.user_id == User.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()
    items = [MessageOut(id=r[0].id, username=r[1], content=r[0].content, created_at=r[0].created_at) for r in rows]

    if offset == 0:
        serializable = [item.model_dump(mode="json") for item in items]
        await redis.setex(GLOBAL_FEED_KEY, GLOBAL_FEED_TTL, json.dumps(serializable))

    return FeedResponse(items=items, total=len(items))

async def invalidate_global_feed_cache(redis: aioredis.Redis) -> None:
    await redis.delete(GLOBAL_FEED_KEY)
