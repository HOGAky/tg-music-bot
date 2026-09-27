import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import config
from database.session import init_db
from handlers import common, favorites, inline, player, playlists, search, track_tools
from webapp.app import start_webapp


def _setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # собираем всё, фильтр по хендлерам
    root_logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root_logger.addHandler(console)

    file_handler = RotatingFileHandler(
        "logs/musicbot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # Уменьшаем шум от сторонних библиотек
    for name in ("aiogram.event", "aiohttp.access", "aiohttp.client", "urllib3", "httpx",
                 "yandex_music"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def main() -> None:
    _setup_logging()
    logging.info("=== musicbot запускается ===")

    os.makedirs("data", exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    await init_db()

    bot = Bot(token=config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # ВАЖНО: playlists/favorites ДО search — FSM-состояния должны ловить текст раньше поиска
    dp.include_routers(common.router, favorites.router, player.router, playlists.router,
                       track_tools.router, inline.router, search.router)

    await bot.set_my_commands([
        BotCommand(command="playlists", description="Мои плейлисты"),
        BotCommand(command="new", description="Создать плейлист"),
        BotCommand(command="fav", description="Избранное"),
        BotCommand(command="now", description="Что сейчас играет"),
        BotCommand(command="top", description="Топ треков"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="help", description="Справка"),
    ])

    runner = None
    if config.API_ENABLED:
        runner = await start_webapp()
        logging.info("WebApp HTTP-сервер включён: %s:%s", config.API_HOST, config.API_PORT)
    if config.WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🎵 Плеер", web_app=WebAppInfo(url=config.WEBAPP_URL)))

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен и готов к приёму сообщений")
    try:
        await dp.start_polling(bot)
    finally:
        if runner is not None:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен пользователем")
    except Exception:
        logging.exception("Фатальная ошибка при старте/работе бота")
        raise