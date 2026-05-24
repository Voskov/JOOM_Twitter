from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import User
from app.dependencies import get_current_user
from app.services.social_service import follow_user, unfollow_user

router = APIRouter(prefix="/social", tags=["social"])


@router.post("/follow/{username}")
async def follow(
    username: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    await follow_user(username, current_user, db)
    return {"message": f"Now following {username}"}


@router.post("/unfollow/{username}")
async def unfollow(
    username: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    await unfollow_user(username, current_user, db)
    return {"message": f"Unfollowed {username}"}
