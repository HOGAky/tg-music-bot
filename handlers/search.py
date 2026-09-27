import asyncio
import logging
import secrets
import time

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

import keyboards
from callbacks import DlAllCB, DlStopCB, FavCB, PlayCB
from database import repo
from database.session import SessionFactory
from services import searcher, sender
from utils import msg_of, short

logger = logging.getLogger(__name__)

router = Router(name="search")

# ---------- батчи «скачать всё» (альбом/плейлист по ссылке) ----------
# ключ -> {"user_id", "ids", "cancel", "busy", "ts"}; живёт в памяти процесса
_batches: dict[str, dict] = {}
BATCH_TTL = 6 * 3600.0


def _register_batch(user_id: int, track_ids: list[int]) -> str:
    now = time.monotonic()
    for stale in [k for k, v in _batches.items() if now - v["ts"] > BATCH_TTL]:
        _batches.pop(stale, None)
    key = secrets.token_hex(4)
    _batches[key] = {"user_id": user_id, "ids": track_ids,
                     "cancel": False, "busy": False, "ts": now}
    return key


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_text(message: Message):
    """Любой текст = либо ссылка, либо поисковый запрос."""
    text = message.text.strip()
    user_id = message.from_user.id
    logger.info("user=%s запрос: %r (url=%s)", user_id, text, searcher.is_url(text))
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id, message.from_user.username)

        if searcher.is_url(text):
            status = await message.answer("🔗 Обрабатываю ссылку...")
            found = await searcher.resolve_url(text)
        else:
            status = await message.answer("🔎 Ищу...")
            found = await searcher.search(text)

        if not found:
            try:
                await status.delete()
            except Exception as e:
                logger.debug("Не удалось удалить статус-сообщение: %s", e)
            await message.answer(
                f"Ничего не нашёл по запросу <i>{text[:200]}</i> 😔\n"
                "Поддерживаю: Spotify, Яндекс Музыку, YouTube, SoundCloud, Bandcamp и "
                "поиск по названию. Попробуй изменить запрос."
            )
            return

        tracks = [
            await repo.upsert_track(
                session, source_url=f.source_url, title=f.title, artist=f.artist,
                duration=f.duration, download_url=f.download_url)
            for f in found
        ]
        try:
            await status.delete()
        except Exception as e:
            logger.debug("Не удалось удалить статус-сообщение: %s", e)

        if len(tracks) == 1:
            t = tracks[0]
            in_fav = await repo.is_favorite(session, message.from_user.id, t.id)
            await sender.send_track(
                message, session, t,
                keyboards.track_kb(t.id, in_fav, share_query=f"{t.artist} {t.title}".strip()),
                user_id=message.from_user.id)
        elif searcher.is_url(text):
            # ссылка на альбом/плейлист: предлагаем скачать всё целиком
            key = _register_batch(message.from_user.id, [t.id for t in tracks])
            shown = min(len(tracks), keyboards.COLLECTION_SHOWN)
            await message.answer(
                f"🎧 Плейлист из <b>{len(tracks)}</b> треков — можно скачать всё разом "
                f"или слушать по одному (первые {shown}):",
                reply_markup=keyboards.collection_kb(tracks, key))
        else:
            what = "треков" if len(tracks) > 1 else "трек"
            await message.answer(f"🎧 Нашёл {len(tracks)} {what} — выбирай:",
                                 reply_markup=keyboards.results_kb(tracks))


@router.callback_query(PlayCB.filter())
async def cb_play(callback: CallbackQuery, callback_data: PlayCB):
    await callback.answer()
    msg = msg_of(callback)
    if msg is None:
        await callback.answer("Сообщение устарело, отправь запрос заново", show_alert=True)
        return
    async with SessionFactory() as session:
        track = await repo.get_track(session, callback_data.track_id)
        if track is None:
            await callback.answer("Трек не найден", show_alert=True)
            return
        in_fav = await repo.is_favorite(session, callback.from_user.id, track.id)
        await sender.send_track(
            msg, session, track,
            keyboards.track_kb(track.id, in_fav,
                               share_query=f"{track.artist} {track.title}".strip()),
            user_id=callback.from_user.id)


@router.callback_query(FavCB.filter())
async def cb_fav(callback: CallbackQuery, callback_data: FavCB):
    """Переключатель избранного."""
    async with SessionFactory() as session:
        track = await repo.get_track(session, callback_data.track_id)
        if track is None:
            await callback.answer("Трек не найден", show_alert=True)
            return
        if await repo.is_favorite(session, callback.from_user.id, track.id):
            await repo.remove_favorite(session, callback.from_user.id, track.id)
            in_fav, note = False, "💔 Убрано из избранного"
        else:
            await repo.add_favorite(session, callback.from_user.id, track.id)
            in_fav, note = True, "❤️ Добавлено в избранное"
    msg = msg_of(callback)
    if msg is not None:
        try:
            await msg.edit_reply_markup(reply_markup=keyboards.track_kb(
                track.id, in_fav, share_query=f"{track.artist} {track.title}".strip()))
        except Exception as e:
            logger.debug("Не удалось обновить клавиатуру избранного user=%s track=%s: %s",
                         callback.from_user.id, track.id, e)
    await callback.answer(note)


# ---------- скачивание коллекции целиком ----------
@router.callback_query(DlAllCB.filter())
async def cb_download_all(callback: CallbackQuery, callback_data: DlAllCB):
    msg = msg_of(callback)
    batch = _batches.get(callback_data.key)
    if msg is None or batch is None or batch["user_id"] != callback.from_user.id:
        await callback.answer("Сообщение устарело — отправь ссылку заново", show_alert=True)
        return
    if batch["busy"]:
        await callback.answer("Уже качается — подожди 🙂", show_alert=True)
        return

    batch["busy"] = True
    ids: list[int] = batch["ids"]
    total = len(ids)
    await callback.answer("⏬ Начинаю")
    progress = await msg.answer(f"⏬ Скачиваю плейлист: 0/{total}",
                                reply_markup=keyboards.stop_kb(callback_data.key))
    sent = failed = 0
    for i, track_id in enumerate(ids, 1):
        if batch["cancel"]:
            break
        async with SessionFactory() as session:
            track = await repo.get_track(session, track_id)
            if track is None:
                failed += 1
                continue
            try:  # имя трека в прогресс до (возможно долгого) скачивания
                await progress.edit_text(f"⬇️ [{i}/{total}] {short(track.title, 40)}")
            except Exception:
                pass
            in_fav = await repo.is_favorite(session, callback.from_user.id, track.id)
            ok = await sender.send_track(
                msg, session, track,
                keyboards.track_kb(track.id, in_fav,
                                   share_query=f"{track.artist} {track.title}".strip()),
                user_id=callback.from_user.id, queue_name="Плейлист",
                position=i, queue_size=total,
                status=progress, quiet=True)
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
        if i < total:
            await asyncio.sleep(1.0)  # защита от rate limit Telegram

    stopped = batch["cancel"]
    _batches.pop(callback_data.key, None)
    tail = "⏹ Остановлено" if stopped else "✅ Готово"
    note = f"\n⚠️ {failed} трек(ов) не удалось получить" if failed else ""
    try:
        await progress.edit_text(f"{tail}: отправлено {sent} из {total}.{note}",
                                 reply_markup=None)
    except Exception:
        pass


@router.callback_query(DlStopCB.filter())
async def cb_dl_stop(callback: CallbackQuery, callback_data: DlStopCB):
    batch = _batches.get(callback_data.key)
    if batch is not None:
        batch["cancel"] = True
    await callback.answer("⏹ Остановлюсь после текущего трека")