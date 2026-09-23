import logging

from aiogram import Router
from aiogram.types import CallbackQuery

import keyboards
from callbacks import LyricsCB, RadioCB
from database import repo
from database.session import SessionFactory
from services import lyrics, radio
from utils import msg_of

logger = logging.getLogger(__name__)

router = Router(name="track_tools")

LYRICS_LIMIT = 3500  # запас под лимит Telegram в 4096 символов
RADIO_TRACKS = 5


@router.callback_query(LyricsCB.filter())
async def cb_lyrics(callback: CallbackQuery, callback_data: LyricsCB):
    msg = msg_of(callback)
    if msg is None:
        await callback.answer("Сообщение устарело", show_alert=True)
        return
    async with SessionFactory() as session:
        track = await repo.get_track(session, callback_data.track_id)
    if track is None:
        await callback.answer("Трек не найден", show_alert=True)
        return

    await callback.answer("📝 Ищу текст...")
    text = await lyrics.fetch_lyrics(track.artist, track.title, track.duration)
    if not text:
        await msg.answer("Текст для этого трека не нашёлся 🤷")
        return
    name = f"{track.artist} — {track.title}" if track.artist else track.title
    body = text[:LYRICS_LIMIT] + ("…" if len(text) > LYRICS_LIMIT else "")
    await msg.answer(f"📝 <b>{name}</b>\n\n{body}")


@router.callback_query(RadioCB.filter())
async def cb_radio(callback: CallbackQuery, callback_data: RadioCB):
    msg = msg_of(callback)
    if msg is None:
        await callback.answer("Сообщение устарело", show_alert=True)
        return
    async with SessionFactory() as session:
        track = await repo.get_track(session, callback_data.track_id)
        if track is None:
            await callback.answer("Трек не найден", show_alert=True)
            return

        await callback.answer("📻 Подбираю похожее...")
        found = await radio.similar_tracks(track.source_url, track.artist, track.title,
                                           limit=RADIO_TRACKS)
        if not found:
            await msg.answer("Не получилось подобрать похожие треки 😔")
            return
        similar = [
            await repo.upsert_track(
                session, source_url=f.source_url, title=f.title, artist=f.artist,
                duration=f.duration, download_url=f.download_url)
            for f in found
        ]
    await msg.answer("📻 <b>Радио</b> — похожее на этот трек:",
                     reply_markup=keyboards.results_kb(similar))
