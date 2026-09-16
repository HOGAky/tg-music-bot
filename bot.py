import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import config
from database.session import init_db
from handlers import common, favorites, playlists, search


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    os.makedirs("data", exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    await init_db()

    bot = Bot(token=config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # ВАЖНО: playlists/favorites ДО search — FSM-состояния должны ловить текст раньше поиска
    dp.include_routers(common.router, favorites.router, playlists.router, search.router)

    await bot.set_my_commands([
        BotCommand(command="playlists", description="Мои плейлисты"),
        BotCommand(command="new", description="Создать плейлист"),
        BotCommand(command="fav", description="Избранное"),
        BotCommand(command="help", description="Справка"),
    ])

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")