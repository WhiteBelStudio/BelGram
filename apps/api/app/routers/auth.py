from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, revoke_refresh_token
from app.config import get_settings
from app.db import get_db
from app.models import Session, User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserPublic,
)
from app.security import (
    ACCESS_TTL_SECONDS,
    REFRESH_TTL_SECONDS,
    create_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def normalize_username(username: str) -> str:
    return username.strip().lower()


async def issue_tokens(
    user: User,
    request: Request,
    db: AsyncSession,
) -> TokenResponse:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TTL_SECONDS)
    session = Session(
        user_id=user.id,
        token_hash="pending",
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()

    refresh_token = create_token(
        user_id=user.id,
        session_id=session.id,
        token_type="refresh",
        ttl=REFRESH_TTL_SECONDS,
    )
    session.token_hash = hash_refresh_token(refresh_token)
    access_token = create_token(
        user_id=user.id,
        session_id=session.id,
        token_type="access",
        ttl=ACCESS_TTL_SECONDS,
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TTL_SECONDS,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    username = normalize_username(payload.username)
    existing = await db.scalar(select(User).where(User.username == username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username=username,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    return await issue_tokens(user, request, db)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    login_value = payload.login.strip().lower()
    user = await db.scalar(
        select(User).where(or_(User.username == login_value))
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return await issue_tokens(user, request, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        token_payload = jwt.decode(
            payload.refresh_token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
        )
        if token_payload.get("type") != "refresh":
            raise jwt.InvalidTokenError("wrong token type")
        session_id = int(token_payload["sid"])
        user_id = int(token_payload["sub"])
    except (ValueError, jwt.PyJWTError):
        raise HTTPException(status_code=401, detail="Invalid refresh token") from None

    session = await db.get(Session, session_id)
    if (
        session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(timezone.utc)
        or session.token_hash != hash_refresh_token(payload.refresh_token)
    ):
        raise HTTPException(status_code=401, detail="Invalid refresh session")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    session.revoked_at = datetime.now(timezone.utc)
    await db.flush()
    return await issue_tokens(user, request, db)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await revoke_refresh_token(payload.refresh_token, db)
    return MessageResponse(message="Logged out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(select(Session).where(Session.user_id == user.id))
    now = datetime.now(timezone.utc)
    for session in result.scalars():
        session.revoked_at = now
    await db.commit()
    return MessageResponse(message="All sessions revoked")


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserPublic)
async def update_me(
    payload: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if payload.username is not None:
        username = normalize_username(payload.username)
        existing = await db.scalar(
            select(User).where(User.username == username, User.id != user.id)
        )
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        user.username = username
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.bio is not None:
        user.bio = payload.bio.strip()
    if payload.status is not None:
        user.status = payload.status.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url.strip() or None
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    result = await db.execute(select(Session).where(Session.user_id == user.id))
    now = datetime.now(timezone.utc)
    for session in result.scalars():
        session.revoked_at = now
    await db.commit()
    return MessageResponse(message="Password changed; all sessions revoked")


@router.delete("/me", response_model=MessageResponse)
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await db.delete(user)
    await db.commit()
    return MessageResponse(message="Account deleted")
