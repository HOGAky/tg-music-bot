import unittest
from unittest.mock import patch

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

import health


class FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement):
        return 1


class FakeEngine:
    def __init__(self, error=None):
        self.error = error

    def connect(self):
        if self.error:
            raise self.error
        return FakeConnection()


class FakeRedis:
    def __init__(self, error=None):
        self.error = error

    async def ping(self):
        if self.error:
            raise self.error
        return True


class HealthCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        health.set_bot_ready(False)
        health.set_health_redis_client(None)

    async def test_reports_starting_before_bot_is_ready(self):
        response = await health.healthz(None)

        self.assertEqual(response.status, 503)
        self.assertIn('"status": "unhealthy"', response.text)

    async def test_reports_healthy_database_and_redis(self):
        health.set_bot_ready(True)
        health.set_health_redis_client(FakeRedis())
        with patch.object(health, "engine", FakeEngine()):
            response = await health.healthz(None)

        self.assertEqual(response.status, 200)
        self.assertIn('"database": "ok"', response.text)
        self.assertIn('"redis": "ok"', response.text)

    async def test_reports_database_failure(self):
        health.set_bot_ready(True)
        with patch.object(health, "engine", FakeEngine(SQLAlchemyError())):
            response = await health.healthz(None)

        self.assertEqual(response.status, 503)
        self.assertIn('"database": "unavailable"', response.text)

    async def test_reports_redis_failure(self):
        health.set_bot_ready(True)
        health.set_health_redis_client(FakeRedis(RedisError()))
        with patch.object(health, "engine", FakeEngine()):
            response = await health.healthz(None)

        self.assertEqual(response.status, 503)
        self.assertIn('"redis": "unavailable"', response.text)


if __name__ == "__main__":
    unittest.main()
