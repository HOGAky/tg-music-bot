import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BotCommand, MenuButtonDefault, MenuButtonWebApp, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
from redis.exceptions import RedisError

from config import config
from database.session import engine, init_db
from handlers import common, favorites, inline, player, playlists, search, track_tools
from health import healthz, set_bot_ready, set_health_redis_client
from monitor import (
    MAX_QUEUED_LOGS,
    TelegramLogHandler,
    run_monitoring_bot,
)
from services import downloader
from services.storage import storage
from utils import bust_url
from webapp.app import start_webapp

logger = logging.getLogger(__name__)


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
    file_handler.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # Уменьшаем шум от сторонних библиотек
    for name in (
        "aiogram.event",
        "aiohttp.access",
        "aiohttp.client",
        "aiosqlite",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "urllib3",
        "httpx",
        "yandex_music",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


async def _make_dispatcher() -> tuple[Dispatcher, object | None]:
    """RedisStorage для мультиинстансности, если он явно включён.

    Возвращает (диспетчер, redis-клиент): клиент нужен, чтобы закрыть соединение
    при graceful shutdown (ТЗ v3.0, п. 3.2)."""
    if not config.USE_REDIS:
        return Dispatcher(), None
    if not config.REDIS_URL:
        raise ValueError("REDIS_URL обязателен при USE_REDIS=true")

    import redis.asyncio as aioredis
    from aiogram.fsm.storage.redis import RedisStorage

    client = aioredis.from_url(config.REDIS_URL, socket_connect_timeout=3)
    try:
        await client.ping()  # проверяем связь до выбора хранилища (ТЗ, п. 3.2)
    except RedisError:
        await client.aclose()
        logger.exception("Redis настроен, но недоступен")
        raise
    logger.info("FSM storage: Redis")
    return Dispatcher(storage=RedisStorage(client)), client


class BoundedWebhookRequestHandler(SimpleRequestHandler):
    def __init__(
        self,
        *,
        dispatcher: Dispatcher,
        bot: Bot,
        secret_token: str | None,
        max_concurrent_updates: int,
    ) -> None:
        super().__init__(
            dispatcher=dispatcher,
            bot=bot,
            handle_in_background=True,
            secret_token=secret_token,
        )
        self._update_slots = asyncio.BoundedSemaphore(max_concurrent_updates)

    async def handle(self, request: web.Request) -> web.Response:
        bot = await self.resolve_bot(request)
        if not self.verify_secret(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), bot
        ):
            return web.Response(body="Unauthorized", status=401)
        if self._update_slots.locked():
            return web.Response(
                text="Bot is busy",
                status=503,
                headers={"Retry-After": "1"},
            )

        await self._update_slots.acquire()
        try:
            update = await request.json(loads=bot.session.json_loads)
        except BaseException:
            self._update_slots.release()
            raise

        task = asyncio.create_task(self._process_update(bot, update))
        self._background_feed_update_tasks.add(task)
        task.add_done_callback(self._background_feed_update_tasks.discard)
        return web.json_response({}, dumps=bot.session.json_dumps)

    async def _process_update(self, bot: Bot, update: dict) -> None:
        try:
            await self._background_feed_update(bot, update)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Webhook update processing failed")
        finally:
            self._update_slots.release()

    async def close(self) -> None:
        tasks = set(self._background_feed_update_tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=30)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        await super().close()


