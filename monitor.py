import asyncio
import logging
import re
import threading
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from config import config

logger = logging.getLogger("musicbot.monitor")
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(bot_token|monitor_bot_token|webhook_secret|"
    r"spotify_client_secret|yandex_token|s3_secret_access_key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)")
MAX_QUEUED_LOGS = 1000
_MAX_TELEGRAM_TEXT = 3500


class MonitorTarget:
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id.strip() or None
        self.ready = asyncio.Event()
        if self.chat_id is not None:
            self.ready.set()

    def authorize_or_claim(self, chat_id: int) -> bool:
        requested_chat = str(chat_id)
        if self.chat_id is None:
            self.chat_id = requested_chat
            self.ready.set()
        return self.chat_id == requested_chat

    async def wait_for_chat(self) -> str:
        await self.ready.wait()
        if self.chat_id is None:
            raise RuntimeError("Monitor chat was not registered")
        return self.chat_id


def create_monitor_router(target: MonitorTarget, health_url: str) -> Router:
    router = Router(name="monitor_commands")

    def is_allowed(message: Message) -> bool:
        if target.chat_id == str(message.chat.id):
            return True
        logger.warning(
            "Ignoring monitoring command from an unconfigured chat"
        )
        return False

    @router.message(Command("start"))
    async def start_command(message: Message) -> None:
        if not target.authorize_or_claim(message.chat.id):
            logger.warning(
                "Ignoring monitoring command from an unconfigured chat"
            )
            return
        await message.answer(
            "Этот чат подключён для мониторинга MusicBot.\n"
            "Команда /status показывает состояние основного бота."
        )

    @router.message(Command("status"), is_allowed)
    async def status_command(message: Message) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            healthy, detail = await _check_health(session, health_url)
        state = "работает" if healthy else "не отвечает"
        await message.answer(f"Основной бот {state}. Проверка: {detail}")

    return router


async def _run_monitor_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    retry_delay = 1
    while True:
        try:
            await dispatcher.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                tasks_concurrency_limit=16,
            )
            logger.warning("Monitoring bot polling stopped; restarting it")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitoring bot polling failed; restarting it")
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)


def _redact_secrets(message: str) -> str:
    message = _TELEGRAM_TOKEN.sub("[REDACTED_TELEGRAM_TOKEN]", message)
    message = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", message)
    message = _URL_PASSWORD.sub(r"\1[REDACTED]\2", message)
    for secret in (
        config.BOT_TOKEN,
        config.MONITOR_BOT_TOKEN,
        config.WEBHOOK_SECRET,
        config.SPOTIFY_CLIENT_SECRET,
        config.YANDEX_TOKEN,
        config.S3_SECRET_ACCESS_KEY,
        config.S3_SECONDARY_SECRET_ACCESS_KEY,
    ):
        if secret and len(secret) >= 6:
            message = message.replace(secret, "[REDACTED]")
    return message


class TelegramLogHandler(logging.Handler):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        log_queue: asyncio.Queue[str],
    ) -> None:
        super().__init__(level=logging.WARNING)
        self._loop = loop
        self._queue = log_queue
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("musicbot.monitor"):
            return
        try:
            message = _redact_secrets(self.format(record))
            if len(message) > _MAX_TELEGRAM_TEXT:
                message = message[:_MAX_TELEGRAM_TEXT - 32] + "\n...[truncated]"
            self._loop.call_soon_threadsafe(self._enqueue, message)
        except RuntimeError:
            with self._dropped_lock:
                self._dropped += 1

    def _enqueue(self, message: str) -> None:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            with self._dropped_lock:
                self._dropped += 1

    def take_dropped_count(self) -> int:
        with self._dropped_lock:
            dropped = self._dropped
            self._dropped = 0
        return dropped


async def _run_log_forwarder(
    session: aiohttp.ClientSession,
    token: str,
    target: MonitorTarget,
    log_queue: asyncio.Queue[str],
    log_handler: TelegramLogHandler,
) -> None:
    chat_id = await target.wait_for_chat()
    pending_message = ""
    while True:
        if pending_message:
            first_message = pending_message
            pending_message = ""
        else:
            try:
                first_message = await asyncio.wait_for(log_queue.get(), timeout=2)
            except asyncio.TimeoutError:
                first_message = ""

        messages = [first_message] if first_message else []
        total_length = len(first_message)
        while True:
            try:
                next_message = log_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if total_length + len(next_message) + 1 > _MAX_TELEGRAM_TEXT:
                pending_message = next_message
                break
            messages.append(next_message)
            total_length += len(next_message) + 1

        dropped = log_handler.take_dropped_count()
        if dropped:
            messages.append(f"[monitor] dropped {dropped} log messages (queue full)")
        if messages:
            await _send_notification(
                session,
                token,
                chat_id,
                "⚠️ MusicBot logs:\n" + "\n".join(messages),
            )
            await asyncio.sleep(3)


