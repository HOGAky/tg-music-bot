"""aiohttp-приложение Mini App.

- Статика плеера: /app, /app/static/*
- REST API: /api/* (поиск, избранное, плейлисты, радио, тексты, топ, история)
- /api/stream/{id}: live-прокси Range-запросов к CDN трека (HTTP 206) БЕЗ записи
  файла на диск; при недоступности потока — фолбэк на скачанный mp3 из кэша
- /api/download/{id}: mp3-файлом; /api/send_tg/{id}: трек в чат пользователя
- /api/batch/{key}: zip-архивы больших плейлистов по подписанным ссылкам
"""
import asyncio
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import quote

import aiohttp
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from aiohttp import ContentTypeError, web

import keyboards
from config import config
from database import repo
from database.models import Track
from database.session import SessionFactory
from health import healthz
from services import downloader, player, radio, searcher, sender, streamer
from services import lyrics as lyrics_svc
from services.storage import storage
from webapp.auth import auth_middleware, extract_init_data, validate_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STREAM_CHUNK = 65536  # 64 КБ

# ---------- отдача zip-архивов больших плейлистов ----------
BATCH_ZIP_TTL = 24 * 3600
_batch_zips: dict[str, dict] = {}  # key -> {"path", "name", "ts"}
_CACHE_PRUNE_INTERVAL = 3600
_cache_pruned_at = time.monotonic()
_cache_prune_lock = asyncio.Lock()


async def _prune_cache_if_due() -> None:
    global _cache_pruned_at
    async with _cache_prune_lock:
        if time.monotonic() - _cache_pruned_at < _CACHE_PRUNE_INTERVAL:
            return
        await asyncio.to_thread(downloader.prune_cache)
        _cache_pruned_at = time.monotonic()


# ---------- авторизация ----------
def _optional_user_id(request: web.Request) -> int | None:
    """user_id если initData валиден (иначе None). Для публичных /api/stream."""
    if request.get("user_id") is not None:
        return request["user_id"]
    data = extract_init_data(request)
    user = validate_init_data(data, config.BOT_TOKEN) if data else None
    if user and "id" in user:
        request["user_id"] = int(user["id"])
        request["tg_user"] = user
        return request["user_id"]
    return None


def _user_id(request: web.Request) -> int:
    user_id = _optional_user_id(request)
    if user_id is None:
        raise web.HTTPUnauthorized(text="invalid initData")
    return user_id


# ---------- сериализация ----------
def _track_json(track: Track, in_fav: bool | None = None) -> dict:
    d = {"id": track.id, "title": track.title,
         "artist": track.artist, "duration": track.duration,
         "source_url": track.source_url}
    if in_fav is not None:
        d["in_fav"] = in_fav
    return d


async def _fav_ids(session, user_id: int) -> set[int]:
    return {t.id for t in await repo.get_favorites(session, user_id, limit=500)}


async def _upsert_found(session, found: list) -> list[Track]:
    tracks = [await repo.upsert_track(
        session, source_url=f.source_url, title=f.title, artist=f.artist,
        duration=f.duration, download_url=f.download_url) for f in found]
    return [t for t in tracks if t is not None]


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


# ---------- подготовка mp3 (для download/send_tg, не для live-стрима) ----------
async def _prepare_track(session, track: Track, quality: int):
    """DownloadResult для трека: YouTube, при age-restricted — SoundCloud."""
    url = track.download_url or track.source_url
    try:
        result = await downloader.download(url, quality=quality, cache_key=track.s3_key)
    except Exception as e:
        if "Sign in to confirm" not in str(e):
            raise
        result = await downloader.download_soundcloud_fallback(
            track, quality)
    if storage.enabled and result.cache_key and track.s3_key != result.cache_key:
        track.s3_key = result.cache_key
        await session.commit()
    return result