class RetryOnNetworkError(BaseRequestMiddleware):
    """Повторяем запросы к Telegram при сетевых сбоях: из облака часть новых
    соединений к api.telegram.org рвётся (обрыв TCP, TLS handshake) — повтор
    обычно проходит. До 5 попыток с экспоненциальной задержкой (ТЗ v3.0, п. 3.1)."""

    def __init__(self, attempts: int = 5) -> None:
        self._attempts = attempts

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Экспоненциальная пауза: 0.5, 1, 2, 4 ... максимум 30 секунд."""
        return min(0.5 * (2 ** (attempt - 1)), 30.0)

    async def __call__(self, make_request, bot, method):
        for attempt in range(1, self._attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as e:
                if attempt == self._attempts:
                    raise
                await asyncio.sleep(e.retry_after + 0.5)
            except (TelegramNetworkError, aiohttp.ClientError, OSError) as e:
                # OSError покрывает в т.ч. ssl.SSLError/TimeoutError/обрывы соединения
                if attempt == self._attempts:
                    raise
                delay = self._backoff(attempt)
                logger.warning(
                    "Сетевой сбой Telegram (попытка %s/%s): %s — повтор через %.1f с",
                    attempt, self._attempts, e, delay,
                )
                await asyncio.sleep(delay)


def _shutdown_signal_handler(stop_event: asyncio.Event):
    """Обработчик SIGINT/SIGTERM: ставит stop_event (ТЗ v3.0, п. 3.1)."""

    def handler(signum, frame=None) -> None:
        logger.info("Получен сигнал %s — начинаю корректную остановку", signum)
        stop_event.set()

    return handler


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """SIGINT/SIGTERM -> запрос graceful shutdown (ТЗ v3.0, п. 3.1)."""
    handler = _shutdown_signal_handler(stop_event)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handler, sig)
        except (NotImplementedError, RuntimeError):  # Windows: loop-хендлеров нет
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, RuntimeError):
                logger.debug("Не удалось перехватить сигнал %s", sig)


async def _poll_until_stop(
    dp: Dispatcher, bot: Bot, stop_event: asyncio.Event
) -> None:
    """Polling идёт, пока не пришёл SIGINT/SIGTERM (или пока не упадёт сам)."""
    poll_task = asyncio.create_task(
        dp.start_polling(
            bot,
            tasks_concurrency_limit=config.MAX_CONCURRENT_UPDATES,
        )
    )
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait(
            {poll_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (poll_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(poll_task, stop_task, return_exceptions=True)
    if not poll_task.cancelled() and poll_task.exception() is not None:
        raise poll_task.exception()  # сбой polling не глушим — пусть увидит main


async def _start_healthz_server():
    """Минимальный HTTP-сервер с /healthz, чтобы контейнер всегда проходил
    healthcheck, даже когда веб-плеер и webhook отключены."""
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app_runner = web.AppRunner(app)
    await app_runner.setup()
    site = web.TCPSite(
        app_runner, host=config.API_HOST, port=config.API_PORT
    )
    await site.start()
    logger.info("Healthcheck HTTP-сервер запущен: %s:%s",
                config.API_HOST, config.API_PORT)
    return app_runner


async def _graceful_shutdown(bot: Bot, runner, redis_client) -> None:
    """Остановка после SIGINT/SIGTERM (ТЗ v3.0, п. 3.1): webhook -> HTTP-сервер
    -> Redis -> пул PostgreSQL. Сбой одного закрытия не отменяет остальные."""
    set_bot_ready(False)
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e:
        logger.warning("Не удалось удалить webhook при остановке: %s", e)

    if runner is not None:
        try:
            await runner.cleanup()
        except Exception as e:
            logger.warning("Не удалось остановить HTTP-сервер: %s", e)

    if redis_client is not None:
        try:
            closer = getattr(redis_client, "aclose", None) or redis_client.close
            await closer()
            logger.info("Соединение с Redis закрыто")
        except Exception as e:
            logger.warning("Не удалось закрыть Redis: %s", e)

    try:
        await engine.dispose()
        logger.info("Пул PostgreSQL закрыт")
    except Exception as e:
        logger.warning("Не удалось закрыть пул PostgreSQL: %s", e)


def _setup_monitoring() -> tuple[asyncio.Queue[str], TelegramLogHandler] | None:
    if not config.MONITOR_BOT_TOKEN and not config.MONITOR_CHAT_ID:
        return None
    if not config.MONITOR_BOT_TOKEN:
        logger.error(
            "Мониторинг логов отключён: задайте MONITOR_BOT_TOKEN"
        )
        return None
    if config.MONITOR_BOT_TOKEN == config.BOT_TOKEN:
        logger.error(
            "MONITOR_BOT_TOKEN должен отличаться от BOT_TOKEN, "
            "иначе polling двух ботов конфликтует"
        )
        return None

    log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUED_LOGS)
    log_handler = TelegramLogHandler(asyncio.get_running_loop(), log_queue)
    logging.getLogger().addHandler(log_handler)
    return log_queue, log_handler


def _start_monitoring(
    monitor_config: tuple[asyncio.Queue[str], TelegramLogHandler] | None,
) -> asyncio.Task | None:
    if monitor_config is None:
        return None
    log_queue, log_handler = monitor_config
    logger.info("Мониторинг логов и состояния сервиса запущен")
    return asyncio.create_task(
        run_monitoring_bot(
            token=config.MONITOR_BOT_TOKEN,
            chat_id=config.MONITOR_CHAT_ID,
            health_url=f"http://127.0.0.1:{config.API_PORT}/healthz",
            interval_seconds=config.MONITOR_INTERVAL_SECONDS,
            log_queue=log_queue,
            log_handler=log_handler,
        ),
        name="telegram-log-monitor",
    )


async def _stop_monitoring(
    monitor_task: asyncio.Task | None,
    monitor_config: tuple[asyncio.Queue[str], TelegramLogHandler] | None,
) -> None:
    if monitor_config is not None:
        logging.getLogger().removeHandler(monitor_config[1])
    if monitor_task is not None:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)


async def main() -> None:
    _setup_logging()
    monitor_config = _setup_monitoring()
    monitor_task = _start_monitoring(monitor_config)
    logger.info("=== musicbot запускается ===")

    # Graceful shutdown: SIGINT/SIGTERM вежливо останавливает бота
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    os.makedirs("data", exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    await init_db()

    # Этап 1 (ТЗ): возрастная ротация локального кэша и Lifecycle Rule бакета.
    # Обе операции отказоустойчивы — ошибка S3/диска не должна ронять старт.
    await asyncio.to_thread(downloader.prune_cache)
    if storage.enabled:
        await asyncio.to_thread(storage.setup_lifecycle_policy)

    bot = Bot(token=config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(RetryOnNetworkError())
    dp, redis_client = await _make_dispatcher()
    set_health_redis_client(redis_client)
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
        runner = await start_webapp(bot=bot)
        logger.info("WebApp HTTP-сервер включён: %s:%s", config.API_HOST, config.API_PORT)
    elif not config.WEBHOOK_ENABLED:
        # polling без веб-плеера: иначе healthcheck docker-контейнера не пройдёт
        runner = await _start_healthz_server()
    if config.API_ENABLED and config.WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🎵 Плеер",
                                         web_app=WebAppInfo(url=bust_url(config.WEBAPP_URL))))
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())

    # Webhook или Polling режим
    if config.WEBHOOK_ENABLED:
        if not config.WEBHOOK_URL:
            logger.error("WEBHOOK_ENABLED=true, но WEBHOOK_URL не задан!")
            raise ValueError("WEBHOOK_URL обязателен при WEBHOOK_ENABLED=true")

        webhook_url = f"{config.WEBHOOK_URL}{config.WEBHOOK_PATH}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET if config.WEBHOOK_SECRET else None,
            drop_pending_updates=True,
        )
        logger.info("Webhook установлен: %s", webhook_url)

        # Если API не включен, поднимаем aiohttp сервер только для webhook
        if not config.API_ENABLED:
            from aiohttp import web
            app = web.Application()
            app.router.add_get("/healthz", healthz)  # healthcheck контейнера
            webhook_handler = BoundedWebhookRequestHandler(
                dispatcher=dp,
                bot=bot,
                secret_token=config.WEBHOOK_SECRET if config.WEBHOOK_SECRET else None,
                max_concurrent_updates=config.MAX_CONCURRENT_UPDATES,
            )
            webhook_handler.register(app, path=config.WEBHOOK_PATH)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host=config.API_HOST, port=config.API_PORT)
            await site.start()
            logger.info("Webhook HTTP-сервер запущен: %s:%s", config.API_HOST, config.API_PORT)

        set_bot_ready(True)
        logger.info("Бот запущен в webhook режиме")
        try:
            # Держим процесс до SIGINT/SIGTERM
            await stop_event.wait()
        finally:
            await _stop_monitoring(monitor_task, monitor_config)
            await _graceful_shutdown(bot, runner, redis_client)
    else:
        # Polling режим
        await bot.delete_webhook(drop_pending_updates=True)
        set_bot_ready(True)
        logger.info("Бот запущен в polling режиме")
        try:
            await _poll_until_stop(dp, bot, stop_event)
        finally:
            await _stop_monitoring(monitor_task, monitor_config)
            await _graceful_shutdown(bot, runner, redis_client)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем")
    except Exception:
        logger.exception("Фатальная ошибка при старте/работе бота")
        raise
