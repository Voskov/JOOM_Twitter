from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from app.db.database import get_db
from app.db.redis_client import get_redis_pool
from app.db.models import User
from app.dependencies import get_current_user
from app.schemas.message import FeedResponse
from app.services.feed_service import get_global_feed, get_follow_feed, get_user_feed

router = APIRouter(prefix="/feed", tags=["feeds"])


@router.get("/global", response_model=FeedResponse)
async def global_feed(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis_pool)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> FeedResponse:
    return await get_global_feed(db=db, redis=redis, limit=limit, offset=offset)


@router.get("/following", response_model=FeedResponse)
async def following_feed(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis_pool)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> FeedResponse:
    return await get_follow_feed(current_user=current_user, db=db, redis=redis, limit=limit, offset=offset)


@router.get("/{username}", response_model=FeedResponse)
async def user_feed(
    username: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis_pool)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> FeedResponse:
    return await get_user_feed(username=username, db=db, limit=limit, offset=offset)