# ---------- live-стриминг (без записи на диск) ----------
async def _proxy_live(request: web.Request, media_url: str, hint: str) -> web.StreamResponse:
    """Пробрасывает Range клиента к CDN и отдаёт поток чанками.
    Протухшая CDN-ссылка (403/410/416) — переэкстракт и один повтор."""
    rng = request.headers.get("Range")
    http: aiohttp.ClientSession = request.app["http"]
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=180)

    for attempt in (0, 1):
        info = await streamer.get_live_stream_info(media_url, query_hint=hint)
        upstream_headers = dict(info.get("headers") or {})
        if rng:
            upstream_headers["Range"] = rng
        try:
            upstream = await http.get(info["stream_url"], headers=upstream_headers,
                                      timeout=timeout, allow_redirects=True)
        except (aiohttp.ClientError, TimeoutError) as e:
            if attempt == 0:
                streamer.drop_cache(media_url)
                continue
            raise web.HTTPBadGateway(text="upstream unreachable") from e

        if upstream.status in (403, 404, 410, 416) and attempt == 0:
            upstream.close()
            streamer.drop_cache(media_url)
            continue
        if upstream.status not in (200, 206):
            status = upstream.status
            upstream.close()
            raise web.HTTPBadGateway(text=f"upstream {status}")

        resp = web.StreamResponse(status=upstream.status)
        resp.headers["Content-Type"] = (
            upstream.headers.get("Content-Type") or info["mime_type"])
        for h in ("Content-Length", "Content-Range"):
            if upstream.headers.get(h):
                resp.headers[h] = upstream.headers[h]
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Cache-Control"] = "no-cache"
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_chunked(STREAM_CHUNK):
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionError, asyncio.CancelledError, aiohttp.ClientError):
            pass  # клиент <audio> отключился — нормально
        finally:
            upstream.release()
        return resp
    raise web.HTTPBadGateway(text="stream url expired")


