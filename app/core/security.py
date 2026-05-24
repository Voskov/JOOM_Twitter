from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> tuple[str, str]:
    """Returns (token, jti). jti is a unique JWT ID used for blacklisting."""
    jti = str(uuid.uuid4())
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "jti": jti, "exp": expire}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, jti


def decode_access_token(token: str) -> dict[str, str]:
    """Raises JWTError if invalid/expired. Returns payload dict."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
