import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import parse_qsl, quote

import aiohttp
from aiohttp import web

from config import config
from database import repo
from database.models import Track
from database.session import SessionFactory
from services import downloader, radio, searcher

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
INIT_DATA_TTL = 24 * 3600

# ---------- отдача zip-архивов больших плейлистов ----------
BATCH_ZIP_TTL = 24 * 3600
_batch_zips: dict[str, dict] = {}  # key -> {"path", "name", "ts"}


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


async def _prepare_track(track: Track, quality: int) -> str:
    """Файл mp3 для трека: YouTube, а при age-restricted — SoundCloud."""
    url = track.download_url or track.source_url
    try:
        result = await downloader.download(url, quality=quality)
    except Exception as e:
        if "Sign in to confirm" not in str(e):
            raise
        result = await downloader.download_soundcloud_fallback(track, quality)
    return result.path


async def stream(request: web.Request) -> web.StreamResponse:
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        user = await repo.get_user(session, user_id)
        quality = user.quality if user is not None else 192

    try:
        path = await _prepare_track(track, quality)
    except Exception:
        logger.exception("Не удалось подготовить трек %s для плеера", track_id)
        raise web.HTTPBadGateway(text="download failed")

    downloader.prune_cache()
    return web.FileResponse(path, headers={"Content-Type": "audio/mpeg"})


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _playlist_json(pl) -> dict:
    return {"id": pl.id, "name": pl.name, "tracks": [_track_json(t) for t in pl.tracks]}


async def search_api(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    q = (request.query.get("q") or "").strip()
    if not q:
        return web.json_response({"results": []})
    found = await searcher.search(q, limit=15)
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id, None)
        tracks = [await repo.upsert_track(session, source_url=f.source_url, title=f.title,
                                          artist=f.artist, duration=f.duration,
                                          download_url=f.download_url) for f in found]
        tracks = [t for t in tracks if t is not None]
        favorites = {t.id for t in await repo.get_favorites(session, user_id, limit=500)}
    out = [_track_json(t) | {"favorite": t.id in favorites} for t in tracks]
    return web.json_response({"results": out})


