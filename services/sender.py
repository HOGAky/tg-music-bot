import os

from aiogram.types import FSInputFile, Message

from config import config
from database import repo
from database.models import Track
from services import downloader, searcher


def _dur(*values) -> int | None:
    """Telegram требует целые секунды; float от yt-dlp недопустим."""
    for v in values:
        if v:
            return int(v)
    return None


async def ensure_download_url(session, track: Track) -> str | None:
    """Для треков без прямой ссылки (например, из Spotify-плейлиста) — ищем на YT Music."""
    if track.download_url:
        return track.download_url
    query = f"{track.artist} {track.title}".strip()
    if not query:
        return None
    results = await searcher.search(query, limit=1)
    if not results:
        return None
    track.download_url = results[0].download_url
    await session.commit()
    return track.download_url


async def send_track(msg: Message, session, track: Track, reply_markup=None) -> bool:
    """Отправка трека: сначала пробуем кеш file_id, иначе качаем. После — чистим диск."""
    # 1) мгновенная отправка из кеша Telegram
    if track.file_id:
        try:
            await msg.answer_audio(
                audio=track.file_id,
                title=track.title[:64],
                performer=(track.artist or "Unknown")[:64],
                duration=_dur(track.duration),
                reply_markup=reply_markup,
            )
            return True
        except Exception:  # file_id протух — перекачиваем
            track.file_id = None
            await session.commit()

    url = await ensure_download_url(session, track)
    if not url:
        await msg.answer("Не удалось найти источник для этого трека 😔")
        return False

    status = await msg.answer("⏳ Скачиваю трек...")
    try:
        result = await downloader.download(url)
    except Exception:
        await status.edit_text("⚠️ Не удалось скачать трек. Попробуй другой вариант из поиска.")
        return False

    if os.path.getsize(result.path) > 49 * 1024 * 1024:  # лимит Telegram для ботов — 50 МБ
        await status.edit_text("⚠️ Трек слишком длинный — Telegram ограничивает ботов 50 МБ.")
        _cleanup(result.path, result.thumbnail)
        return False

    kwargs = dict(
        audio=FSInputFile(result.path),
        title=track.title[:64],
        performer=(track.artist or "Unknown")[:64],
        duration=_dur(track.duration, result.duration),
        reply_markup=reply_markup,
    )
    try:
        audio_msg = await msg.answer_audio(
            thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None, **kwargs)
    except Exception:  # вдруг не понравилась обложка — отправим без неё
        audio_msg = await msg.answer_audio(**kwargs)
    finally:
        try:
            await status.delete()
        except Exception:
            pass
        _cleanup(result.path, result.thumbnail)

    await repo.set_file_id(session, track.id, audio_msg.audio.file_id)
    return True


def _cleanup(*paths: str | None) -> None:
    for p in paths:
        if p:
            try:
                os.remove(p)
            except OSError:
                pass