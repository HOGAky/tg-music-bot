"""Начальная схема: users, tracks, playlists, playlist_tracks, track_plays, favorites.

Соответствует database/models.py (Base.metadata) один-в-один — создание таблиц
через Alembic и через create_all дают идентичную схему.

Revision ID: 0001
Revises:
Create Date: 2026-10-12

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизий: строки
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        # telegram user_id: BigInteger-PK -> BIGSERIAL в PostgreSQL
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("quality", sa.Integer(), server_default="192", nullable=False),
        sa.Column("protect_content", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("download_url", sa.String(length=512), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("artist", sa.String(length=256), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("file_id", sa.String(length=256), nullable=True),
        sa.Column("s3_key", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
    )

    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_playlists_user_id", "playlists", ["user_id"], unique=False)

    op.create_table(
        "track_plays",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_track_plays_user_id", "track_plays", ["user_id"], unique=False
    )
    op.create_index(
        "ix_track_plays_track_id", "track_plays", ["track_id"], unique=False
    )

    op.create_table(
        "favorites",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "track_id"),
    )

    op.create_table(
        "playlist_tracks",
        sa.Column("playlist_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("playlist_id", "track_id"),
        sa.UniqueConstraint("playlist_id", "track_id"),
    )


def downgrade() -> None:
    # порядок обратный созданию (сначала таблицы-зависимости)
    op.drop_table("playlist_tracks")
    op.drop_table("favorites")
    op.drop_index("ix_track_plays_track_id", table_name="track_plays")
    op.drop_index("ix_track_plays_user_id", table_name="track_plays")
    op.drop_table("track_plays")
    op.drop_index("ix_playlists_user_id", table_name="playlists")
    op.drop_table("playlists")
    op.drop_table("tracks")
    op.drop_table("users")
