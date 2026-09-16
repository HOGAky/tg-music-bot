from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

import keyboards
from callbacks import FavCB, PlayCB
from database import repo
from database.session import SessionFactory
from services import searcher, sender
from utils import msg_of

router = Router(name="search")


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_text(message: Message):
    """Любой текст = либо ссылка, либо поисковый запрос."""
    text = message.text.strip()
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)

        if searcher.is_url(text):
            status = await message.answer("🔗 Обрабатываю ссылку...")
            found = await searcher.resolve_url(text)
        else:
            status = await message.answer("🔎 Ищу...")
            found = await searcher.search(text)

        if not found:
            await status.delete()
            await message.answer(
                "Ничего не нашёл 😔\nПоддерживаю: Spotify, YouTube, SoundCloud, Bandcamp и "
                "поиск по названию. Попробуй изменить запрос."
            )
            return

        tracks = [
            await repo.upsert_track(
                session, source_url=f.source_url, title=f.title, artist=f.artist,
                duration=f.duration, download_url=f.download_url)
            for f in found
        ]
        await status.delete()

        if len(tracks) == 1:
            t = tracks[0]
            in_fav = await repo.is_favorite(session, message.from_user.id, t.id)
            await sender.send_track(message, session, t, keyboards.track_kb(t.id, in_fav))
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
        await sender.send_track(msg, session, track, keyboards.track_kb(track.id, in_fav))


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
            await msg.edit_reply_markup(reply_markup=keyboards.track_kb(track.id, in_fav))
        except Exception:
            pass
    await callback.answer(note)