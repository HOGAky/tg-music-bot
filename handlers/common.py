from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from database import repo
from database.session import SessionFactory

router = Router(name="common")

START_TEXT = """🎧 <b>Музыкальный бот</b>

Просто отправь <b>название трека</b> — я найду его и пришлю аудио.
Также понимаю ссылки: <b>Spotify, YouTube, SoundCloud</b> и др.

Под каждым треком есть кнопки:
❤️ — в избранное, 📁 — добавить в плейлист.

<b>Команды:</b>
/playlists — мои плейлисты
/new — создать плейлист
/fav — избранное
/help — справка"""


@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(START_TEXT)


@router.callback_query(lambda c: c.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()