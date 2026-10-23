import asyncio
import json
import unittest
from types import SimpleNamespace

from aiogram import Dispatcher
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot import BoundedWebhookRequestHandler


class WebhookCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_503_when_update_capacity_is_full(self):
        started = asyncio.Event()
        release = asyncio.Event()
        fake_bot = SimpleNamespace(
            session=SimpleNamespace(
                json_loads=json.loads,
                json_dumps=json.dumps,
                close=self._close_session,
            )
        )
        handler = BoundedWebhookRequestHandler(
            dispatcher=Dispatcher(),
            bot=fake_bot,
            secret_token="test-secret",
            max_concurrent_updates=1,
        )

        async def hold_update(bot, update):
            started.set()
            await release.wait()

        handler._process_update = hold_update
        app = web.Application()
        handler.register(app, path="/webhook")
        async with TestClient(TestServer(app)) as client:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
            first = await client.post("/webhook", headers=headers, json={"update_id": 1})
            await started.wait()
            second = await client.post("/webhook", headers=headers, json={"update_id": 2})

            self.assertEqual(first.status, 200)
            self.assertEqual(second.status, 503)

            release.set()
            await asyncio.gather(
                *handler._background_feed_update_tasks, return_exceptions=True
            )

    async def _close_session(self):
        return None


if __name__ == "__main__":
    unittest.main()
