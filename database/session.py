from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import config
from database.models import Base

engine = create_async_engine(config.DB_URL, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

# колонки, добавленные после первого релиза: create_all их не досоздаёт
_NEW_COLUMNS = {
    "users": {
        "quality": "INTEGER NOT NULL DEFAULT 192",
        "protect_content": "BOOLEAN NOT NULL DEFAULT 0",
    },
}


def _add_missing_columns(conn) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table, columns in _NEW_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(Base.metadata.create_all)


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session