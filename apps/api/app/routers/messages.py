import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_db
from app.models import (
    DirectConversation,
    DirectMessage,
    DirectMessageDraft,
    DirectMessageReaction,
    User,
    UserBlock,
)
from app.realtime import manager
from app.schemas import (
    ClearHistoryResponse,
    DialogPublic,
    DirectMessageCreate,
    DirectMessagePublic,
    DirectMessageUpdate,
    DraftRequest,
    MessageResponse,
    MessageSearchResult,
    ProfilePublic,
    ReactionRequest,
    UnreadCount,
)

router = APIRouter(prefix="/messages", tags=["messages"])

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
MENTION_RE = re.compile(r"@([a-zA-Z0-9_]{3,32})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _blocked_either_way(db: AsyncSession, first_id: int, second_id: int) -> bool:
    return bool(
        await db.scalar(
            select(UserBlock.id).where(
                or_(
                    (UserBlock.blocker_id == first_id) & (UserBlock.blocked_id == second_id),
                    (UserBlock.blocker_id == second_id) & (UserBlock.blocked_id == first_id),
                )
            )
        )
    )


async def _conversation_for_user(
    db: AsyncSession, conversation_id: int, user_id: int
) -> DirectConversation:
    conversation = await db.get(DirectConversation, conversation_id)
    if conversation is None or user_id not in (conversation.user_low_id, conversation.user_high_id):
        raise HTTPException(status_code=404, detail="Dialog not found")
    return conversation


async def _peer(db: AsyncSession, conversation: DirectConversation, user_id: int) -> User:
    peer_id = (
        conversation.user_high_id
        if conversation.user_low_id == user_id
        else conversation.user_low_id
    )
    peer = await db.get(User, peer_id)
    if peer is None:
        raise HTTPException(status_code=404, detail="User not found")
    return peer


def _profile(user: User) -> ProfilePublic:
    return ProfilePublic(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        bio=user.bio,
        status=user.status if user.show_status else None,
        is_verified=user.is_verified,
        is_online=user.is_online,
        last_seen_at=user.last_seen_at if user.show_last_seen else None,
        mutual_groups=0,
        mutual_communities=0,
        is_blocked=False,
        is_private=user.profile_visibility == "private",
    )


async def _reactions(db: AsyncSession, message_id: int) -> dict[str, int]:
    result = await db.execute(
        select(DirectMessageReaction.emoji, func.count(DirectMessageReaction.id))
        .where(DirectMessageReaction.message_id == message_id)
        .group_by(DirectMessageReaction.emoji)
    )
    return {emoji: int(count) for emoji, count in result.all()}


async def _public_message(db: AsyncSession, message: DirectMessage) -> DirectMessagePublic:
    return DirectMessagePublic(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        body="" if message.deleted_at else message.body,
        reply_to_id=message.reply_to_id,
        forwarded_from_id=message.forwarded_from_id,
        mentions=json.loads(message.mentions_json or "[]"),
        link_url=message.link_url,
        link_preview_title=message.link_preview_title,
        created_at=message.created_at,
        edited_at=message.edited_at,
        deleted_at=message.deleted_at,
        delivered_at=message.delivered_at,
        read_at=message.read_at,
        pinned_at=message.pinned_at,
        reactions=await _reactions(db, message.id),
    )


async def _extract_link(body: str) -> tuple[str | None, str | None]:
    match = URL_RE.search(body)
    if not match:
        return None, None
    url = match.group(0).rstrip(".,!?")
    title = url.split("//", 1)[-1].split("/", 1)[0][:300]
    return url, title


async def _mentions(db: AsyncSession, body: str) -> list[str]:
    names = list(dict.fromkeys(match.group(1).lower() for match in MENTION_RE.finditer(body)))
    if not names:
        return []
    result = await db.execute(select(User.username).where(User.username.in_(names)))
    existing = {name.lower() for name in result.scalars()}
    return [name for name in names if name in existing]


@router.post("/dialogs/{user_id}", response_model=DialogPublic, status_code=status.HTTP_201_CREATED)
async def create_dialog(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DialogPublic:
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot message yourself")
    peer = await db.get(User, user_id)
    if peer is None:
        raise HTTPException(status_code=404, detail="User not found")
    if await _blocked_either_way(db, user.id, peer.id):
        raise HTTPException(status_code=403, detail="Messaging is blocked")

    low, high = sorted((user.id, peer.id))
    conversation = await db.scalar(
        select(DirectConversation).where(
            DirectConversation.user_low_id == low,
            DirectConversation.user_high_id == high,
        )
    )
    if conversation is None:
        conversation = DirectConversation(user_low_id=low, user_high_id=high)
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

    last = await db.scalar(
        select(DirectMessage)
        .where(
            DirectMessage.conversation_id == conversation.id,
            DirectMessage.deleted_at.is_(None),
        )
        .order_by(DirectMessage.created_at.desc(), DirectMessage.id.desc())
        .limit(1)
    )
    unread = int(
        await db.scalar(
            select(func.count(DirectMessage.id)).where(
                DirectMessage.conversation_id == conversation.id,
                DirectMessage.sender_id == peer.id,
                DirectMessage.read_at.is_(None),
                DirectMessage.deleted_at.is_(None),
            )
        )
        or 0
    )
    return DialogPublic(
        id=conversation.id,
        peer=_profile(peer),
        last_message=await _public_message(db, last) if last else None,
        unread_count=unread,
        created_at=conversation.created_at,
    )


@router.get("/dialogs", response_model=list[DialogPublic])
async def list_dialogs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DialogPublic]:
    result = await db.execute(
        select(DirectConversation)
        .where(
            or_(
                DirectConversation.user_low_id == user.id,
                DirectConversation.user_high_id == user.id,
            )
        )
        .order_by(DirectConversation.created_at.desc())
    )
    dialogs: list[DialogPublic] = []
    for conversation in result.scalars():
        peer = await _peer(db, conversation, user.id)
        last = await db.scalar(
            select(DirectMessage)
            .where(
                DirectMessage.conversation_id == conversation.id,
                DirectMessage.deleted_at.is_(None),
            )
            .order_by(DirectMessage.created_at.desc(), DirectMessage.id.desc())
            .limit(1)
        )
        unread = int(
            await db.scalar(
                select(func.count(DirectMessage.id)).where(
                    DirectMessage.conversation_id == conversation.id,
                    DirectMessage.sender_id == peer.id,
                    DirectMessage.read_at.is_(None),
                    DirectMessage.deleted_at.is_(None),
                )
            )
            or 0
        )
        dialogs.append(
            DialogPublic(
                id=conversation.id,
                peer=_profile(peer),
                last_message=await _public_message(db, last) if last else None,
                unread_count=unread,
                created_at=conversation.created_at,
            )
        )
    return dialogs


@router.get("/dialogs/{conversation_id}/messages", response_model=list[DirectMessagePublic])
async def list_messages(
    conversation_id: int,
    before_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DirectMessagePublic]:
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    if await _blocked_either_way(db, user.id, peer.id):
        raise HTTPException(status_code=403, detail="Messaging is blocked")

    stmt = (
        select(DirectMessage)
        .where(
            DirectMessage.conversation_id == conversation.id,
            DirectMessage.deleted_at.is_(None),
        )
        .order_by(DirectMessage.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        stmt = stmt.where(DirectMessage.id < before_id)
    result = await db.execute(stmt)
    messages = list(result.scalars())
    now = _now()
    for message in messages:
        if message.sender_id == peer.id and message.delivered_at is None:
            message.delivered_at = now
    await db.commit()
    return [await _public_message(db, message) for message in reversed(messages)]


@router.post("/dialogs/{conversation_id}/messages", response_model=DirectMessagePublic, status_code=201)
async def send_message(
    conversation_id: int,
    payload: DirectMessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DirectMessagePublic:
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    if await _blocked_either_way(db, user.id, peer.id):
        raise HTTPException(status_code=403, detail="Messaging is blocked")
    if not payload.body.strip() and payload.forwarded_from_id is None:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    if payload.reply_to_id is not None:
        reply = await db.get(DirectMessage, payload.reply_to_id)
        if reply is None or reply.conversation_id != conversation.id:
            raise HTTPException(status_code=400, detail="Invalid reply target")

    if payload.forwarded_from_id is not None:
        source = await db.get(DirectMessage, payload.forwarded_from_id)
        if source is None:
            raise HTTPException(status_code=400, detail="Invalid forwarded message")
        source_conversation = await db.get(DirectConversation, source.conversation_id)
        if source_conversation is None or user.id not in (
            source_conversation.user_low_id,
            source_conversation.user_high_id,
        ):
            raise HTTPException(status_code=403, detail="You cannot forward this message")

    link_url, preview_title = await _extract_link(payload.body)
    message = DirectMessage(
        conversation_id=conversation.id,
        sender_id=user.id,
        body=payload.body.strip(),
        reply_to_id=payload.reply_to_id,
        forwarded_from_id=payload.forwarded_from_id,
        mentions_json=json.dumps(await _mentions(db, payload.body)),
        link_url=link_url,
        link_preview_title=preview_title,
        delivered_at=None,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    public_message = await _public_message(db, message)
    await manager.send_user(
        peer.id,
        {"type": "message.new", "conversation_id": conversation.id, "message": public_message.model_dump(mode="json")},
    )
    return public_message


@router.patch("/dialogs/{conversation_id}/messages/{message_id}", response_model=DirectMessagePublic)
async def edit_message(
    conversation_id: int,
    message_id: int,
    payload: DirectMessageUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DirectMessagePublic:
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    message = await db.get(DirectMessage, message_id)
    if (
        message is None
        or message.conversation_id != conversation_id
        or message.sender_id != user.id
        or message.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Message not found")
    message.body = payload.body.strip()
    message.mentions_json = json.dumps(await _mentions(db, message.body))
    message.link_url, message.link_preview_title = await _extract_link(message.body)
    message.edited_at = _now()
    await db.commit()
    await db.refresh(message)
    public_message = await _public_message(db, message)
    await manager.broadcast_users(
        [user.id, peer.id],
        {"type": "message.updated", "conversation_id": conversation_id, "message": public_message.model_dump(mode="json")},
    )
    return public_message


@router.delete("/dialogs/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def delete_message(
    conversation_id: int,
    message_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await _conversation_for_user(db, conversation_id, user.id)
    message = await db.get(DirectMessage, message_id)
    if (
        message is None
        or message.conversation_id != conversation_id
        or message.sender_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Message not found")
    message.deleted_at = _now()
    message.body = ""
    await db.commit()
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    await manager.broadcast_users(
        [user.id, peer.id],
        {"type": "message.deleted", "conversation_id": conversation_id, "message_id": message_id},
    )
    return MessageResponse(message="Message deleted")


@router.post("/dialogs/{conversation_id}/messages/{message_id}/delivered", response_model=MessageResponse)
async def mark_delivered(
    conversation_id: int,
    message_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await _conversation_for_user(db, conversation_id, user.id)
    message = await db.get(DirectMessage, message_id)
    if message is None or message.conversation_id != conversation_id or message.sender_id == user.id:
        raise HTTPException(status_code=404, detail="Message not found")
    message.delivered_at = _now()
    await db.commit()
    await _conversation_for_user(db, conversation_id, user.id)
    await manager.send_user(
        message.sender_id,
        {"type": "message.delivered", "conversation_id": conversation_id, "message_id": message_id, "delivered_at": message.delivered_at.isoformat()},
    )
    return MessageResponse(message="Delivered")


@router.post("/dialogs/{conversation_id}/read", response_model=UnreadCount)
async def mark_read(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UnreadCount:
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    now = _now()
    result = await db.execute(
        select(DirectMessage).where(
            DirectMessage.conversation_id == conversation.id,
            DirectMessage.sender_id != user.id,
            DirectMessage.read_at.is_(None),
            DirectMessage.deleted_at.is_(None),
        )
    )
    count = 0
    for message in result.scalars():
        message.read_at = now
        count += 1
    await db.commit()
    peer = await _peer(db, conversation, user.id)
    await manager.send_user(
        peer.id,
        {"type": "message.read", "conversation_id": conversation_id, "read_at": now.isoformat()},
    )
    return UnreadCount(unread_count=count)


@router.post("/dialogs/{conversation_id}/messages/{message_id}/reaction", response_model=DirectMessagePublic)
async def add_reaction(
    conversation_id: int,
    message_id: int,
    payload: ReactionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DirectMessagePublic:
    await _conversation_for_user(db, conversation_id, user.id)
    message = await db.get(DirectMessage, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    existing = await db.scalar(
        select(DirectMessageReaction).where(
            DirectMessageReaction.message_id == message_id,
            DirectMessageReaction.user_id == user.id,
            DirectMessageReaction.emoji == payload.emoji,
        )
    )
    if existing is None:
        db.add(DirectMessageReaction(message_id=message_id, user_id=user.id, emoji=payload.emoji))
        await db.commit()
    public_message = await _public_message(db, message)
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    await manager.broadcast_users(
        [user.id, peer.id],
        {"type": "message.reaction", "conversation_id": conversation_id, "message": public_message.model_dump(mode="json")},
    )
    return public_message


@router.delete("/dialogs/{conversation_id}/messages/{message_id}/reaction", response_model=DirectMessagePublic)
async def remove_reaction(
    conversation_id: int,
    message_id: int,
    emoji: str = Query(min_length=1, max_length=32),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DirectMessagePublic:
    await _conversation_for_user(db, conversation_id, user.id)
    await db.execute(
        delete(DirectMessageReaction).where(
            DirectMessageReaction.message_id == message_id,
            DirectMessageReaction.user_id == user.id,
            DirectMessageReaction.emoji == emoji,
        )
    )
    await db.commit()
    message = await db.get(DirectMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return await _public_message(db, message)


@router.post("/dialogs/{conversation_id}/messages/{message_id}/pin", response_model=DirectMessagePublic)
async def toggle_pin(
    conversation_id: int,
    message_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DirectMessagePublic:
    await _conversation_for_user(db, conversation_id, user.id)
    message = await db.get(DirectMessage, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    message.pinned_at = None if message.pinned_at else _now()
    await db.commit()
    public_message = await _public_message(db, message)
    conversation = await _conversation_for_user(db, conversation_id, user.id)
    peer = await _peer(db, conversation, user.id)
    await manager.broadcast_users(
        [user.id, peer.id],
        {"type": "message.pinned", "conversation_id": conversation_id, "message": public_message.model_dump(mode="json")},
    )
    return public_message


@router.put("/dialogs/{conversation_id}/draft", response_model=MessageResponse)
async def save_draft(
    conversation_id: int,
    payload: DraftRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await _conversation_for_user(db, conversation_id, user.id)
    draft = await db.scalar(
        select(DirectMessageDraft).where(
            DirectMessageDraft.conversation_id == conversation_id,
            DirectMessageDraft.user_id == user.id,
        )
    )
    if draft is None:
        draft = DirectMessageDraft(
            conversation_id=conversation_id, user_id=user.id, body=payload.body
        )
        db.add(draft)
    else:
        draft.body = payload.body
        draft.updated_at = _now()
    await db.commit()
    return MessageResponse(message="Draft saved")


@router.get("/dialogs/{conversation_id}/draft", response_model=DraftRequest)
async def get_draft(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DraftRequest:
    await _conversation_for_user(db, conversation_id, user.id)
    draft = await db.scalar(
        select(DirectMessageDraft).where(
            DirectMessageDraft.conversation_id == conversation_id,
            DirectMessageDraft.user_id == user.id,
        )
    )
    return DraftRequest(body=draft.body if draft else "")


@router.delete("/dialogs/{conversation_id}/draft", response_model=MessageResponse)
async def delete_draft(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await _conversation_for_user(db, conversation_id, user.id)
    await db.execute(
        delete(DirectMessageDraft).where(
            DirectMessageDraft.conversation_id == conversation_id,
            DirectMessageDraft.user_id == user.id,
        )
    )
    await db.commit()
    return MessageResponse(message="Draft deleted")


@router.get("/search", response_model=list[MessageSearchResult])
async def search_messages(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageSearchResult]:
    result = await db.execute(
        select(DirectMessage)
        .join(
            DirectConversation,
            DirectConversation.id == DirectMessage.conversation_id,
        )
        .where(
            or_(
                DirectConversation.user_low_id == user.id,
                DirectConversation.user_high_id == user.id,
            ),
            DirectMessage.deleted_at.is_(None),
            DirectMessage.body.ilike(f"%{q}%"),
        )
        .order_by(DirectMessage.created_at.desc(), DirectMessage.id.desc())
        .limit(limit)
    )
    items: list[MessageSearchResult] = []
    for message in result.scalars():
        conversation = await db.get(DirectConversation, message.conversation_id)
        if conversation is None:
            continue
        peer = await _peer(db, conversation, user.id)
        items.append(MessageSearchResult(message=await _public_message(db, message), peer=_profile(peer)))
    return items


@router.delete("/dialogs/{conversation_id}/history", response_model=ClearHistoryResponse)
async def clear_history(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClearHistoryResponse:
    await _conversation_for_user(db, conversation_id, user.id)
    result = await db.execute(
        delete(DirectMessage).where(DirectMessage.conversation_id == conversation_id)
    )
    await db.commit()
    return ClearHistoryResponse(deleted_messages=result.rowcount or 0)
