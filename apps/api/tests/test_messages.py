from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_belgram_messages.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncGenerator[None, None]:
    from app import models  # noqa: F401

    app.dependency_overrides[get_db] = override_get_db
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.pop(get_db, None)


async def register(client: AsyncClient, username: str, email: str) -> str:
    response = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "display_name": username.title(),
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_direct_messages_lifecycle() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await register(client, "message_one", "message1@example.com")
        second = await register(client, "message_two", "message2@example.com")
        first_headers = {"Authorization": f"Bearer {first}"}
        second_headers = {"Authorization": f"Bearer {second}"}

        second_me = await client.get("/auth/me", headers=second_headers)
        second_id = second_me.json()["id"]

        dialog = await client.post(f"/messages/dialogs/{second_id}", headers=first_headers)
        assert dialog.status_code == 201
        conversation_id = dialog.json()["id"]

        sent = await client.post(
            f"/messages/dialogs/{conversation_id}/messages",
            headers=first_headers,
            json={"body": "Hello @message_two https://example.com"},
        )
        assert sent.status_code == 201
        message = sent.json()
        message_id = message["id"]
        assert message["link_url"] == "https://example.com"
        assert message["mentions"] == ["message_two"]

        listed = await client.get(
            f"/messages/dialogs/{conversation_id}/messages", headers=second_headers
        )
        assert listed.status_code == 200
        assert listed.json()[0]["delivered_at"] is not None

        read = await client.post(
            f"/messages/dialogs/{conversation_id}/read", headers=second_headers
        )
        assert read.status_code == 200
        assert read.json()["unread_count"] == 1

        reaction = await client.post(
            f"/messages/dialogs/{conversation_id}/messages/{message_id}/reaction",
            headers=second_headers,
            json={"emoji": "👍"},
        )
        assert reaction.status_code == 200
        assert reaction.json()["reactions"]["👍"] == 1

        edited = await client.patch(
            f"/messages/dialogs/{conversation_id}/messages/{message_id}",
            headers=first_headers,
            json={"body": "Edited"},
        )
        assert edited.status_code == 200
        assert edited.json()["edited_at"] is not None

        pin = await client.post(
            f"/messages/dialogs/{conversation_id}/messages/{message_id}/pin",
            headers=first_headers,
        )
        assert pin.status_code == 200
        assert pin.json()["pinned_at"] is not None

        draft = await client.put(
            f"/messages/dialogs/{conversation_id}/draft",
            headers=second_headers,
            json={"body": "Draft text"},
        )
        assert draft.status_code == 200
        assert (
            await client.get(
                f"/messages/dialogs/{conversation_id}/draft", headers=second_headers
            )
        ).json()["body"] == "Draft text"

        search = await client.get(
            "/messages/search", headers=first_headers, params={"q": "Edited"}
        )
        assert search.status_code == 200
        assert search.json()[0]["message"]["id"] == message_id

        deleted = await client.delete(
            f"/messages/dialogs/{conversation_id}/messages/{message_id}",
            headers=first_headers,
        )
        assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_reply_forward_and_clear_history() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await register(client, "reply_one", "reply1@example.com")
        second = await register(client, "reply_two", "reply2@example.com")
        second_id = (
            await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {second}"}
            )
        ).json()["id"]
        first_headers = {"Authorization": f"Bearer {first}"}

        dialog = await client.post(f"/messages/dialogs/{second_id}", headers=first_headers)
        conversation_id = dialog.json()["id"]
        original = await client.post(
            f"/messages/dialogs/{conversation_id}/messages",
            headers=first_headers,
            json={"body": "Original"},
        )
        original_id = original.json()["id"]
        reply = await client.post(
            f"/messages/dialogs/{conversation_id}/messages",
            headers=first_headers,
            json={"body": "Reply", "reply_to_id": original_id},
        )
        assert reply.json()["reply_to_id"] == original_id

        forwarded = await client.post(
            f"/messages/dialogs/{conversation_id}/messages",
            headers=first_headers,
            json={"forwarded_from_id": original_id},
        )
        assert forwarded.json()["forwarded_from_id"] == original_id

        clear = await client.delete(
            f"/messages/dialogs/{conversation_id}/history", headers=first_headers
        )
        assert clear.status_code == 200
        assert clear.json()["deleted_messages"] == 3
