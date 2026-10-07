from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message, WebAppInfo)

from config import config
from database import repo
from database.session import SessionFactory
from utils import bust_url

router = Router(name="common")

START_TEXT = """🎧 <b>Музыкальный бот</b>

Просто отправь <b>название трека</b> — я найду его и пришлю аудио.
Также понимаю ссылки: <b>Spotify, YouTube, SoundCloud</b> и др.

Под каждым треком есть кнопки:
❤️ — в избранное, 📝 — текст, 📻 — радио, 📁 — добавить в плейлист.
В любом чате работает inline: <code>@имя_бота название</code>.

<b>Команды:</b>
/playlists — мои плейлисты
/new — создать плейлист
/fav — избранное
/now — что сейчас играет
/top — топ треков
/settings — качество и защита контента
/help — справка"""


@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
    kb = None
    if config.WEBAPP_URL:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎵 Открыть плеер",
                                 web_app=WebAppInfo(url=bust_url(config.WEBAPP_URL))),
        ]])
    await message.answer(START_TEXT, reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(START_TEXT)


@router.callback_query(lambda c: c.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()