"""Перенос данных из SQLite в PostgreSQL (idемпотентно: merge по первичному ключу).

Использование:
    python scripts/migrate_sqlite_to_postgres.py \
        "sqlite+aiosqlite:///data/musicbot.db" \
        "postgresql+asyncpg://musicbot:ПАРОЛЬ@ХОСТ:6432/musicbot"

Перед запуском: таблицы в PostgreSQL создаются автоматически (create_all),
пустой целевой базе ничего не нужно. Повторный запуск допишет только новые строки.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from database.models import (  # noqa: E402
    Base, Favorite, Playlist, PlaylistTrack, Track, TrackPlay, User,
)

# порядок важен: сначала независимые таблицы, затем связи
TABLES = [User, Track, Favorite, Playlist, PlaylistTrack, TrackPlay]


async def migrate(src_url: str, dst_url: str) -> None:
    src = create_async_engine(src_url)
    dst = create_async_engine(dst_url)

    async with dst.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionSrc = async_sessionmaker(src, expire_on_commit=False)
    SessionDst = async_sessionmaker(dst, expire_on_commit=False)

    total = 0
    for model in TABLES:
        async with SessionSrc() as s, SessionDst() as d:
            rows = (await s.scalars(select(model))).all()
            for row in rows:
                await d.merge(row)
            await d.commit()
            print(f"{model.__tablename__}: перенесено {len(rows)}")
            total += len(rows)

    print(f"Итого: {total} строк")
    await src.dispose()
    await dst.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(migrate(sys.argv[1], sys.argv[2]))
