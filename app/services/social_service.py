from __future__ import annotations
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import redis.asyncio as aioredis
from app.db.models import User, Message, Follow
from app.core.exceptions import NotFoundError, ConflictError, ValidationError
from app.services.feed_service import invalidate_global_feed_cache


async def post_message(
    content: str,
    current_user: User,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> Message:
    if len(content) > 140:
        raise ValidationError("Message exceeds 140 characters")
    if not content.strip():
        raise ValidationError("Message cannot be empty")
    msg = Message(user_id=current_user.id, content=content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    await invalidate_global_feed_cache(redis)
    result = await db.execute(
        select(Follow.follower_id).where(Follow.followed_id == current_user.id)
    )
    follower_ids = result.scalars().all()
    if follower_ids:
        keys = [f"follow_feed:{fid}" for fid in follower_ids]
        await redis.delete(*keys)
    return msg


async def follow_user(
    username_to_follow: str,
    current_user: User,
    db: AsyncSession,
) -> None:
    if username_to_follow == current_user.username:
        raise ConflictError("Cannot follow yourself")
    result = await db.execute(select(User).where(User.username == username_to_follow))
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError(f"User '{username_to_follow}' not found")
    existing = await db.execute(
        select(Follow).where(
            Follow.follower_id == current_user.id,
            Follow.followed_id == target.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"Already following '{username_to_follow}'")
    follow = Follow(follower_id=current_user.id, followed_id=target.id)
    db.add(follow)
    await db.commit()


async def unfollow_user(
    username_to_unfollow: str,
    current_user: User,
    db: AsyncSession,
) -> None:
    result = await db.execute(select(User).where(User.username == username_to_unfollow))
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError(f"User '{username_to_unfollow}' not found")
    result2 = await db.execute(
        delete(Follow).where(
            Follow.follower_id == current_user.id,
            Follow.followed_id == target.id,
        )
    )
    if result2.rowcount == 0:
        raise NotFoundError(f"Not following '{username_to_unfollow}'")
    await db.commit()
