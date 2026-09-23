from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.auth import get_current_user
from app.db import get_db
from app.models import CommunityMember, GroupMember, User, UserBlock
from app.schemas import BlockedUserPublic, MessageResponse, ProfilePrivacy, ProfilePublic, UpdatePrivacyRequest

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _blocked_query(viewer_id: int, target_id: int) -> ColumnElement[bool]:
    return exists(select(UserBlock.id).where(UserBlock.blocker_id == viewer_id, UserBlock.blocked_id == target_id))


async def _is_blocked(db: AsyncSession, viewer_id: int, target_id: int) -> bool:
    return bool(await db.scalar(select(_blocked_query(viewer_id, target_id))))


async def _mutual_group_count(db: AsyncSession, viewer_id: int, target_id: int) -> int:
    mine = aliased(GroupMember)
    theirs = aliased(GroupMember)
    stmt = select(func.count(func.distinct(mine.group_id))).join(theirs, theirs.group_id == mine.group_id).where(
        mine.user_id == viewer_id, theirs.user_id == target_id
    )
    return int(await db.scalar(stmt) or 0)


async def _mutual_community_count(db: AsyncSession, viewer_id: int, target_id: int) -> int:
    mine = aliased(CommunityMember)
    theirs = aliased(CommunityMember)
    stmt = select(func.count(func.distinct(mine.community_id))).join(theirs, theirs.community_id == mine.community_id).where(
        mine.user_id == viewer_id, theirs.user_id == target_id
    )
    return int(await db.scalar(stmt) or 0)


async def _profile(db: AsyncSession, viewer: User, target: User) -> ProfilePublic:
    blocked = await _is_blocked(db, viewer.id, target.id)
    private = target.profile_visibility == "private" and viewer.id != target.id
    return ProfilePublic(
        id=target.id,
        username=target.username,
        display_name=target.display_name,
        avatar_url=target.avatar_url,
        bio="" if private and not blocked else target.bio,
        status=None if private or not target.show_status else target.status,
        is_verified=target.is_verified,
        is_online=target.is_online,
        last_seen_at=target.last_seen_at if target.show_last_seen else None,
        mutual_groups=await _mutual_group_count(db, viewer.id, target.id),
        mutual_communities=await _mutual_community_count(db, viewer.id, target.id),
        is_blocked=blocked,
        is_private=private and not blocked,
    )


@router.get("/me", response_model=ProfilePublic)
async def my_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ProfilePublic:
    return await _profile(db, user, user)


@router.get("/me/privacy", response_model=ProfilePrivacy)
async def get_privacy(user: User = Depends(get_current_user)) -> ProfilePrivacy:
    return ProfilePrivacy(profile_visibility=user.profile_visibility, show_email=user.show_email,
                          show_last_seen=user.show_last_seen, show_status=user.show_status)


@router.patch("/me/privacy", response_model=ProfilePrivacy)
async def update_privacy(payload: UpdatePrivacyRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ProfilePrivacy:
    user.profile_visibility = payload.profile_visibility
    user.show_email = payload.show_email
    user.show_last_seen = payload.show_last_seen
    user.show_status = payload.show_status
    await db.commit()
    await db.refresh(user)
    return await get_privacy(user)


@router.get("/me/blocked", response_model=list[BlockedUserPublic])
async def blocked_users(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[BlockedUserPublic]:
    result = await db.execute(select(User).join(UserBlock, UserBlock.blocked_id == User.id).where(
        UserBlock.blocker_id == user.id).order_by(UserBlock.created_at.desc()))
    return [BlockedUserPublic(id=item.id, username=item.username, display_name=item.display_name,
                              avatar_url=item.avatar_url, created_at=item.created_at) for item in result.scalars()]


@router.get("/{user_id}", response_model=ProfilePublic)
async def get_profile(user_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> ProfilePublic:
    target = await db.get(User, user_id)
    if target is None or await _is_blocked(db, target.id, user.id):
        raise HTTPException(status_code=404, detail="User not found")
    return await _profile(db, user, target)


@router.post("/{user_id}/block", response_model=MessageResponse)
async def block_user(user_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot block yourself")
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not await _is_blocked(db, user.id, user_id):
        db.add(UserBlock(blocker_id=user.id, blocked_id=user_id))
        await db.commit()
    return MessageResponse(message="User blocked")


@router.delete("/{user_id}/block", response_model=MessageResponse)
async def unblock_user(user_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    result = await db.execute(select(UserBlock).where(UserBlock.blocker_id == user.id, UserBlock.blocked_id == user_id))
    for block in result.scalars():
        await db.delete(block)
    await db.commit()
    return MessageResponse(message="User unblocked")
