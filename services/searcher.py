import asyncio
import logging
import os
import re
import ssl
from dataclasses import dataclass

import aiohttp
import certifi
import yt_dlp
from ytmusicapi import YTMusic

from config import config

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
# ДВЕ захватывающие группы: тип (track/album/playlist) и id
SPOTIFY_RE = re.compile(r"(track|album|playlist)/([A-Za-z0-9]{22})")

_ytm = YTMusic()


@dataclass
class FoundTrack:
    title: str
    artist: str
    source_url: str                 # уникальный идентификатор трека
    duration: int | None = None
    download_url: str | None = None # если None — найдём лениво через YT Music


def is_url(text: str) -> bool:
    return bool(URL_RE.fullmatch(text.strip()))


# ---------- поиск по названию (YouTube Music) ----------
def _ytm_search_sync(query: str, limit: int) -> list[FoundTrack]:
    results = _ytm.search(query, filter="songs", limit=limit) or []
    out = []
    for r in results:
        vid = r.get("videoId")
        if not vid:
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        out.append(FoundTrack(
            title=r.get("title", ""),
            artist=", ".join(a["name"] for a in r.get("artists", [])),
            source_url=url, duration=r.get("duration_seconds"), download_url=url,
        ))
    return out


async def search(query: str, limit: int = 8) -> list[FoundTrack]:
    return await asyncio.to_thread(_ytm_search_sync, query, limit)


# ---------- метаданные для "прочих" ссылок через yt-dlp ----------
def _probe_sync(url: str) -> FoundTrack:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = next((e for e in info["entries"] if e), info)
        raw_duration = info.get("duration")
        return FoundTrack(
            title=info.get("title") or url,
            artist=info.get("uploader") or "",
            source_url=info.get("webpage_url") or url,
            duration=int(raw_duration) if raw_duration else None,  # float недопустим!
            download_url=info.get("webpage_url") or url,
        )
    except Exception:
        return FoundTrack(title=url, artist="", source_url=url, download_url=url)


# ---------- Spotify ----------
def _sp_track_sync(sid: str) -> FoundTrack:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET), requests_timeout=15)
    t = sp.track(sid)
    return FoundTrack(
        title=t["name"],
        artist=", ".join(a["name"] for a in t["artists"]),
        source_url=t["external_urls"]["spotify"],
        duration=int(t["duration_ms"] / 1000),
    )


def _sp_collection_sync(kind: str, sid: str) -> list[FoundTrack]:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET), requests_timeout=15)
    out = []
    if kind == "album":
        for t in sp.album(sid)["tracks"]["items"][:30]:
            out.append(FoundTrack(
                title=t["name"],
                artist=", ".join(a["name"] for a in t["artists"]),
                source_url=t["external_urls"]["spotify"],
                duration=int(t["duration_ms"] / 1000),
            ))
    elif kind == "playlist":
        for item in sp.playlist(sid)["tracks"]["items"][:50]:
            t = item.get("track")
            if not t or not t.get("id"):
                continue
            out.append(FoundTrack(
                title=t["name"],
                artist=", ".join(a["name"] for a in t["artists"]),
                source_url=t["external_urls"]["spotify"],
                duration=int(t["duration_ms"] / 1000),
            ))
    return out


async def _fetch_oembed(url: str, ssl_ctx) -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=aiohttp.TCPConnector(ssl=ssl_ctx),
    ) as http:
        async with http.get("https://open.spotify.com/oembed", params={"url": url}) as r:
            r.raise_for_status()
            return await r.json()


async def _spotify_via_oembed(url: str) -> list[FoundTrack]:
    """Fallback без API-ключей. Сначала с проверкой сертификата (через certifi),
    при ошибке сертификата (антивирус/прокси на Windows) — повтор без проверки."""
    try:
        ctx: object = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None  # дефолтный контекст
    try:
        data = await _fetch_oembed(url, ctx)
    except aiohttp.ClientConnectorCertificateError:
        logger.warning("Spotify oembed: сертификат не прошёл проверку "
                       "(обычно антивирус/прокси подменяет TLS). Повтор без проверки SSL.")
        try:
            data = await _fetch_oembed(url, False)
        except aiohttp.ClientError:
            return []
    except aiohttp.ClientError:
        return []
    title = (data or {}).get("title")
    if not title:
        return []
    return [FoundTrack(title=title, artist="", source_url=url)]


async def resolve_url(url: str) -> list[FoundTrack]:
    """Разбор ссылки -> список треков (1 для трека, много для альбома/плейлиста)."""
    if "spotify.com" in url:
        m = SPOTIFY_RE.search(url)
        if not m:
            return []
        kind, sid = m.groups()
        if config.SPOTIFY_CLIENT_ID:
            try:
                if kind == "track":
                    return [await asyncio.to_thread(_sp_track_sync, sid)]
                return await asyncio.to_thread(_sp_collection_sync, kind, sid)
            except Exception:
                pass  # упадём в oembed-fallback ниже
        if kind == "track":
            return await _spotify_via_oembed(url)
        return []
    # YouTube / YouTube Music / SoundCloud / Bandcamp / ... — всё, что умеет yt-dlp
    return [await asyncio.to_thread(_probe_sync, url)]