from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(140), default="")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_visibility: Mapped[str] = mapped_column(String(16), default="public")
    show_email: Mapped[bool] = mapped_column(Boolean, default=False)
    show_last_seen: Mapped[bool] = mapped_column(Boolean, default=True)
    show_status: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_sessions_token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class UserBlock(Base):
    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_user_blocks_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class CommunityMember(Base):
    __tablename__ = "community_members"
    __table_args__ = (UniqueConstraint("community_id", "user_id", name="uq_community_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    community_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class DirectConversation(Base):
    __tablename__ = "direct_conversations"
    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_direct_conversation_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_low_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user_high_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DirectMessage(Base):
    __tablename__ = "direct_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("direct_conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    reply_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("direct_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    forwarded_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("direct_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mentions_json: Mapped[str] = mapped_column(Text, default="[]")
    link_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    link_preview_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DirectMessageReaction(Base):
    __tablename__ = "direct_message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", "emoji", name="uq_direct_message_reaction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("direct_messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    emoji: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DirectMessageDraft(Base):
    __tablename__ = "direct_message_drafts"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_direct_message_draft"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("direct_conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
