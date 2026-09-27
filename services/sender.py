import logging
import os

from aiogram.types import FSInputFile, Message

from database import repo
from database.models import Track
from services import downloader, player, searcher

logger = logging.getLogger(__name__)


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


async def _track_played(session, user_id: int | None, track: Track,
                        queue_name: str, position: int, queue_size: int) -> None:
    if user_id is None:
        return
    player.set_now_playing(user_id, track, queue_name=queue_name,
                           position=position, queue_size=queue_size)
    await repo.log_play(session, user_id, track.id)


async def send_track(msg: Message, session, track: Track, reply_markup=None, *,
                     user_id: int | None = None, queue_name: str = "",
                     position: int = 0, queue_size: int = 0,
                     status: Message | None = None, quiet: bool = False) -> bool:
    """Отправка трека: сначала пробуем кеш file_id, иначе качаем. После — чистим диск.

    status — готовое сообщение-статус (для пакетной отправки): тогда не создаём
    и не удаляем своё, текстом управляет вызывающий код.
    quiet — не писать пользователю о неудачах (батч сам посчитает их в итоге)."""
    user = await repo.get_user(session, user_id) if user_id is not None else None
    protect = bool(user.protect_content) if user is not None else False
    quality = user.quality if user is not None else 192

    # 1) мгновенная отправка из кеша Telegram
    if track.file_id:
        try:
            await msg.answer_audio(
                audio=track.file_id,
                title=track.title[:64],
                performer=(track.artist or "Unknown")[:64],
                duration=_dur(track.duration),
                reply_markup=reply_markup,
                protect_content=protect,
            )
            await _track_played(session, user_id, track, queue_name, position, queue_size)
            return True
        except Exception as e:
            logger.warning("file_id трека %s (%s) протух, перекачиваем: %s",
                           track.id, track.title, e)
            track.file_id = None
            await session.commit()

    url = await ensure_download_url(session, track)
    if not url:
        if not quiet:
            await msg.answer("Не удалось найти источник для этого трека 😔")
        return False

    own_status = status is None
    if own_status:
        status = await msg.answer("⏳ Скачиваю трек...")
    try:
        result = await downloader.download(url, quality=quality)
    except Exception as e:
        logger.exception("Ошибка скачивания url=%s track=%s (%s): %s",
                         url, track.id, track.title, e)
        if not quiet:
            try:
                await status.edit_text("⚠️ Не удалось скачать трек. Попробуй другой вариант из поиска.")
            except Exception:
                pass
        return False

    if os.path.getsize(result.path) > 49 * 1024 * 1024:  # лимит Telegram для ботов — 50 МБ
        _cleanup(result.path, result.thumbnail)
        if not quiet:
            try:
                await status.edit_text("⚠️ Трек слишком длинный — Telegram ограничивает ботов 50 МБ.")
            except Exception:
                pass
        return False

    kwargs = dict(
        audio=FSInputFile(result.path),
        title=track.title[:64],
        performer=(track.artist or "Unknown")[:64],
        duration=_dur(track.duration, result.duration),
        reply_markup=reply_markup,
        protect_content=protect,
    )
    audio_msg = None
    try:
        audio_msg = await msg.answer_audio(
            thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None, **kwargs)
    except Exception as e:
        logger.warning("Отправка с обложкой не удалась track=%s (%s): %s — пробую без обложки",
                       track.id, track.title, e)
        try:
            audio_msg = await msg.answer_audio(**kwargs)
        except Exception as e2:
            logger.exception("Отправка трека в Telegram провалилась track=%s (%s): %s",
                             track.id, track.title, e2)
            if not quiet:
                try:
                    await status.edit_text("⚠️ Telegram отказался принимать трек 😔. Попробуй другой вариант.")
                except Exception:
                    pass

    # статус удаляем только при успехе: в случае ошибки там текст для пользователя
    if own_status and audio_msg is not None:
        try:
            await status.delete()
        except Exception as del_e:
            logger.debug("Не удалось удалить статус-сообщение: %s", del_e)
    _cleanup(result.path, result.thumbnail)

    if audio_msg is None:
        return False

    await repo.set_file_id(session, track.id, audio_msg.audio.file_id)
    await _track_played(session, user_id, track, queue_name, position, queue_size)
    return True


def _cleanup(*paths: str | None) -> None:
    for p in paths:
        if p:
            try:
                os.remove(p)
            except OSError:
                pass