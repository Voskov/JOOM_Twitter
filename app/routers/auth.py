from __future__ import annotations

from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models import User
from app.db.redis_client import get_redis_pool
from app.dependencies import get_current_user
from app.schemas.auth import SignInRequest, SignUpRequest, TokenResponse
from app.schemas.user import UserOut
from app.services.auth_service import sign_in, sign_out, sign_up

router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_jti(request: Request) -> str:
    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
    if token is None:
        token = request.cookies.get("access_token")
    if token is None:
        raise UnauthorizedError("Missing token")
    payload: dict[str, Any] = decode_access_token(token)
    jti: str | None = payload.get("jti")
    if jti is None:
        raise UnauthorizedError("Invalid token: missing jti")
    return jti


@router.get("/me", response_model=UserOut)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignUpRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    user: User = await sign_up(username=body.username, password=body.password, db=db)
    return {"message": "User created", "username": user.username}


@router.post("/signin", status_code=status.HTTP_200_OK, response_model=TokenResponse)
async def signin(
    body: SignInRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    token, _jti = await sign_in(username=body.username, password=body.password, db=db)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return TokenResponse(access_token=token)


@router.post("/signout", status_code=status.HTTP_200_OK)
async def signout(
    request: Request,
    response: Response,
    _current_user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis_pool)],
) -> dict[str, str]:
    jti: str = _extract_jti(request)
    await sign_out(jti=jti, redis=redis)
    response.delete_cookie(key="access_token")
    return {"message": "Signed out"}
