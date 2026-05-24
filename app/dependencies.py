from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models import User
from app.db.redis_client import get_redis_pool

# Optional Bearer — won't auto-raise 403 if missing (we handle cookie fallback)
_bearer = HTTPBearer(auto_error=False)


async def get_redis(redis: aioredis.Redis = Depends(get_redis_pool)) -> aioredis.Redis:
    return redis


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User:
    token: str | None = None
    if credentials is not None:
        token = credentials.credentials
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]

    if token is None:
        raise UnauthorizedError("Authentication required")

    try:
        payload = decode_access_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    jti: str | None = payload.get("jti")
    username: str | None = payload.get("sub")
    if not jti or not username:
        raise UnauthorizedError("Invalid token claims")

    if await redis.exists(f"blacklist:{jti}"):
        raise UnauthorizedError("Token has been revoked")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User not found")
    return user
