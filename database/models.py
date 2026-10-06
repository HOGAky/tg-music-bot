from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user_id
    username: Mapped[str | None] = mapped_column(String(64))
    quality: Mapped[int] = mapped_column(Integer, default=192, server_default="192")  # kbps: 128/192/320
    protect_content: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(String(512), unique=True)   # идентификатор (spotify/youtube/soundcloud)
    download_url: Mapped[str | None] = mapped_column(String(512))       # откуда реально качать
    title: Mapped[str] = mapped_column(String(256))
    artist: Mapped[str] = mapped_column(String(256), default="")
    duration: Mapped[int | None] = mapped_column(Integer)
    file_id: Mapped[str | None] = mapped_column(String(256))            # кеш telegram file_id
    s3_key: Mapped[str | None] = mapped_column(String(256))             # ключ mp3 в Object Storage


class Playlist(Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tracks: Mapped[list["Track"]] = relationship(
        secondary="playlist_tracks", order_by="PlaylistTrack.position", lazy="selectin"
    )


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("playlist_id", "track_id"),)


class TrackPlay(Base):
    __tablename__ = "track_plays"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Favorite(Base):
    __tablename__ = "favorites"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())