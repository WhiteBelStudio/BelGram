import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from pwdlib import PasswordHash

from app.config import get_settings

password_hash = PasswordHash.recommended()
settings = get_settings()
ACCESS_TTL_SECONDS = 15 * 60
REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(*, user_id: int, session_id: int, token_type: str, ttl: int) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {"sub": str(user_id), "sid": str(session_id), "type": token_type, "iat": now, "exp": now + timedelta(seconds=ttl), "jti": secrets.token_hex(16)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type or not payload.get("sub") or not payload.get("sid"):
        raise jwt.InvalidTokenError("invalid token")
    return payload


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
