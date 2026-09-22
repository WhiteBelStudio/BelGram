from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Session, User
from app.security import decode_token, hash_refresh_token


async def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")

    token = authorization[7:].strip()
    try:
        payload = decode_token(token, "access")
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
    except (ValueError, jwt.PyJWTError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token"
        ) from None

    session = await db.get(Session, session_id)
    if (
        session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or _as_utc(session.expires_at) <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def revoke_refresh_token(token: str, db: AsyncSession) -> None:
    try:
        payload = decode_token(token, "refresh")
        session_id = int(payload["sid"])
    except (ValueError, jwt.PyJWTError):
        return

    session = await db.get(Session, session_id)
    if session and session.token_hash == hash_refresh_token(token):
        session.revoked_at = datetime.now(timezone.utc)
        await db.commit()
