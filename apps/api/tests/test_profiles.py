import pytest
import pytest_asyncio
from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, get_db
from app.main import app



TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_belgram.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncGenerator[None, None]:
    from app import models  # noqa: F401

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def register(client: AsyncClient, username: str, email: str) -> tuple[str, int]:
    response = await client.post("/auth/register", json={
        "username": username,
        "email": email,
        "display_name": username.title(),
        "password": "correct-horse-battery",
    })
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


@pytest.mark.asyncio
async def test_profile_privacy_and_blocking() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_token, first_id = await register(client, "profile_one", "profile1@example.com")
        second_token, second_id = await register(client, "profile_two", "profile2@example.com")
        first_headers = {"Authorization": f"Bearer {first_token}"}
        second_headers = {"Authorization": f"Bearer {second_token}"}

        update = await client.patch("/auth/me", headers=first_headers, json={
            "display_name": "Profile One", "bio": "Hello BelGram", "status": "Available",
        })
        assert update.status_code == 200

        profile = await client.get(f"/profiles/{first_id}", headers=second_headers)
        assert profile.status_code == 200
        assert profile.json()["bio"] == "Hello BelGram"
        assert profile.json()["username"] == "profile_one"

        privacy = await client.patch("/profiles/me/privacy", headers=first_headers, json={
            "profile_visibility": "private", "show_email": False,
            "show_last_seen": False, "show_status": False,
        })
        assert privacy.status_code == 200
        assert privacy.json()["profile_visibility"] == "private"

        private_view = await client.get(f"/profiles/{first_id}", headers=second_headers)
        assert private_view.status_code == 200
        assert private_view.json()["is_private"] is True
        assert private_view.json()["bio"] == ""
        assert private_view.json()["status"] is None
        assert private_view.json()["last_seen_at"] is None

        block = await client.post(f"/profiles/{second_id}/block", headers=first_headers)
        assert block.status_code == 200
        blocked = await client.get("/profiles/me/blocked", headers=first_headers)
        assert blocked.status_code == 200
        assert blocked.json()[0]["id"] == second_id

        hidden = await client.get(f"/profiles/{first_id}", headers=second_headers)
        assert hidden.status_code == 404

        unblock = await client.delete(f"/profiles/{second_id}/block", headers=first_headers)
        assert unblock.status_code == 200
