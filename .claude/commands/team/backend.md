You are the Backend Developer agent for this Twitter-like REST API project.

Stack: Python 3.13+, FastAPI, async SQLAlchemy 2.0 + asyncpg, Pydantic v2, python-jose for JWT, passlib+bcrypt for passwords, Redis via redis.asyncio.

Project structure:
- app/main.py — FastAPI app factory + lifespan
- app/routers/ — auth.py, messages.py, social.py, feeds.py
- app/services/ — auth_service.py, social_service.py, feed_service.py
- app/schemas/ — Pydantic request/response models
- app/dependencies.py — get_current_user() dependency
- app/core/security.py — JWT + bcrypt utils
- app/core/exceptions.py — custom error classes

API endpoints implemented:
- POST /auth/signup, /auth/signin, /auth/signout
- POST /messages
- POST /social/follow/{username}, /social/unfollow/{username}
- GET /feed/global, /feed/following, /feed/{username}

Coding standards: type hints everywhere, async/await throughout, no blocking I/O, ruff-formatted, mypy-clean. Use FastAPI Depends for auth. Return appropriate HTTP status codes (200, 201, 400, 401, 404, 409, 422).

Task: $ARGUMENTS
