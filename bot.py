import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from config import config
from database.session import init_db
from handlers import common, favorites, inline, player, playlists, search, track_tools
from webapp.app import start_webapp


def _setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # собираем всё, фильтр по хендлерам
    root_logger.handlers.clear()

    if config.LOG_FORMAT == "json":
        try:
            from pythonjsonlogger import jsonlogger
            fmt = jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        except ImportError:
            fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    else:
        fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
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


async def _make_dispatcher() -> Dispatcher:
    """RedisStorage для мультиинстансности; при недоступности Redis — MemoryStorage."""
    if not (config.USE_REDIS and config.REDIS_URL):
        return Dispatcher()
    try:
        import redis.asyncio as aioredis
        from aiogram.fsm.storage.redis import RedisStorage

        client = aioredis.from_url(config.REDIS_URL, socket_connect_timeout=3)
        await client.ping()  # проверяем связь до выбора хранилища
        logging.info("FSM storage: Redis (%s)", config.REDIS_URL)
        return Dispatcher(storage=RedisStorage(client))
    except Exception as e:
        logging.warning("Redis недоступен (%s) — используется MemoryStorage", e)
        return Dispatcher()


class RetryOnNetworkError(BaseRequestMiddleware):
    """Повторяем запросы к Telegram при сетевых сбоях: из облака часть новых
    соединений к api.telegram.org рвётся — повтор обычно проходит."""

    def __init__(self, attempts: int = 3) -> None:
        self._attempts = attempts

    async def __call__(self, method, session, bot, request_timeout=None, *args, **kwargs):
        for attempt in range(1, self._attempts + 1):
            try:
                return await session.make_request(bot, method, request_timeout=request_timeout)
            except TelegramRetryAfter as e:
                if attempt == self._attempts:
                    raise
                await asyncio.sleep(e.retry_after + 0.5)
            except TelegramNetworkError:
                if attempt == self._attempts:
                    raise
                await asyncio.sleep(1.5 * attempt)


async def main() -> None:
    _setup_logging()
    logging.info("=== musicbot запускается ===")

    os.makedirs("data", exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    await init_db()

    bot = Bot(token=config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(RetryOnNetworkError())
    dp = await _make_dispatcher()
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

    # Webhook или Polling режим
    if config.WEBHOOK_ENABLED:
        if not config.WEBHOOK_URL:
            logging.error("WEBHOOK_ENABLED=true, но WEBHOOK_URL не задан!")
            raise ValueError("WEBHOOK_URL обязателен при WEBHOOK_ENABLED=true")

        webhook_url = f"{config.WEBHOOK_URL}{config.WEBHOOK_PATH}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET if config.WEBHOOK_SECRET else None,
            drop_pending_updates=True,
        )
        logging.info("Webhook установлен: %s", webhook_url)

        # Если API не включен, поднимаем aiohttp сервер только для webhook
        if not config.API_ENABLED:
            from aiohttp import web
            app = web.Application()
            webhook_handler = SimpleRequestHandler(
                dispatcher=dp,
                bot=bot,
                secret_token=config.WEBHOOK_SECRET if config.WEBHOOK_SECRET else None,
            )
            webhook_handler.register(app, path=config.WEBHOOK_PATH)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host=config.API_HOST, port=config.API_PORT)
            await site.start()
            logging.info("Webhook HTTP-сервер запущен: %s:%s", config.API_HOST, config.API_PORT)

        logging.info("Бот запущен в webhook режиме")
        try:
            # Просто держим процесс активным
            while True:
                await asyncio.sleep(3600)
        finally:
            await bot.delete_webhook()
            logging.info("Webhook удалён")
            if runner is not None:
                await runner.cleanup()
    else:
        # Polling режим
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Бот запущен в polling режиме")
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