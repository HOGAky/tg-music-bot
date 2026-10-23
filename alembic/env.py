"""Асинхронное окружение Alembic для tg-music-bot.

Работает с database.models.Base.metadata и поддерживает как PostgreSQL
(asyncpg), так и SQLite (aiosqlite). URL берётся из переменной окружения
DB_URL или из .env — хардкод-эндпоинтов нет (ТЗ v3.0, п. 2.2).
"""
import asyncio
from logging.config import fileConfig

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from database.models import Base

config_alembic = context.config

if config_alembic.config_file_name is not None:
    fileConfig(config_alembic.config_file_name)


class _MigrationSettings(BaseSettings):
    """Отдельный конфиг для миграций: здесь BOT_TOKEN не обязателен —
    миграциям нужен только DB_URL (см. config.py бота)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str = ""
    DB_URL: str = "sqlite+aiosqlite:///data/musicbot.db"


def _async_url(url: str) -> str:
    """postgres(ql):// -> postgresql+asyncpg://; SQLite остаётся как есть.

    Тот же приём, что в database/session.py — asyncpg закреплён для PG-схем."""
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """SQL-скрипт без подключения к БД: alembic upgrade head --sql."""
    context.configure(
        url=_async_url(_MigrationSettings().DB_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config_alembic.get_section(config_alembic.config_ini_section) or {}
    section["sqlalchemy.url"] = _async_url(_MigrationSettings().DB_URL)
    connectable = async_engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Подключение к БД и прогон миграций (alembic upgrade head)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
