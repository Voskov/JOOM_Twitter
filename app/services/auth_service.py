from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User


async def sign_up(username: str, password: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none() is not None:
        raise ConflictError(f"Username '{username}' already taken")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def sign_in(username: str, password: str, db: AsyncSession) -> tuple[str, str]:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid username or password")
    return create_access_token(subject=username)


async def sign_out(jti: str, redis: aioredis.Redis) -> None:
    ttl = settings.access_token_expire_minutes * 60
    await redis.setex(f"blacklist:{jti}", ttl, "1")