async def radio_api(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
    found = await radio.similar_tracks(track.source_url, track.artist, track.title, limit=10)
    async with SessionFactory() as session:
        tracks = [await repo.upsert_track(session, source_url=f.source_url, title=f.title,
                                          artist=f.artist, duration=f.duration,
                                          download_url=f.download_url) for f in found]
        tracks = [t for t in tracks if t is not None and t.id != track_id]
    return web.json_response({"results": [_track_json(t) for t in tracks]})


async def download_api(request: web.Request) -> web.FileResponse:
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        user = await repo.get_user(session, user_id)
        quality = user.quality if user is not None else 192
    try:
        path = await _prepare_track(track, quality)
    except Exception:
        logger.exception("Не удалось отдать файл трека %s", track_id)
        raise web.HTTPBadGateway(text="download failed")
    safe = re.sub(r"[^\w\s.\-]", "", f"{track.artist} {track.title}").strip() or f"track_{track_id}"
    utf8 = quote(f"{track.artist} - {track.title}.mp3")
    return web.FileResponse(path, headers={
        "Content-Type": "audio/mpeg",
        "Content-Disposition": f'attachment; filename="{safe[:64]}.mp3"; filename*=UTF-8\'\'{utf8}',
    })


async def history_api(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionFactory() as session:
        tracks = await repo.recent_tracks(session, user_id, limit=15)
    return web.json_response({"recent": [_track_json(t) for t in tracks]})


async def favorites_toggle(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    body = await _json_body(request)
    track_id = int(body.get("track_id") or 0)
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        if await repo.is_favorite(session, user_id, track_id):
            await repo.remove_favorite(session, user_id, track_id)
            fav = False
        else:
            await repo.add_favorite(session, user_id, track_id)
            fav = True
    return web.json_response({"favorite": fav})


async def playlist_create(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    name = (await _json_body(request)).get("name", "").strip()
    if not name:
        raise web.HTTPBadRequest(text="name required")
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id, None)
        pl = await repo.create_playlist(session, user_id, name[:64])
        out = _playlist_json(pl)
    return web.json_response(out)


def _own_playlist(pl, user_id: int) -> None:
    if pl is None or pl.user_id != user_id:
        raise web.HTTPForbidden(text="not your playlist")


async def playlist_rename(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    name = (await _json_body(request)).get("name", "").strip()
    if not name:
        raise web.HTTPBadRequest(text="name required")
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        ok = await repo.rename_playlist(session, user_id, pl_id, name)
    return web.json_response({"ok": ok})


async def playlist_delete(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        ok = await repo.delete_playlist(session, user_id, pl_id)
    return web.json_response({"ok": ok})


async def playlist_add(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    track_id = int((await _json_body(request)).get("track_id") or 0)
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        added = await repo.add_track_to_playlist(session, pl_id, track_id)
    return web.json_response({"added": added})


async def playlist_remove(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    track_id = int((await _json_body(request)).get("track_id") or 0)
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        await repo.remove_track_from_playlist(session, pl_id, track_id)
    return web.json_response({"ok": True})


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def _batch_sig(key: str) -> str:
    return hmac.new(config.BOT_TOKEN.encode(), f"batch-zip:{key}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def register_batch_zip(key: str, path: str, name: str) -> None:
    """Регистрирует собранный архив для скачивания по ссылке; чистит протухшие."""
    now = time.time()
    for k, info in list(_batch_zips.items()):
        if now - info["ts"] > BATCH_ZIP_TTL:
            try:
                os.remove(info["path"])
            except OSError:
                pass
            _batch_zips.pop(k, None)
    _batch_zips[key] = {"path": path, "name": name, "ts": now}


def batch_zip_url(key: str) -> str | None:
    if not config.WEBAPP_URL:
        return None
    base = config.WEBAPP_URL.split("/app/")[0]
    return f"{base}/api/batch/{key}?sig={_batch_sig(key)}"


async def batch_zip(request: web.Request) -> web.FileResponse:
    key = request.match_info["key"]
    if request.query.get("sig") != _batch_sig(key):
        raise web.HTTPUnauthorized(text="bad signature")
    info = _batch_zips.get(key)
    if info is None or not os.path.exists(info["path"]):
        raise web.HTTPNotFound(text="archive expired or not ready")
    info["ts"] = time.time()
    return web.FileResponse(info["path"], headers={
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{info["name"]}"',
    })


async def tunnel_alive() -> bool:
    """Доступен ли веб-сервер снаружи (через публичный адрес из WEBAPP_URL)."""
    if not (config.API_ENABLED and config.WEBAPP_URL):
        return False
    base = config.WEBAPP_URL.split("/app/")[0]
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(f"{base}/healthz") as r:
                return r.status == 200
    except Exception:
        return False


async def index(_: web.Request) -> web.FileResponse:
    # no-store: webview Telegram не должен показывать устаревшую страницу
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"),
                            headers={"Cache-Control": "no-store"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/library", library)
    app.router.add_get(r"/api/stream/{track_id:\d+}", stream)
    app.router.add_get(r"/api/download/{track_id:\d+}", download_api)
    app.router.add_get("/api/search", search_api)
    app.router.add_get(r"/api/radio/{track_id:\d+}", radio_api)
    app.router.add_get("/api/history", history_api)
    app.router.add_post("/api/favorites/toggle", favorites_toggle)
    app.router.add_post("/api/playlist/create", playlist_create)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/rename", playlist_rename)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/delete", playlist_delete)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/add", playlist_add)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/remove", playlist_remove)
    app.router.add_get(r"/api/batch/{key:[A-Za-z0-9]+}", batch_zip)
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
