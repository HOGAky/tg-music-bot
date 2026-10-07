import asyncio
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
import zipfile

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, FSInputFile, LinkPreviewOptions, Message

import keyboards
from callbacks import DlAllCB, DlStopCB, FavCB, PlayCB
from config import config
from database import repo
from database.models import Track
from database.session import SessionFactory
from services import downloader, searcher, sender
from utils import msg_of, short
from webapp.app import batch_zip_url, register_batch_zip, tunnel_alive

logger = logging.getLogger(__name__)

router = Router(name="search")

# ---------- батчи «скачать всё» (альбом/плейлист по ссылке) ----------
# ключ -> {"user_id", "ids", "cancel", "busy", "ts"}; живёт в памяти процесса
_batches: dict[str, dict] = {}
BATCH_TTL = 6 * 3600.0

ZIP_THRESHOLD = 50        # больше треков — отдаём zip-архивом вместо сообщений
ZIP_PART_LIMIT = 40 * 1024 * 1024  # запас до лимита Telegram в 50 МБ на файл (fallback-режим)


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

        try:
            tracks = [
                await repo.upsert_track(
                    session, source_url=f.source_url, title=f.title, artist=f.artist,
                    duration=f.duration, download_url=f.download_url)
                for f in found
            ]
            tracks = [t for t in tracks if t is not None]
        except Exception as e:
            # не оставляем пользователя с молчаливым «Ищу...» при любой ошибке БД
            logger.exception("Не удалось сохранить результаты по %r: %s", text, e)
            await message.answer("⚠️ Нашёл треки, но не смог их сохранить. Попробуй ещё раз через минуту.")
            return
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
            zip_note = ("\n\n📦 Треков больше 50 — «Скачать все» соберёт их "
                        "в один zip-архив.") if len(tracks) > ZIP_THRESHOLD else ""
            await message.answer(
                f"🎧 Плейлист из <b>{len(tracks)}</b> треков — можно скачать всё разом "
                f"или слушать по одному (первые {shown}):{zip_note}",
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

    async with SessionFactory() as session:
        user = await repo.get_user(session, callback.from_user.id)
    quality = user.quality if user is not None else 192
    protect = bool(user.protect_content) if user is not None else False

    zip_mode = total > ZIP_THRESHOLD
    single = zip_mode and await tunnel_alive()
    progress = await msg.answer(
        f"⬇️ Скачиваю {total} треков" + (" в zip-архив…" if single else "…" if zip_mode else ""),
        reply_markup=keyboards.stop_kb(callback_data.key))

    parts = 0
    sent = failed = 0
    if single:
        sent, failed = await _run_full_zip_batch(
            msg, progress, batch, callback_data.key, ids, quality, protect)
    elif zip_mode:
        sent, failed, parts = await _run_parts_batch(msg, progress, batch, ids, quality, protect)
    else:
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
    if single:
        result = f"в архиве {sent} из {total} треков"
    elif zip_mode:
        result = f"упаковано и отправлено {sent} из {total} ({parts} архив(ов))"
    else:
        result = f"отправлено {sent} из {total}"
    note = f"\n⚠️ {failed} трек(ов) не удалось получить" if failed else ""
    try:
        await progress.edit_text(f"{tail}: {result}.{note}",
                                 reply_markup=None)
    except Exception:
        pass


@router.callback_query(DlStopCB.filter())
async def cb_dl_stop(callback: CallbackQuery, callback_data: DlStopCB):
    batch = _batches.get(callback_data.key)
    if batch is not None:
        batch["cancel"] = True
    await callback.answer("⏹ Остановлюсь после текущего трека")


# ---------- zip-режим: плейлисты > ZIP_THRESHOLD треков ----------
def _safe_name(artist: str, title: str) -> str:
    text = " - ".join(p for p in (artist, title) if p)
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text)
    return short(cleaned.strip(" ."), 100) or "track"


