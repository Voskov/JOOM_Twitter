from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.core.security import decode_access_token


def _get_user_or_ip(request: Request) -> str:
    """Per-user key for PostMessage. Falls back to IP for unauthenticated requests."""
    token: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
    if not token:
        token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_access_token(token)
            sub: str | None = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    default_limits=[],
)
