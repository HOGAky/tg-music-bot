from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import keyboards
from callbacks import SetProtectCB, SetQualityCB
from database import repo
from database.session import SessionFactory
from services import player
from utils import msg_of, safe_edit

router = Router(name="player")

SETTINGS_TEXT = """⚙️ <b>Настройки</b>

<b>Качество</b> — битрейт mp3 при скачивании.
<b>Защита от пересылки</b> — треки нельзя переслать или сохранить."""


@router.message(Command("now"))
async def cmd_now(message: Message):
    state = player.get_now_playing(message.from_user.id)
    if state is None:
        await message.answer("Сейчас ничего не играет. Отправь название трека — найду.")
        return
    name = f"{state.artist} — {state.title}" if state.artist else state.title
    lines = [f"🎧 <b>{name}</b>",
             f"⏱ {player.fmt_duration(state.elapsed)} / {player.fmt_duration(state.duration)}"]
    if state.queue_size:
        lines.append(f"📁 {state.queue_name}: {state.position} из {state.queue_size}")
    await message.answer("\n".join(lines))


@router.message(Command("top"))
async def cmd_top(message: Message):
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
        global_top = await repo.top_tracks(session, limit=10)
        personal_top = await repo.top_tracks(session, limit=5, user_id=message.from_user.id)

    if not global_top:
        await message.answer("Пока никто ничего не слушал — будь первым 🎧")
        return

    lines = ["🏆 <b>Топ-10 бота</b>"]
    lines += [f"{i}. {_name(t)} — {n}" for i, (t, n) in enumerate(global_top, 1)]
    if personal_top:
        lines.append("\n⭐️ <b>Твой топ-5</b>")
        lines += [f"{i}. {_name(t)} — {n}" for i, (t, n) in enumerate(personal_top, 1)]
    await message.answer("\n".join(lines))


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    async with SessionFactory() as session:
        user = await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
        markup = keyboards.settings_kb(user)
    await message.answer(SETTINGS_TEXT, reply_markup=markup)


@router.callback_query(SetQualityCB.filter())
async def cb_set_quality(callback: CallbackQuery, callback_data: SetQualityCB):
    async with SessionFactory() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        await repo.set_quality(session, user.id, callback_data.quality)
        markup = keyboards.settings_kb(user)
    msg = msg_of(callback)
    if msg is not None:
        await safe_edit(msg, SETTINGS_TEXT, markup)
    await callback.answer(f"Качество: {callback_data.quality} kbps")


@router.callback_query(SetProtectCB.filter())
async def cb_toggle_protect(callback: CallbackQuery):
    async with SessionFactory() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        enabled = await repo.toggle_protect_content(session, user.id)
        markup = keyboards.settings_kb(user)
    msg = msg_of(callback)
    if msg is not None:
        await safe_edit(msg, SETTINGS_TEXT, markup)
    await callback.answer("Защита включена" if enabled else "Защита выключена")


def _name(track) -> str:
    return f"{track.artist} — {track.title}" if track.artist else track.title