def _make_zip(zip_path: str, staging: list[tuple[str, str]]) -> int:
    """mp3 уже сжаты — пакуем без дожатия (быстрее и так же по размеру)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for path, name in staging:
            zf.write(path, arcname=name)
    return os.path.getsize(zip_path)


async def _run_parts_batch(msg: Message, progress: Message, batch: dict,
                           ids: list[int], quality: int, protect: bool) -> tuple[int, int, int]:
    """Fallback, когда веб-сервер недоступен: zip-части до ~40 МБ, чтобы
    скачанное не терялось даже при остановке."""
    total = len(ids)
    width = len(str(total))
    tmp = tempfile.mkdtemp(prefix="musicbot_batch_")
    sent = failed = 0
    parts = 0
    staging: list[tuple[str, str]] = []  # (файл в кеше, имя внутри архива)
    staging_size = 0

    async def flush() -> None:
        nonlocal staging, staging_size, parts
        if not staging:
            return
        parts += 1
        count = len(staging)
        zip_path = os.path.join(tmp, f"part{parts}.zip")
        size_mb = (await asyncio.to_thread(_make_zip, zip_path, staging)) // (1024 * 1024)
        try:
            await progress.edit_text(f"📦 Часть {parts} ({count} треков, ~{size_mb} МБ) — отправляю…")
        except Exception:
            pass
        try:
            await msg.answer_document(
                FSInputFile(zip_path),
                caption=f"📦 Часть {parts} — {count} треков",
                protect_content=protect)
        except Exception as e:
            logger.exception("Не удалось отправить архив части %s: %s", parts, e)
        for path, _ in staging:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.remove(zip_path)
        except OSError:
            pass
        staging, staging_size = [], 0

    for i, track_id in enumerate(ids, 1):
        if batch["cancel"]:
            break
        async with SessionFactory() as session:
            track = await repo.get_track(session, track_id)
            if track is None:
                failed += 1
                continue
            url = await sender.ensure_download_url(session, track)
            name = f"{i:0{width}d} — {_safe_name(track.artist, track.title)}.mp3"
        if not url:
            failed += 1
            continue
        try:
            await progress.edit_text(f"⬇️ [{i}/{total}] {short(track.title, 40)}")
        except Exception:
            pass
        try:
            result = await downloader.download(url, quality=quality)
        except Exception as e:
            logger.warning("Батч: не скачался трек %s (%s): %s", track_id, track.title, e)
            failed += 1
            continue
        staging.append((result.path, name))
        staging_size += os.path.getsize(result.path)
        sent += 1
        if staging_size >= ZIP_PART_LIMIT:
            await flush()
    await flush()  # хвост; при остановке — досылает накопленное

    shutil.rmtree(tmp, ignore_errors=True)
    return sent, failed, parts


async def _run_full_zip_batch(msg: Message, progress: Message, batch: dict, key: str,
                              ids: list[int], quality: int, protect: bool) -> tuple[int, int]:
    """Один zip-архив на весь плейлист. Telegram не даёт ботам слать файлы
    больше 50 МБ, поэтому архив, который не влезает, отдаётся ссылкой через
    веб-сервер бота. Треки качаются параллельно и сразу дописываются в архив,
    так что на диске не копится лишнего."""
    total = len(ids)
    width = len(str(total))
    tmp = tempfile.mkdtemp(prefix="musicbot_zip_")
    zip_path = os.path.join(tmp, "playlist.zip")
    sent = failed = 0

    # 1) треки из БД одним запросом
    async with SessionFactory() as session:
        found = await repo.get_tracks(session, ids)
    items = [(i, found[tid]) for i, tid in enumerate(ids, 1) if tid in found]
    failed += total - len(items)

    # 2) недостающие ссылки ищем параллельно (поиск по YT Music), без БД
    sem = asyncio.Semaphore(4)

    async def resolve(t: Track) -> None:
        if batch["cancel"] or t.download_url:
            return
        query = f"{t.artist} {t.title}".strip()
        if not query:
            return
        async with sem:
            results = await searcher.search(query, limit=1)
        if results and results[0].download_url:
            t.download_url = results[0].download_url

    await asyncio.gather(*(resolve(t) for _, t in items))
    async with SessionFactory() as session:  # найденные url — одним коммитом
        for _, t in items:
            if t.download_url:
                db_track = await session.get(Track, t.id)
                if db_track is not None and not db_track.download_url:
                    db_track.download_url = t.download_url
        await session.commit()

    # 3) параллельное скачивание; готовый mp3 сразу дописываем в архив
    zf = zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED)
    zip_lock = asyncio.Lock()
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
    done = 0
    last_edit = 0.0

    async def one(index: int, t: Track) -> None:
        nonlocal done, failed, last_edit
        async with sem:
            if batch["cancel"] or not t.download_url:
                return
            try:
                dl = await downloader.download(t.download_url, quality=quality)
            except Exception as e:
                logger.warning("Батч zip: не скачался трек %s (%s): %s", t.id, t.title, e)
                failed += 1
                return
        name = f"{index:0{width}d} — {_safe_name(t.artist, t.title)}.mp3"
        async with zip_lock:
            try:
                await asyncio.to_thread(zf.write, dl.path, name)
            except Exception as e:  # дубликат/исчезнувший файл — не роняем весь батч
                logger.warning("Батч zip: не упаковать %s: %s", name, e)
                failed += 1
                return
            try:
                os.remove(dl.path)
            except OSError:
                pass
        done += 1
        now = time.monotonic()
        if now - last_edit > 1.2:  # не спамим правками прогресса
            last_edit = now
            try:
                await progress.edit_text(f"⬇️ скачано {done}/{total}")
            except Exception:
                pass

    await asyncio.gather(*(asyncio.create_task(one(i, t)) for i, t in items))
    await asyncio.to_thread(zf.close)
    sent = done

    # 4) доставка: файлом, если влез в лимит Telegram, иначе ссылкой
    size = os.path.getsize(zip_path)
    if size <= 49 * 1024 * 1024:
        try:
            await msg.answer_document(
                FSInputFile(zip_path),
                caption=f"📦 Плейлист — {sent} треков",
                protect_content=protect)
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:
            logger.exception("Не удалось отправить zip-архив: %s", e)
            await msg.answer("⚠️ Архив собран, но Telegram не принял файл.")
        return sent, failed

    link = batch_zip_url(key)
    if link:
        register_batch_zip(key, zip_path, f"playlist_{total}tracks.zip")
        gb = size / (1024 ** 3)
        await msg.answer(
            f"📦 <b>Архив готов</b>: {sent} треков, {gb:.2f} ГБ.\n"
            f"Telegram не пропускает файлы больше 50 МБ, поэтому забирай ссылкой:\n"
            f'<a href="{link}">⬇️ Скачать zip</a>',
            link_preview_options=LinkPreviewOptions(is_disabled=True))
        # файл лежит на диске до 24 часов, потом очистится сам
    else:
        await msg.answer("⚠️ Архив собран, но веб-сервер недоступен — ссылку дать не могу.")
    return sent, failed