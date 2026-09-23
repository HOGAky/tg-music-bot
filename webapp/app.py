import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

from aiohttp import web

from config import config
from database import repo
from database.session import SessionFactory
from services import downloader

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
INIT_DATA_TTL = 24 * 3600


def _check_init_data(init_data: str) -> int | None:
    """Проверка подписи Telegram Mini App -> telegram user_id или None."""
    if not init_data:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > INIT_DATA_TTL:
            return None
    except ValueError:
        return None
    try:
        return int(json.loads(pairs.get("user", ""))["id"])
    except (ValueError, KeyError, TypeError):
        return None


def _user_id(request: web.Request) -> int:
    user_id = _check_init_data(request.query.get("initData", ""))
    if user_id is None:
        raise web.HTTPUnauthorized(text="invalid initData")
    return user_id


def _track_json(track) -> dict:
    return {"id": track.id, "title": track.title,
            "artist": track.artist, "duration": track.duration}


async def library(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionFactory() as session:
        favorites = await repo.get_favorites(session, user_id)
        playlists = await repo.get_playlists(session, user_id)
        data = {
            "favorites": [_track_json(t) for t in favorites],
            "playlists": [{"id": p.id, "name": p.name,
                           "tracks": [_track_json(t) for t in p.tracks]} for p in playlists],
        }
    return web.json_response(data)


async def stream(request: web.Request) -> web.StreamResponse:
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        user = await repo.get_user(session, user_id)
        quality = user.quality if user is not None else 192
        url = track.download_url or track.source_url

    try:
        result = await downloader.download(url, quality=quality)
    except Exception:
        logger.exception("Не удалось подготовить трек %s для плеера", track_id)
        raise web.HTTPBadGateway(text="download failed")

    downloader.prune_cache()
    return web.FileResponse(result.path, headers={"Content-Type": "audio/mpeg"})


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/library", library)
    app.router.add_get(r"/api/stream/{track_id:\d+}", stream)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    return app


async def start_webapp() -> web.AppRunner:
    runner = web.AppRunner(create_app())
    await runner.setup()
    site = web.TCPSite(runner, config.API_HOST, config.API_PORT)
    await site.start()
    logger.info("Mini App слушает %s:%s", config.API_HOST, config.API_PORT)
    return runner
