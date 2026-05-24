from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.exceptions import register_exception_handlers
from app.core.rate_limit import limiter
from app.db.redis_client import close_redis_pool, get_redis_pool
from app.routers import auth, feeds, messages, social


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
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
    app.state.limiter = limiter
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(auth.router)
    app.include_router(messages.router)
    app.include_router(social.router)
    app.include_router(feeds.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
