import asyncio

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import keyboards
from callbacks import (PlAddQCB, PlAddTrackCB, PlDelCB, PlDelTrackCB, PlMenuCB,
                       PlNewCB, PlSelCB, PlayAllCB, PlViewCB)
from database import repo
from database.session import SessionFactory
from services import searcher, sender
from utils import msg_of, safe_edit

router = Router(name="playlists")


class PlaylistStates(StatesGroup):
    creating = State()
    adding_track = State()


async def _show_playlists(message: Message):
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
        pls = await repo.get_playlists(session, message.from_user.id)
    if not pls:
        await message.answer("У тебя пока нет плейлистов.\nСоздай первый: <code>/new Мой плейлист</code>",
                             reply_markup=keyboards.playlists_kb([]))
    else:
        await message.answer("🎵 <b>Твои плейлисты</b>", reply_markup=keyboards.playlists_kb(pls))


@router.message(Command("playlists"))
async def cmd_playlists(message: Message):
    await _show_playlists(message)


# ---------- создание ----------
@router.message(Command("new"))
async def cmd_new(message: Message, command: CommandObject, state: FSMContext):
    name = (command.args or "").strip()
    if name:
        async with SessionFactory() as session:
            await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
            pl = await repo.create_playlist(session, message.from_user.id, name[:64])
        await message.answer(f"✅ Плейлист «{pl.name}» создан.",
                             reply_markup=keyboards.back_to_playlist_kb(pl.id))
        return
    await state.set_state(PlaylistStates.creating)
    await state.update_data(pending_track_id=0)
    await message.answer("Отправь название нового плейлиста (одним сообщением):")


@router.message(PlaylistStates.creating, F.text)
async def process_new_name(message: Message, state: FSMContext):
    name = message.text.strip()[:64]
    data = await state.get_data()
    pending = data.get("pending_track_id") or 0
    async with SessionFactory() as session:
        pl = await repo.create_playlist(session, message.from_user.id, name)
        if pending:
            await repo.add_track_to_playlist(session, pl.id, pending)
    await state.clear()
    text = f"✅ Плейлист «{name}» создан." + (" Трек добавлен." if pending else "")
    await message.answer(text, reply_markup=keyboards.back_to_playlist_kb(pl.id))


# ---------- навигация ----------
@router.callback_query(PlMenuCB.filter())
async def cb_pl_menu(callback: CallbackQuery):
    msg = msg_of(callback)
    await callback.answer()
    if msg:
        await _show_playlists(msg)


@router.callback_query(PlViewCB.filter())
async def cb_pl_view(callback: CallbackQuery, callback_data: PlViewCB):
    msg = msg_of(callback)
    if msg is None:
        await callback.answer("Сообщение устарело", show_alert=True)
        return
    async with SessionFactory() as session:
        pl = await repo.get_playlist(session, callback_data.playlist_id)
    if pl is None:
        await callback.answer("Плейлист не найден", show_alert=True)
        return
    if pl.user_id != callback.from_user.id:
        await callback.answer("Это не твой плейлист 🙂", show_alert=True)
        return
    await safe_edit(msg, f"🎵 <b>{pl.name}</b> — {len(pl.tracks)} треков",
                    keyboards.playlist_kb(pl))
    await callback.answer()


# ---------- добавление треков ----------
@router.callback_query(PlSelCB.filter())
async def cb_pl_sel(callback: CallbackQuery, callback_data: PlSelCB):
    async with SessionFactory() as session:
        pls = await repo.get_playlists(session, callback.from_user.id)
    msg = msg_of(callback)
    if msg:
        await msg.answer("Выбери плейлист:",
                         reply_markup=keyboards.playlists_select_kb(pls, callback_data.track_id))
    await callback.answer()


@router.callback_query(PlAddTrackCB.filter())
async def cb_pl_add_track(callback: CallbackQuery, callback_data: PlAddTrackCB):
    async with SessionFactory() as session:
        pl = await repo.get_playlist(session, callback_data.playlist_id)
        if pl is None or pl.user_id != callback.from_user.id:
            await callback.answer("Плейлист недоступен", show_alert=True)
            return
        added = await repo.add_track_to_playlist(session, pl.id, callback_data.track_id)
    await callback.answer("✅ Добавлено" if added else "Уже есть в плейлисте")


