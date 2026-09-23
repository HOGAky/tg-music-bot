from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Favorite, Playlist, PlaylistTrack, Track, TrackPlay, User

MAX_PLAYLIST_TRACKS = 50
MAX_PLAYLISTS = 20
QUALITIES = (128, 192, 320)


# ---------- users ----------
async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None) -> User:
    user = await session.get(User, tg_id)
    if user is None:
        user = User(id=tg_id, username=username)
        session.add(user)
        await session.commit()
    elif user.username != username:
        user.username = username
        await session.commit()
    return user


async def get_user(session: AsyncSession, tg_id: int) -> User | None:
    return await session.get(User, tg_id)


async def set_quality(session: AsyncSession, tg_id: int, quality: int) -> None:
    user = await session.get(User, tg_id)
    if user is not None and quality in QUALITIES:
        user.quality = quality
        await session.commit()


async def toggle_protect_content(session: AsyncSession, tg_id: int) -> bool:
    user = await session.get(User, tg_id)
    if user is None:
        return False
    user.protect_content = not user.protect_content
    await session.commit()
    return user.protect_content


# ---------- tracks ----------
async def upsert_track(
    session: AsyncSession, *, source_url: str, title: str, artist: str,
    duration: int | None, download_url: str | None = None,
) -> Track:
    stmt = select(Track).where(Track.source_url == source_url)
    track = (await session.scalars(stmt)).first()
    if track is not None:
        track.title = title or track.title
        track.artist = artist or track.artist
        track.duration = duration or track.duration
        track.download_url = download_url or track.download_url
        await session.commit()
        return track
    track = Track(source_url=source_url, title=title, artist=artist,
                  duration=duration, download_url=download_url)
    session.add(track)
    try:
        await session.commit()
    except IntegrityError:  # гонка двух одинаковых запросов
        await session.rollback()
        track = (await session.scalars(stmt)).first()
    return track


async def get_track(session: AsyncSession, track_id: int) -> Track | None:
    return await session.get(Track, track_id)


async def set_file_id(session: AsyncSession, track_id: int, file_id: str) -> None:
    track = await session.get(Track, track_id)
    if track is not None:
        track.file_id = file_id
        await session.commit()


# ---------- статистика прослушиваний ----------
async def log_play(session: AsyncSession, user_id: int, track_id: int) -> None:
    session.add(TrackPlay(user_id=user_id, track_id=track_id))
    await session.commit()


async def top_tracks(session: AsyncSession, limit: int = 10,
                     user_id: int | None = None) -> list[tuple[Track, int]]:
    plays = func.count(TrackPlay.id).label("plays")
    stmt = (select(Track, plays).join(TrackPlay, TrackPlay.track_id == Track.id)
            .group_by(Track.id).order_by(plays.desc()).limit(limit))
    if user_id is not None:
        stmt = stmt.where(TrackPlay.user_id == user_id)
    return [(track, count) for track, count in (await session.execute(stmt)).all()]


# ---------- favorites ----------
async def is_favorite(session: AsyncSession, user_id: int, track_id: int) -> bool:
    stmt = select(Favorite).where(Favorite.user_id == user_id, Favorite.track_id == track_id)
    return (await session.scalars(stmt)).first() is not None


async def add_favorite(session: AsyncSession, user_id: int, track_id: int) -> None:
    if await is_favorite(session, user_id, track_id):
        return
    session.add(Favorite(user_id=user_id, track_id=track_id))
    await session.commit()


async def remove_favorite(session: AsyncSession, user_id: int, track_id: int) -> None:
    await session.execute(delete(Favorite).where(
        Favorite.user_id == user_id, Favorite.track_id == track_id))
    await session.commit()


async def get_favorites(session: AsyncSession, user_id: int, limit: int = 30) -> list[Track]:
    stmt = (select(Track).join(Favorite, Favorite.track_id == Track.id)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc()).limit(limit))
    return list((await session.scalars(stmt)).all())


# ---------- playlists ----------
async def create_playlist(session: AsyncSession, user_id: int, name: str) -> Playlist:
    pl = Playlist(user_id=user_id, name=name)
    session.add(pl)
    await session.commit()
    return pl


async def get_playlists(session: AsyncSession, user_id: int) -> list[Playlist]:
    stmt = (select(Playlist).where(Playlist.user_id == user_id)
            .order_by(Playlist.created_at.desc()).limit(MAX_PLAYLISTS))
    return list((await session.scalars(stmt)).all())


async def get_playlist(session: AsyncSession, playlist_id: int) -> Playlist | None:
    return await session.get(Playlist, playlist_id)


async def delete_playlist(session: AsyncSession, user_id: int, playlist_id: int) -> bool:
    stmt = select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
    pl = (await session.scalars(stmt)).first()
    if pl is None:
        return False
    await session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == pl.id))
    await session.delete(pl)
    await session.commit()
    return True


async def add_track_to_playlist(session: AsyncSession, playlist_id: int, track_id: int) -> bool:
    if await session.get(PlaylistTrack, (playlist_id, track_id)) is not None:
        return False
    cnt = await session.scalar(
        select(func.count()).select_from(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
    if (cnt or 0) >= MAX_PLAYLIST_TRACKS:
        return False
    session.add(PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=cnt or 0))
    await session.commit()
    return True


async def search_cached_tracks(session: AsyncSession, query: str, limit: int = 20) -> list[Track]:
    """Треки с готовым file_id — только их можно отдать в inline-режиме."""
    stmt = select(Track).where(Track.file_id.is_not(None))
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(Track.title.ilike(pattern) | Track.artist.ilike(pattern))
    return list((await session.scalars(stmt.order_by(Track.id.desc()).limit(limit))).all())


async def remove_track_from_playlist(session: AsyncSession, playlist_id: int, track_id: int) -> None:
    await session.execute(delete(PlaylistTrack).where(
        PlaylistTrack.playlist_id == playlist_id, PlaylistTrack.track_id == track_id))
    await session.commit()