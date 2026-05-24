from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from app.db.database import get_db
from app.db.models import User
from app.db.redis_client import get_redis_pool
from app.dependencies import get_current_user
from app.schemas.message import MessageOut
from app.services.social_service import post_message

router = APIRouter(prefix="/messages", tags=["messages"])


class MessageCreate(BaseModel):
    content: str = Field(max_length=140, min_length=1)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MessageOut)
async def create_message(
    body: MessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis_pool)],
) -> MessageOut:
    msg = await post_message(body.content, current_user, db, redis)
    return MessageOut(
        id=msg.id,
        username=current_user.username,
        content=msg.content,
        created_at=msg.created_at,
    )