@router.callback_query(PlNewCB.filter())
async def cb_pl_new(callback: CallbackQuery, callback_data: PlNewCB, state: FSMContext):
    await state.set_state(PlaylistStates.creating)
    await state.update_data(pending_track_id=callback_data.track_id)
    msg = msg_of(callback)
    if msg:
        await msg.answer("Отправь название нового плейлиста (одним сообщением):")
    await callback.answer()


@router.callback_query(PlAddQCB.filter())
async def cb_pl_add_q(callback: CallbackQuery, callback_data: PlAddQCB, state: FSMContext):
    await state.set_state(PlaylistStates.adding_track)
    await state.update_data(playlist_id=callback_data.playlist_id)
    msg = msg_of(callback)
    if msg:
        await msg.answer("Отправь название трека или ссылку — добавлю в плейлист:")
    await callback.answer()


@router.message(PlaylistStates.adding_track, F.text)
async def process_add_track(message: Message, state: FSMContext):
    data = await state.get_data()
    playlist_id = data["playlist_id"]
    text = message.text.strip()

    found = (await searcher.resolve_url(text) if searcher.is_url(text)
             else await searcher.search(text, limit=5))
    if not found:
        await message.answer("Ничего не нашёл. Попробуй другой запрос.")
        return

    async with SessionFactory() as session:
        tracks = [await repo.upsert_track(
            session, source_url=f.source_url, title=f.title, artist=f.artist,
            duration=f.duration, download_url=f.download_url) for f in found]
    await message.answer("Что добавить?",
                         reply_markup=keyboards.add_to_playlist_kb(playlist_id, tracks))
    await state.clear()


# ---------- удаление ----------
@router.callback_query(PlDelCB.filter())
async def cb_pl_del(callback: CallbackQuery, callback_data: PlDelCB):
    async with SessionFactory() as session:
        deleted = await repo.delete_playlist(session, callback.from_user.id, callback_data.playlist_id)
    await callback.answer("🗑 Плейлист удалён" if deleted else "Плейлист не найден",
                          show_alert=not deleted)
    msg = msg_of(callback)
    if deleted and msg:
        try:
            await msg.delete()
        except Exception:
            pass


@router.callback_query(PlDelTrackCB.filter())
async def cb_pl_del_track(callback: CallbackQuery, callback_data: PlDelTrackCB):
    async with SessionFactory() as session:
        pl = await repo.get_playlist(session, callback_data.playlist_id)
        if pl is None or pl.user_id != callback.from_user.id:
            await callback.answer("Недоступно", show_alert=True)
            return
        await repo.remove_track_from_playlist(session, pl.id, callback_data.track_id)
        pl = await repo.get_playlist(session, pl.id)
    msg = msg_of(callback)
    if msg:
        await safe_edit(msg, f"🎵 <b>{pl.name}</b> — {len(pl.tracks)} треков", keyboards.playlist_kb(pl))
    await callback.answer("Убрано из плейлиста")


# ---------- воспроизведение плейлиста ----------
@router.callback_query(PlayAllCB.filter())
async def cb_play_all(callback: CallbackQuery, callback_data: PlayAllCB):
    await callback.answer("▶️ Начинаю...")
    msg = msg_of(callback)
    if msg is None:
        return
    async with SessionFactory() as session:
        pl = await repo.get_playlist(session, callback_data.playlist_id)
        if pl is None or pl.user_id != callback.from_user.id:
            await callback.answer("Плейлист недоступен", show_alert=True)
            return
        tracks = list(pl.tracks)
    if not tracks:
        await msg.answer("Плейлист пуст — добавь треки через «➕ Добавить трек».")
        return

    status = await msg.answer(f"▶️ Играет «{pl.name}» ({len(tracks)} треков)")
    for i, t in enumerate(tracks, 1):
        async with SessionFactory() as session:
            track = await repo.get_track(session, t.id)
            if track is None:
                continue
            in_fav = await repo.is_favorite(session, callback.from_user.id, track.id)
            await sender.send_track(msg, session, track, keyboards.track_kb(track.id, in_fav))
        if i < len(tracks):
            await asyncio.sleep(1.0)  # защита от rate limit Telegram
    try:
        await status.delete()
    except Exception:
        pass