from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, revoke_refresh_token
from app.config import get_settings
from app.db import get_db
from app.mailer import send_email
from app.models import Session, User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    RecoveryRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorSetupResponse,
    UpdateProfileRequest,
    UserPublic,
    VerifyEmailRequest,
)
from app.security import (
    ACCESS_TTL_SECONDS,
    REFRESH_TTL_SECONDS,
    create_token,
    create_totp_secret,
    hash_password,
    hash_refresh_token,
    hash_token,
    random_token,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def normalize_username(username: str) -> str:
    return username.strip().lower()


async def issue_tokens(user: User, request: Request, db: AsyncSession) -> TokenResponse:
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
        user_id=user.id, session_id=session.id, token_type="refresh", ttl=REFRESH_TTL_SECONDS
    )
    session.token_hash = hash_refresh_token(refresh_token)
    access_token = create_token(
        user_id=user.id, session_id=session.id, token_type="access", ttl=ACCESS_TTL_SECONDS
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token, expires_in=ACCESS_TTL_SECONDS
    )


def send_verification_email(user: User, token: str) -> None:
    settings = get_settings()
    send_email(
        recipient=user.email or "",
        subject="BelGram — подтверждение email",
        body=f"Подтвердите email в BelGram: {settings.frontend_url}/verify-email?token={token}\n"
        "Ссылка действительна ограниченное время.",
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    username = normalize_username(payload.username)
    email = str(payload.email).lower()
    existing = await db.scalar(select(User).where(or_(User.username == username, User.email == email)))
    if existing:
        raise HTTPException(status_code=409, detail="Username or email already exists")
    token = random_token()
    user = User(
        username=username,
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        verification_token_hash=hash_token(token),
        verification_expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=get_settings().verification_ttl_minutes),
    )
    db.add(user)
    await db.flush()
    try:
        send_verification_email(user, token)
    except RuntimeError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Email service is unavailable") from None
    return await issue_tokens(user, request, db)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    login_value = payload.login.strip().lower()
    user = await db.scalar(select(User).where(or_(User.username == login_value, User.email == login_value)))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.two_factor_enabled:
        if not payload.two_factor_code or not user.two_factor_secret or not verify_totp(user.two_factor_secret, payload.two_factor_code):
            raise HTTPException(status_code=401, detail="Two-factor authentication required")
    return await issue_tokens(user, request, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        token_payload = jwt.decode(payload.refresh_token, get_settings().jwt_secret, algorithms=["HS256"])
        if token_payload.get("type") != "refresh":
            raise jwt.InvalidTokenError("wrong token type")
        session_id = int(token_payload["sid"])
        user_id = int(token_payload["sub"])
    except (ValueError, jwt.PyJWTError):
        raise HTTPException(status_code=401, detail="Invalid refresh token") from None
    session = await db.get(Session, session_id)
    if (
        session is None or session.user_id != user_id or session.revoked_at is not None
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
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await revoke_refresh_token(payload.refresh_token, db)
    return MessageResponse(message="Logged out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> MessageResponse:
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
    payload: UpdateProfileRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    if payload.username is not None:
        username = normalize_username(payload.username)
        if await db.scalar(select(User).where(User.username == username, User.id != user.id)):
            raise HTTPException(status_code=409, detail="Username already exists")
        user.username = username
    if payload.email is not None:
        email = str(payload.email).lower()
        if await db.scalar(select(User).where(User.email == email, User.id != user.id)):
            raise HTTPException(status_code=409, detail="Email already exists")
        user.email = email
        user.is_verified = False
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


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    user = await db.scalar(select(User).where(User.verification_token_hash == hash_token(payload.token)))
    if user is None or user.verification_expires_at is None or user.verification_expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user.is_verified = True
    user.verification_token_hash = None
    user.verification_expires_at = None
    await db.commit()
    return MessageResponse(message="Email verified")


@router.post("/recovery/request", response_model=MessageResponse)
async def request_recovery(payload: RecoveryRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is not None:
        token = random_token()
        user.recovery_token_hash = hash_token(token)
        user.recovery_expires_at = datetime.now(timezone.utc) + timedelta(minutes=get_settings().recovery_ttl_minutes)
        await db.commit()
        try:
            send_email(
                recipient=user.email or "",
                subject="BelGram — восстановление аккаунта",
                body=f"Сбросьте пароль: {get_settings().frontend_url}/reset-password?token={token}\n"
                "Ссылка действует ограниченное время.",
            )
        except RuntimeError:
            pass
    return MessageResponse(message="If the email exists, recovery instructions were sent")


@router.post("/recovery/reset", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    user = await db.scalar(select(User).where(User.recovery_token_hash == hash_token(payload.token)))
    if user is None or user.recovery_expires_at is None or user.recovery_expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired recovery token")
    user.password_hash = hash_password(payload.new_password)
    user.recovery_token_hash = None
    user.recovery_expires_at = None
    result = await db.execute(select(Session).where(Session.user_id == user.id))
    now = datetime.now(timezone.utc)
    for session in result.scalars():
        session.revoked_at = now
    await db.commit()
    return MessageResponse(message="Password reset; all sessions revoked")


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
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


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> TwoFactorSetupResponse:
    secret = create_totp_secret()
    user.two_factor_secret = secret
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name="BelGram")
    return TwoFactorSetupResponse(secret=secret, otpauth_url=uri)


@router.post("/2fa/enable", response_model=MessageResponse)
async def enable_2fa(
    payload: TwoFactorEnableRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    if not user.two_factor_secret or not verify_totp(user.two_factor_secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid two-factor code")
    user.two_factor_enabled = True
    await db.commit()
    return MessageResponse(message="Two-factor authentication enabled")


@router.post("/2fa/disable", response_model=MessageResponse)
async def disable_2fa(
    payload: TwoFactorDisableRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid password")
    if not user.two_factor_secret or not verify_totp(user.two_factor_secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid two-factor code")
    user.two_factor_enabled = False
    user.two_factor_secret = None
    await db.commit()
    return MessageResponse(message="Two-factor authentication disabled")


@router.delete("/me", response_model=MessageResponse)
async def delete_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await db.delete(user)
    await db.commit()
    return MessageResponse(message="Account deleted")
