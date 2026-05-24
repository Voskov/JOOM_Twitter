from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.db.redis_client import get_redis_pool, close_redis_pool
from app.core.exceptions import register_exception_handlers
from app.routers import auth, messages, social, feeds


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # startup
    await get_redis_pool()
    yield
    # shutdown
    await close_redis_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Joon Twitter API",
        description="A simplified Twitter-like social updates API",
        version="1.0.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(auth.router)
    app.include_router(messages.router)
    app.include_router(social.router)
    app.include_router(feeds.router)
    return app


app = create_app()
