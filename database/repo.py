from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Favorite, Playlist, PlaylistTrack, Track, User

MAX_PLAYLIST_TRACKS = 50
MAX_PLAYLISTS = 20


# ---------- users ----------
async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None) -> None:
    user = await session.get(User, tg_id)
    if user is None:
        session.add(User(id=tg_id, username=username))
        await session.commit()
    elif user.username != username:
        user.username = username
        await session.commit()


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


async def remove_track_from_playlist(session: AsyncSession, playlist_id: int, track_id: int) -> None:
    await session.execute(delete(PlaylistTrack).where(
        PlaylistTrack.playlist_id == playlist_id, PlaylistTrack.track_id == track_id))
    await session.commit()