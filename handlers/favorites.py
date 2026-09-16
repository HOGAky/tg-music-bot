from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import keyboards
from callbacks import FavDelCB
from database import repo
from database.session import SessionFactory
from utils import msg_of

router = Router(name="favorites")


@router.message(Command("fav", "favorites"))
async def cmd_fav(message: Message):
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
        tracks = await repo.get_favorites(session, message.from_user.id)
    if not tracks:
        await message.answer("В избранном пока пусто.\nПослушай трек и нажми под ним ❤️")
        return
    await message.answer(f"❤️ <b>Избранное</b> ({len(tracks)}):",
                         reply_markup=keyboards.favorites_kb(tracks))


@router.callback_query(FavDelCB.filter())
async def cb_fav_del(callback: CallbackQuery, callback_data: FavDelCB):
    async with SessionFactory() as session:
        await repo.remove_favorite(session, callback.from_user.id, callback_data.track_id)
        tracks = await repo.get_favorites(session, callback.from_user.id)
    msg = msg_of(callback)
    if msg is not None:
        try:
            await msg.edit_reply_markup(reply_markup=keyboards.favorites_kb(tracks))
        except Exception:
            pass
    await callback.answer("Убрано из избранного")