async def _run_health_monitor(
    session: aiohttp.ClientSession,
    token: str,
    target: MonitorTarget,
    health_url: str,
    interval_seconds: int,
) -> None:
    chat_id = await target.wait_for_chat()
    await _send_notification(
        session,
        token,
        chat_id,
        "MusicBot: мониторинг запущен. Пересылаются логи WARNING и ERROR.",
    )
    notified_state: bool | None = None
    consecutive_failures = 0
    while True:
        healthy, detail = await _check_health(session, health_url)
        if healthy:
            consecutive_failures = 0
            if notified_state is None:
                notified_state = True
            should_notify = notified_state is False
        else:
            consecutive_failures += 1
            should_notify = consecutive_failures >= 2 and notified_state is not False

        if should_notify:
            state = "восстановлен" if healthy else "недоступен"
            message = (
                f"MusicBot: сервис {state}.\n"
                f"Проверка: {detail}\n"
                f"Время UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
            )
            if await _send_notification(session, token, chat_id, message):
                notified_state = healthy
                logger.info("Monitored service state changed: %s", state)

        await asyncio.sleep(interval_seconds)


async def _check_health(
    session: aiohttp.ClientSession, url: str
) -> tuple[bool, str]:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return False, f"health endpoint returned HTTP {response.status}"
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                return False, "health endpoint reports an unhealthy service"
            return True, "healthy"
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return False, "health endpoint is unreachable or returned invalid data"


async def _send_notification(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: str,
    message: str,
) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(3):
        try:
            async with session.post(
                url,
                json={"chat_id": chat_id, "text": message},
            ) as response:
                if response.status == 200:
                    payload = await response.json(content_type=None)
                    if isinstance(payload, dict) and payload.get("ok"):
                        return True
                    logger.error("Monitoring notification was rejected by Telegram")
                    return False
                if response.status == 429 and attempt < 2:
                    payload = await response.json(content_type=None)
                    retry_after = 1
                    if isinstance(payload, dict):
                        parameters = payload.get("parameters")
                        if isinstance(parameters, dict):
                            retry_after = parameters.get("retry_after", 1)
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 1
                    await asyncio.sleep(min(max(delay, 1), 30))
                    continue
                if response.status >= 500 and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                logger.error(
                    "Monitoring notification rejected (HTTP %s)",
                    response.status,
                )
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            if attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            logger.error("Could not deliver monitoring notification")
            return False
    logger.error("Could not deliver monitoring notification after retries")
    return False


async def run_monitor(
    token: str,
    target: MonitorTarget,
    health_url: str,
    interval_seconds: int,
    log_queue: asyncio.Queue[str],
    log_handler: TelegramLogHandler,
) -> None:
    retry_delay = 1
    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await asyncio.gather(
                    _run_health_monitor(
                        session, token, target, health_url, interval_seconds
                    ),
                    _run_log_forwarder(
                        session, token, target, log_queue, log_handler
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitoring task failed; restarting it")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


async def run_monitoring_bot(
    token: str,
    chat_id: str,
    health_url: str,
    interval_seconds: int,
    log_queue: asyncio.Queue[str],
    log_handler: TelegramLogHandler,
) -> None:
    retry_delay = 1
    target = MonitorTarget(chat_id)
    while True:
        bot = Bot(token=token)
        try:
            dispatcher = Dispatcher()
            dispatcher.include_router(create_monitor_router(target, health_url))
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Информация о мониторинге"),
                    BotCommand(command="status", description="Состояние основного бота"),
                ]
            )
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(
                    run_monitor(
                        token=token,
                        target=target,
                        health_url=health_url,
                        interval_seconds=interval_seconds,
                        log_queue=log_queue,
                        log_handler=log_handler,
                    )
                )
                task_group.create_task(_run_monitor_polling(bot, dispatcher))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitoring bot failed; restarting it")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
        finally:
            await bot.session.close()