def _serve_file_range(request: web.Request, path: str) -> web.StreamResponse:
    """Range-раздача локального mp3 (фолбэк, когда live-поток недоступен)."""
    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")
    if not range_header:
        return web.FileResponse(
            path,
            headers={"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes",
                     "Content-Length": str(file_size)})
    try:
        spec = range_header.strip().lower()
        if not spec.startswith("bytes="):
            raise ValueError("invalid unit")
        start_s, end_s = spec[6:].split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except (ValueError, IndexError):
        raise web.HTTPBadRequest(text="Invalid Range header")
    if start >= file_size or end >= file_size or start > end:
        raise web.HTTPRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{file_size}"})
    content_length = end - start + 1

    async def file_sender():
        f = await asyncio.to_thread(open, path, "rb")
        try:
            await asyncio.to_thread(f.seek, start)
            remaining = content_length
            while remaining > 0:
                chunk = await asyncio.to_thread(f.read, min(STREAM_CHUNK, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
        finally:
            await asyncio.to_thread(f.close)

    return web.Response(
        status=206, body=file_sender(),
        headers={"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes",
                 "Content-Range": f"bytes {start}-{end}/{file_size}",
                 "Content-Length": str(content_length),
                 "Cache-Control": "public, max-age=3600"})


async def stream(request: web.Request) -> web.StreamResponse:
    """GET /api/stream/{id}: мгновенное воспроизведение.

    1) presigned S3 (302), если трек уже в облачном кэше;
    2) live-прокси к CDN — файл на диск НЕ пишется;
    3) фолбэк: скачать mp3 и раздать файл с Range."""
    user_id = _optional_user_id(request)  # роут публичный для <audio>
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        media_url = await sender.ensure_download_url(session, track)
        if not media_url:
            media_url = track.source_url
        hint = f"{track.artist or ''} {track.title or ''}".strip()
        s3_key = track.s3_key
        quality = 192
        if user_id is not None:
            user = await repo.get_user(session, user_id)
            if user is not None and user.quality:
                quality = user.quality

    if s3_key and storage.enabled:
        presigned = await asyncio.to_thread(storage.get_public_url, s3_key, 300)
        if presigned:
            raise web.HTTPFound(presigned)

    try:
        return await _proxy_live(request, media_url, hint)
    except web.HTTPException:
        raise
    except Exception as e:
        logger.warning("Live-стрим трека %s недоступен (%s) — качаем файл", track_id, e)

    # фолбэк: файл из кэша/скачивание (медленнее, но надёжнее)
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        try:
            result = await _prepare_track(session, track, quality)
        except Exception:
            logger.exception("Не удалось подготовить трек %s для плеера", track_id)
            raise web.HTTPBadGateway(text="download failed")
    await _prune_cache_if_due()
    return _serve_file_range(request, result.path)


# ---------- скачивание / отправка / лог прослушивания ----------
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
            result = await _prepare_track(session, track, quality)
        except Exception:
            logger.exception("Не удалось отдать файл трека %s", track_id)
            raise web.HTTPBadGateway(text="download failed")
        artist, title = track.artist or "", track.title or ""
    safe = re.sub(r"[^\w\s.\-]", "", f"{artist} {title}".strip()) or f"track_{track_id}"
    utf8 = quote(f"{artist} - {title}.mp3" if artist else f"{title}.mp3")
    return web.FileResponse(result.path, headers={
        "Content-Type": "audio/mpeg",
        "Content-Disposition": f'attachment; filename="{safe[:64]}.mp3"; filename*=UTF-8\'\'{utf8}',
    })


async def send_tg_api(request: web.Request) -> web.Response:
    """Отправить трек аудио-сообщением в чат пользователя с ботом."""
    user_id = _user_id(request)
    bot = request.app.get("bot")
    if bot is None:
        raise web.HTTPServiceUnavailable(text="bot unavailable")
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        tg_user = request.get("tg_user") or {}
        user = await repo.get_or_create_user(session, user_id, tg_user.get("username"))
        quality = user.quality or 192
        protect = bool(user.protect_content)
        in_fav = await repo.is_favorite(session, user_id, track_id)
        markup = keyboards.track_kb(track_id, in_fav)
        title, performer = track.title or "", track.artist or "Unknown"

        # быстрый путь: трек уже на серверах Telegram
        if track.file_id:
            try:
                await bot.send_audio(
                    user_id, audio=track.file_id, title=title[:64],
                    performer=performer[:64], duration=int(track.duration or 0) or None,
                    reply_markup=markup, protect_content=protect)
                await repo.log_play(session, user_id, track.id)
                player.set_now_playing(user_id, track)
                return web.json_response({"ok": True, "cached": True})
            except TelegramAPIError:
                track.file_id = None
                await session.commit()

        try:
            result = await _prepare_track(session, track, quality)
        except Exception:
            logger.exception("send_tg: скачивание трека %s упало", track_id)
            raise web.HTTPBadGateway(text="download failed")

        if os.path.getsize(result.path) > 49 * 1024 * 1024:
            return web.json_response(
                {"ok": False, "error": "Трек длиннее лимита Telegram (50 МБ)"})

        try:
            msg = await bot.send_audio(
                user_id, audio=FSInputFile(result.path),
                thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
                title=title[:64], performer=performer[:64],
                duration=int(track.duration or result.duration or 0) or None,
                reply_markup=markup, protect_content=protect)
        except TelegramAPIError as e:
            logger.warning("send_tg: Telegram не принял трек %s: %s", track_id, e)
            raise web.HTTPBadGateway(text="telegram rejected")

        if msg.audio and msg.audio.file_id:
            await repo.set_file_id(session, track.id, msg.audio.file_id)
        await repo.log_play(session, user_id, track.id)
        player.set_now_playing(user_id, track)

    if result.local_disposable:
        for p in (result.path, result.thumbnail):
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
    return web.json_response({"ok": True})


async def play_api(request: web.Request) -> web.Response:
    """Лог факта воспроизведения + состояние /now."""
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        await repo.log_play(session, user_id, track.id)
        player.set_now_playing(user_id, track)
    return web.json_response({"ok": True})


# ---------- поиск / радио / тексты / топ / история ----------
async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except (ContentTypeError, json.JSONDecodeError):
        return {}


async def search_api(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    q = (request.query.get("q") or "").strip()
    if not q:
        return web.json_response({"tracks": []})
    try:
        found = await (searcher.resolve_url(q) if searcher.is_url(q)
                       else searcher.search(q, limit=15))
    except Exception as e:
        logger.warning("Поиск %r упал: %s", q, e)
        return web.json_response({"tracks": [], "error": "search failed"}, status=502)
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id,
                                      (request.get("tg_user") or {}).get("username"))
        tracks = await _upsert_found(session, found[:15])
        favorites = await _fav_ids(session, user_id)
    out = [_track_json(t, t.id in favorites) for t in tracks]
    return web.json_response({"tracks": out, "results": out})


async def radio_api(request: web.Request) -> web.Response:
    _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
    found = await radio.similar_tracks(track.source_url, track.artist, track.title, limit=10)
    async with SessionFactory() as session:
        tracks = await _upsert_found(session, found)
        tracks = [t for t in tracks if t.id != track_id]
    out = [_track_json(t) for t in tracks]
    return web.json_response({"tracks": out, "results": out})


async def lyrics_api(request: web.Request) -> web.Response:
    _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        artist, title, duration = track.artist, track.title, track.duration
    data = await lyrics_svc.fetch_lyrics_full(artist, title, duration)
    if not data.get("plain") and not data.get("synced"):
        return web.json_response({"plain": None, "lrc": None, "synced": None})
    return web.json_response(data)


async def history_api(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionFactory() as session:
        tracks = await repo.recent_tracks(session, user_id, limit=15)
    return web.json_response({"recent": [_track_json(t) for t in tracks]})


async def chart_api(request: web.Request) -> web.Response:
    """Глобальный топ треков бота по прослушиваниям."""
    _user_id(request)
    async with SessionFactory() as session:
        top = await repo.top_tracks(session, limit=15)
    return web.json_response({"chart": [
        _track_json(t) | {"plays": n} for t, n in top]})


async def top_api(request: web.Request) -> web.Response:
    """Топ бота + недавние прослушивания пользователя (вкладка «Топ»)."""
    user_id = _user_id(request)
    async with SessionFactory() as session:
        top = await repo.top_tracks(session, limit=15)
        recent = await repo.recent_tracks(session, user_id, limit=15)
        favorites = await _fav_ids(session, user_id)
    return web.json_response({
        "top": [_track_json(t, t.id in favorites) | {"plays": n} for t, n in top],
        "recent": [_track_json(t, t.id in favorites) for t in recent],
    })


async def artist_api(request: web.Request) -> web.Response:
    """Треки исполнителя: поиск по имени + фильтр по совпадению артиста."""
    _user_id(request)
    q = (request.query.get("q") or "").strip()
    if not q:
        return web.json_response({"results": [], "artist": q})
    found = await searcher.search(q, limit=20)
    ql = q.lower()
    picked = [f for f in found if ql in (f.artist or "").lower()][:15] or found[:10]
    async with SessionFactory() as session:
        tracks = await _upsert_found(session, picked)
    return web.json_response({"artist": q, "results": [_track_json(t) for t in tracks]})


# ---------- избранное ----------
async def favorites_list(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionFactory() as session:
        tracks = await repo.get_favorites(session, user_id, limit=500)
    return web.json_response({"tracks": [_track_json(t, True) for t in tracks]})


async def favorite_toggle_id(request: web.Request) -> web.Response:
    """POST /api/favorites/{track_id} — переключить избранное."""
    user_id = _user_id(request)
    track_id = int(request.match_info["track_id"])
    async with SessionFactory() as session:
        track = await repo.get_track(session, track_id)
        if track is None:
            raise web.HTTPNotFound(text="track not found")
        if await repo.is_favorite(session, user_id, track_id):
            await repo.remove_favorite(session, user_id, track_id)
            fav = False
        else:
            await repo.get_or_create_user(session, user_id, None)
            await repo.add_favorite(session, user_id, track_id)
            fav = True
    return web.json_response({"in_fav": fav, "favorite": fav})


async def favorites_toggle(request: web.Request) -> web.Response:
    """Legacy: POST /api/favorites/toggle {track_id}."""
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
    return web.json_response({"favorite": fav, "in_fav": fav})


# ---------- плейлисты ----------
def _playlist_json(pl) -> dict:
    return {"id": pl.id, "name": pl.name, "tracks": [_track_json(t) for t in pl.tracks]}


def _own_playlist(pl, user_id: int) -> None:
    if pl is None or pl.user_id != user_id:
        raise web.HTTPForbidden(text="not your playlist")


async def playlists_list(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionFactory() as session:
        pls = await repo.get_playlists(session, user_id)
        out = [{"id": p.id, "name": p.name, "count": len(p.tracks)} for p in pls]
    return web.json_response({"playlists": out})


async def playlist_detail(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    async with SessionFactory() as session:
        pl = await repo.get_playlist(session, pl_id)
        _own_playlist(pl, user_id)
        favorites = await _fav_ids(session, user_id)
        out = {"id": pl.id, "name": pl.name,
               "tracks": [_track_json(t, t.id in favorites) for t in pl.tracks]}
    return web.json_response(out)


async def playlist_create_rest(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    name = ((await _json_body(request)).get("name") or "").strip()
    if not name:
        raise web.HTTPBadRequest(text="name required")
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id, None)
        pl = await repo.create_playlist(session, user_id, name[:64])
        out = {"id": pl.id, "name": pl.name, "count": 0}
    return web.json_response(out)


async def playlist_delete_rest(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        ok = await repo.delete_playlist(session, user_id, pl_id)
    return web.json_response({"ok": ok})


async def playlist_track_add(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    track_id = int((await _json_body(request)).get("track_id") or 0)
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        added = await repo.add_track_to_playlist(session, pl_id, track_id)
    return web.json_response({"added": added})


async def playlist_track_remove(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    try:
        track_id = int(request.match_info["track_id"])
    except ValueError:
        raise web.HTTPNotFound(text="track not found")
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        await repo.remove_track_from_playlist(session, pl_id, track_id)
    return web.json_response({"ok": True})


# --- legacy-роуты старого фронта (совместимость) ---
async def playlist_create(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    name = (await _json_body(request)).get("name", "").strip()
    if not name:
        raise web.HTTPBadRequest(text="name required")
    async with SessionFactory() as session:
        await repo.get_or_create_user(session, user_id, None)
        pl = await repo.create_playlist(session, user_id, name[:64])
        # новый плейлист пуст; pl.tracks здесь трогать нельзя — ленивый LOAD в async-сессии
        out = {"id": pl.id, "name": pl.name, "tracks": []}
    return web.json_response(out)


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


async def playlist_move(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    pl_id = int(request.match_info["playlist_id"])
    body = await _json_body(request)
    track_id = int(body.get("track_id") or 0)
    delta = int(body.get("delta") or 0)
    async with SessionFactory() as session:
        _own_playlist(await repo.get_playlist(session, pl_id), user_id)
        ok = await repo.move_track_in_playlist(session, user_id, pl_id, track_id, delta)
    return web.json_response({"ok": ok})


# ---------- batch zip ----------
def _batch_sig(key: str) -> str:
    return hmac.new(config.BOT_TOKEN.encode(), f"batch-zip:{key}".encode(),
                    "sha256").hexdigest()[:32]


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
        async with aiohttp.ClientSession(timeout=timeout) as http, http.get(f"{base}/healthz") as r:
            return r.status == 200
    except (aiohttp.ClientError, TimeoutError):
        return False


# ---------- статика ----------
async def index(_: web.Request) -> web.FileResponse:
    # no-store: webview Telegram не должен показывать устаревшую страницу
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"),
                            headers={"Cache-Control": "no-store"})


async def static_file(request: web.Request) -> web.FileResponse:
    name = request.match_info["filename"]
    if "/" in name or ".." in name:
        raise web.HTTPNotFound()
    path = os.path.join(STATIC_DIR, name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def _http_session_ctx(app: web.Application):
    app["http"] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=180))
    yield
    await app["http"].close()


def create_app(bot=None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["bot"] = bot
    app.cleanup_ctx.append(_http_session_ctx)

    # плеер/статика/health
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_get(r"/app/static/{filename:[\w.\-]+}", static_file)

    # стриминг/файлы/действия
    app.router.add_get(r"/api/stream/{track_id:\d+}", stream)
    app.router.add_get(r"/api/download/{track_id:\d+}", download_api)
    app.router.add_post(r"/api/send_tg/{track_id:\d+}", send_tg_api)
    app.router.add_post(r"/api/play/{track_id:\d+}", play_api)

    # поиск/контент
    app.router.add_get("/api/search", search_api)
    app.router.add_get(r"/api/radio/{track_id:\d+}", radio_api)
    app.router.add_get(r"/api/lyrics/{track_id:\d+}", lyrics_api)
    app.router.add_get("/api/top", top_api)
    app.router.add_get("/api/history", history_api)
    app.router.add_get("/api/chart", chart_api)
    app.router.add_get("/api/artist", artist_api)
    app.router.add_get("/api/library", library)

    # избранное
    app.router.add_get("/api/favorites", favorites_list)
    app.router.add_post(r"/api/favorites/{track_id:\d+}", favorite_toggle_id)
    app.router.add_post("/api/favorites/toggle", favorites_toggle)

    # плейлисты REST
    app.router.add_get("/api/playlists", playlists_list)
    app.router.add_post("/api/playlists", playlist_create_rest)
    app.router.add_get(r"/api/playlists/{playlist_id:\d+}", playlist_detail)
    app.router.add_delete(r"/api/playlists/{playlist_id:\d+}", playlist_delete_rest)
    app.router.add_post(r"/api/playlists/{playlist_id:\d+}/tracks", playlist_track_add)
    app.router.add_delete(r"/api/playlists/{playlist_id:\d+}/tracks/{track_id:\d+}",
                        playlist_track_remove)

    # legacy-роуты старого фронта
    app.router.add_post("/api/playlist/create", playlist_create)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/rename", playlist_rename)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/delete", playlist_delete_rest)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/add", playlist_add)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/remove", playlist_remove)
    app.router.add_post(r"/api/playlist/{playlist_id:\d+}/move", playlist_move)

    # batch zip
    app.router.add_get(r"/api/batch/{key:[A-Za-z0-9]+}", batch_zip)
    return app


# alias по ТЗ
create_webapp = create_app


async def start_webapp(bot=None) -> web.AppRunner:
    runner = web.AppRunner(create_app(bot=bot))
    await runner.setup()
    site = web.TCPSite(runner, config.API_HOST, config.API_PORT)
    await site.start()
    logger.info("Mini App слушает %s:%s", config.API_HOST, config.API_PORT)
    return runner
