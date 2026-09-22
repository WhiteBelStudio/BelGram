from collections.abc import AsyncGenerator

import pytest
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


@pytest.fixture(autouse=True)
async def reset_database() -> AsyncGenerator[None, None]:
    from app import models  # noqa: F401

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_register_login_and_me() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/register",
            json={
                "username": "Test_User",
                "display_name": "Test User",
                "password": "correct-horse-battery",
            },
        )
        assert response.status_code == 201
        tokens = response.json()
        assert tokens["access_token"]
        assert tokens["refresh_token"]

        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == "test_user"

        login = await client.post(
            "/auth/login",
            json={"login": "test_user", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200


@pytest.mark.asyncio
async def test_duplicate_username_and_bad_password() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "username": "same_user",
            "display_name": "Same User",
            "password": "correct-horse-battery",
        }
        assert (await client.post("/auth/register", json=payload)).status_code == 201
        assert (await client.post("/auth/register", json=payload)).status_code == 409
        assert (
            await client.post(
                "/auth/login",
                json={"login": "same_user", "password": "wrong-password"},
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_and_logout() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/auth/register",
            json={
                "username": "rotate_user",
                "display_name": "Rotate User",
                "password": "correct-horse-battery",
            },
        )
        old_refresh = register.json()["refresh_token"]

        refreshed = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert refreshed.status_code == 200
        new_refresh = refreshed.json()["refresh_token"]
        assert new_refresh != old_refresh

        old_again = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert old_again.status_code == 401

        logged_out = await client.post("/auth/logout", json={"refresh_token": new_refresh})
        assert logged_out.status_code == 200

        after_logout = await client.post(
            "/auth/refresh", json={"refresh_token": new_refresh}
        )
        assert after_logout.status_code == 401
