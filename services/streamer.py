"""Live-стриминг: извлечение прямого URL аудиопотока через yt-dlp БЕЗ
скачивания файла. Результат кэшируется на 4 часа (ссылки GoogleVideo живут
~6 часов). Фолбэк: если YouTube отдаёт 403/бота — ищем трек на SoundCloud
и стримим его progressive-поток."""
import asyncio
import logging
import time

import requests
import yt_dlp

from config import config
from services import downloader

logger = logging.getLogger(__name__)

_OPTS = {
    "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "extract_flat": False,
}

# media_url -> (expires_at, info)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 4 * 3600  # 4 часа

_SC_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}

_MIME_BY_EXT = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
}

_AUDIO_RANK = {"m4a": 0, "mp4": 0, "mp3": 1, "aac": 2, "ogg": 3, "oga": 3,
               "opus": 3, "webm": 4}


def _pick_format(info: dict) -> dict | None:
    """Лучший progressive-аудиоформат: m4a/mp3 в приоритете (AAC/MP3 играют
    все браузеры, включая iOS Safari и WebView Telegram)."""
    formats = [f for f in (info.get("formats") or [])
               if f.get("url") and f.get("acodec") not in (None, "none")
               and f.get("protocol") in ("http", "https", None)]
    if not formats:
        return None
    formats.sort(key=lambda f: (
        _AUDIO_RANK.get(f.get("ext") or "", 9),
        -(f.get("abr") or f.get("tbr") or 0)))
    return formats[0]


def _extract_stream_sync(media_url: str) -> dict:
    """yt-dlp extract без скачивания -> прямой URL и заголовки для CDN."""
    with yt_dlp.YoutubeDL(_OPTS) as ydl:
        info = ydl.extract_info(media_url, download=False) or {}

    fmt = _pick_format(info)
    stream_url = fmt["url"] if fmt else info.get("url")
    if not stream_url:
        raise RuntimeError("yt-dlp не вернул progressive-аудиоформат")

    headers = dict(info.get("http_headers") or {})
    if fmt:
        headers.update(fmt.get("http_headers") or {})
    ext = (fmt or {}).get("ext") or info.get("ext") or ""
    return {
        "stream_url": stream_url,
        "headers": headers,
        "mime_type": _MIME_BY_EXT.get(ext, "audio/mp4"),
        "duration": info.get("duration"),
        "title": info.get("title") or "",
        "filesize": ((fmt or {}).get("filesize") or (fmt or {}).get("filesize_approx")
                     or info.get("filesize") or info.get("filesize_approx")),
    }


def _sc_stream_sync(query: str, duration: int | None) -> dict | None:
    """Прямой progressive-URL трека SoundCloud через api-v2 (без скачивания)."""
    client_id = config.SOUNDCLOUD_CLIENT_ID
    if not client_id:
        return None
    collection = downloader._sc_search_sync(query, client_id)
    # для стрима downloadable не обязателен — важен progressive-транскодинг
    candidates = [t for t in collection
                  if any(tr.get("format", {}).get("protocol") == "progressive"
                         for tr in (t.get("media", {}).get("transcodings") or []))]
    if not candidates:
        return None
    best = downloader._sc_pick_best(collection, duration)
    if best is None:
        if duration:
            with_dur = [t for t in candidates
                        if t.get("duration")
                        and abs(t["duration"] // 1000 - duration) <= 45]
            candidates = with_dur or candidates
        best = min(candidates,
                   key=lambda t: abs((t.get("duration") or 0) // 1000 - (duration or 0)))
    prog = next(tr for tr in best["media"]["transcodings"]
                if tr.get("format", {}).get("protocol") == "progressive")
    r = requests.get(prog["url"], params={"client_id": client_id},
                     timeout=15, headers=_SC_HEADERS)
    r.raise_for_status()
    media_url = (r.json() or {}).get("url")
    if not media_url:
        return None
    return {
        "stream_url": media_url,
        "headers": dict(_SC_HEADERS),
        "mime_type": "audio/mpeg",
        "duration": (best.get("duration") or 0) // 1000 or None,
        "title": best.get("title") or "",
        "filesize": None,
    }


async def _soundcloud_fallback(query: str, duration: int | None) -> dict | None:
    """YouTube забанил/403: ищем тот же трек на SoundCloud и стримим его."""
    try:
        return await asyncio.to_thread(_sc_stream_sync, query, duration)
    except Exception as e:
        logger.warning("SoundCloud fallback для %r не сработал: %s", query, e)
        return None


def _cached(media_url: str) -> dict | None:
    hit = _cache.get(media_url)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _cache.pop(media_url, None)
    return None


def drop_cache(media_url: str) -> None:
    """Протухшая ссылка (CDN ответил 403/410/416) — выбросить из кэша."""
    _cache.pop(media_url, None)


async def get_live_stream_info(media_url: str,
                               query_hint: str | None = None,
                               duration: int | None = None) -> dict:
    """Прямой stream_url + headers + mime. Кэш 4 ч; при ошибке yt-dlp —
    graceful fallback на SoundCloud по query_hint («артист название»)."""
    if info := _cached(media_url):
        return info
    try:
        info = await asyncio.to_thread(_extract_stream_sync, media_url)
    except Exception as e:
        logger.warning("Стрим-экстракт %s упал (%s) — пробуем SoundCloud",
                       media_url, e)
        if query_hint:
            info = await _soundcloud_fallback(query_hint, duration)
        if not info:
            raise
    _cache[media_url] = (time.monotonic() + _CACHE_TTL, info)
    return info
