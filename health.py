import logging

from aiohttp import web
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.session import engine

logger = logging.getLogger(__name__)

_bot_ready = False
_redis_client: Redis | None = None


def set_bot_ready(ready: bool) -> None:
    global _bot_ready
    _bot_ready = ready


def set_health_redis_client(client: Redis | None) -> None:
    global _redis_client
    _redis_client = client


async def healthz(_: web.Request) -> web.Response:
    checks = {"bot": "ok" if _bot_ready else "starting"}
    if not _bot_ready:
        return web.json_response(
            {"status": "unhealthy", "checks": checks}, status=503
        )

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        logger.exception("Health check failed: database is unavailable")
        checks["database"] = "unavailable"

    if _redis_client is not None:
        try:
            await _redis_client.ping()
            checks["redis"] = "ok"
        except RedisError:
            logger.exception("Health check failed: Redis is unavailable")
            checks["redis"] = "unavailable"

    healthy = all(value == "ok" for value in checks.values())
    return web.json_response(
        {"status": "ok" if healthy else "unhealthy", "checks": checks},
        status=200 if healthy else 503,
    )
