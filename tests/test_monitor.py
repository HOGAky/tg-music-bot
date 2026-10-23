import asyncio
import logging
import unittest
from unittest.mock import patch

from monitor import (
    MonitorTarget,
    TelegramLogHandler,
    _check_health,
    _redact_secrets,
)


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url):
        return self.response


class MonitorHealthTests(unittest.IsolatedAsyncioTestCase):
    def test_redacts_secret_assignments_and_url_passwords(self):
        message = _redact_secrets(
            "BOT_TOKEN=secret-value URL=postgres://user:password@database/app"
        )

        self.assertNotIn("secret-value", message)
        self.assertNotIn("password@", message)
        self.assertIn("BOT_TOKEN=[REDACTED]", message)

    async def test_accepts_healthy_endpoint(self):
        result = await _check_health(
            FakeSession(FakeResponse(200, {"status": "ok"})),
            "http://musicbot/healthz",
        )

        self.assertEqual(result, (True, "healthy"))

    async def test_rejects_unhealthy_endpoint(self):
        result = await _check_health(
            FakeSession(FakeResponse(503, {"status": "unhealthy"})),
            "http://musicbot/healthz",
        )

        self.assertEqual(result, (False, "health endpoint returned HTTP 503"))

    async def test_rejects_invalid_payload(self):
        result = await _check_health(
            FakeSession(FakeResponse(200, ["unexpected"])),
            "http://musicbot/healthz",
        )

        self.assertFalse(result[0])

    async def test_forwards_warnings_and_redacts_telegram_tokens(self):
        queue = asyncio.Queue(maxsize=2)
        handler = TelegramLogHandler(asyncio.get_running_loop(), queue)
        logger = logging.getLogger("monitor-log-test")
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            logger.warning(
                "request failed for bot token %s",
                "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            )
            await asyncio.sleep(0)

            message = queue.get_nowait()
            self.assertIn("[REDACTED_TELEGRAM_TOKEN]", message)
            self.assertNotIn(
                "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", message
            )
        finally:
            logger.removeHandler(handler)
            handler.close()

    async def test_reports_logs_dropped_from_full_queue(self):
        queue = asyncio.Queue(maxsize=1)
        handler = TelegramLogHandler(asyncio.get_running_loop(), queue)
        logger = logging.getLogger("monitor-overflow-test")
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            logger.error("first")
            logger.error("second")
            await asyncio.sleep(0)

            self.assertEqual(handler.take_dropped_count(), 1)
            self.assertIn("first", queue.get_nowait())
        finally:
            logger.removeHandler(handler)
            handler.close()


class MonitorTargetTests(unittest.TestCase):
    def test_first_start_chat_is_claimed_and_other_chats_are_rejected(self):
        target = MonitorTarget("")

        self.assertTrue(target.authorize_or_claim(123))
        self.assertEqual(target.chat_id, "123")
        self.assertTrue(target.authorize_or_claim(123))
        self.assertFalse(target.authorize_or_claim(456))

    def test_configured_chat_cannot_be_replaced(self):
        target = MonitorTarget("123")

        self.assertTrue(target.authorize_or_claim(123))
        self.assertFalse(target.authorize_or_claim(456))
        self.assertEqual(target.chat_id, "123")


class MonitorStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_task_starts_with_main_bot_lifecycle(self):
        import bot

        started = asyncio.Event()

        async def fake_run_monitoring_bot(**kwargs):
            started.set()
            await asyncio.Event().wait()

        with (
            patch.object(bot.config, "MONITOR_BOT_TOKEN", "test-token"),
            patch.object(bot.config, "MONITOR_CHAT_ID", ""),
            patch.object(bot.config, "BOT_TOKEN", "main-token"),
            patch.object(bot, "run_monitoring_bot", fake_run_monitoring_bot),
        ):
            monitor_config = bot._setup_monitoring()
            self.assertIsNotNone(monitor_config)
            task = bot._start_monitoring(monitor_config)
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                self.assertFalse(task.done())
            finally:
                await bot._stop_monitoring(task, monitor_config)

    async def test_monitor_task_starts_without_configured_chat_id(self):
        import bot

        with (
            patch.object(bot.config, "MONITOR_BOT_TOKEN", "test-token"),
            patch.object(bot.config, "MONITOR_CHAT_ID", ""),
            patch.object(bot.config, "BOT_TOKEN", "main-token"),
        ):
            monitor_config = bot._setup_monitoring()
            self.assertIsNotNone(monitor_config)
            await bot._stop_monitoring(None, monitor_config)

    async def test_monitoring_process_runs_commands_and_log_sender_together(self):
        import monitor

        polling_started = asyncio.Event()
        logs_started = asyncio.Event()
        closed = asyncio.Event()
        monitor_bot = unittest.mock.AsyncMock()
        monitor_bot.session.close.side_effect = closed.set

        class FakeDispatcher:
            def include_router(self, router):
                self.router = router

        async def fake_run_monitor(**kwargs):
            logs_started.set()
            await asyncio.Event().wait()

        async def fake_start_polling(*args, **kwargs):
            polling_started.set()
            await asyncio.Event().wait()

        with (
            patch.object(monitor, "Bot", return_value=monitor_bot),
            patch.object(monitor, "Dispatcher", FakeDispatcher),
            patch.object(monitor, "run_monitor", fake_run_monitor),
            patch.object(
                monitor.Dispatcher,
                "start_polling",
                fake_start_polling,
                create=True,
            ),
        ):
            log_queue = asyncio.Queue(maxsize=2)
            log_handler = TelegramLogHandler(
                asyncio.get_running_loop(), log_queue
            )
            task = asyncio.create_task(
                monitor.run_monitoring_bot(
                    token="test-token",
                    chat_id="",
                    health_url="http://localhost/healthz",
                    interval_seconds=30,
                    log_queue=log_queue,
                    log_handler=log_handler,
                )
            )
            try:
                await asyncio.wait_for(polling_started.wait(), timeout=1)
                await asyncio.wait_for(logs_started.wait(), timeout=1)
                monitor_bot.set_my_commands.assert_awaited_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                log_handler.close()

        self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()
