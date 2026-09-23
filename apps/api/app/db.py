from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


def _add_missing_profile_columns(connection) -> None:
    inspector = inspect(connection)
    existing = {column["name"] for column in inspector.get_columns("users")}
    dialect = connection.dialect.name
    column_sql = {
        "profile_visibility": "VARCHAR(16) NOT NULL DEFAULT 'public'",
        "show_email": "BOOLEAN NOT NULL DEFAULT FALSE",
        "show_last_seen": "BOOLEAN NOT NULL DEFAULT TRUE",
        "show_status": "BOOLEAN NOT NULL DEFAULT TRUE",
    }
    for name, definition in column_sql.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
    if dialect == "sqlite":
        connection.execute(text("UPDATE users SET profile_visibility = 'public' WHERE profile_visibility IS NULL"))


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_profile_columns)
