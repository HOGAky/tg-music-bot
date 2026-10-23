import asyncio
import json
import logging
import re
import ssl
import threading
import time
from collections import OrderedDict
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
# Яндекс Музыка: .../users/<login>/playlists/<kind>, .../playlist/<login>/<kind>,
# .../album/<id> и .../track/<id> (в т.ч. album/<id>/track/<id>)
YANDEX_HOST = r"music\.yandex\.[a-z]+"
YANDEX_PLAYLIST_RE = re.compile(rf"{YANDEX_HOST}/(?:users/([^/]+)/playlists|playlist/([^/]+))/(\d+)")
YANDEX_ALBUM_RE = re.compile(rf"{YANDEX_HOST}/album/(\d+)")
YANDEX_TRACK_RE = re.compile(rf"{YANDEX_HOST}/(?:album/\d+/)?track/(\d+)")
# новый формат «поделиться»: /playlists/<uuid> (uuid может иметь префикс "lk.")
YANDEX_UUID_RE = re.compile(rf"{YANDEX_HOST}/playlists/([A-Za-z0-9._-]+)")

# максимум треков в альбоме/плейлисте, который отдаём целиком
MAX_COLLECTION_TRACKS = 5000

_ytm: YTMusic | None = None
_ytm_lock = threading.Lock()
_SEARCH_CACHE_TTL = 300
_SEARCH_CACHE_MAX_SIZE = 512
_search_cache: OrderedDict[
    tuple[str, int], tuple[float, list["FoundTrack"]]
] = OrderedDict()


def ytmusic() -> YTMusic:
    global _ytm
    if _ytm is None:
        with _ytm_lock:
            if _ytm is None:
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        _ytm = YTMusic()
                        break
                    except Exception as e:
                        last_exc = e
                        logger.warning("YTMusic init attempt %s failed: %s", attempt + 1, e)
                        if attempt < 2:
                            import time
                            time.sleep(0.5 * (attempt + 1))
                if _ytm is None:
                    raise RuntimeError(f"Не удалось инициализировать YTMusic: {last_exc}") from last_exc
    return _ytm


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
def _parse_ytm_results(results: list[dict], seen: set[str]) -> list[FoundTrack]:
    out: list[FoundTrack] = []
    for r in results or []:
        vid = r.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        url = f"https://www.youtube.com/watch?v={vid}"
        artists_raw = r.get("artists") or []
        artist_names: list[str] = []
        for a in artists_raw:
            name = a.get("name") if isinstance(a, dict) else None
            if name:
                artist_names.append(name)
        out.append(FoundTrack(
            title=r.get("title", ""),
            artist=", ".join(artist_names),
            source_url=url, duration=r.get("duration_seconds"), download_url=url,
        ))
    return out


def _ytm_search_sync(query: str, limit: int) -> list[FoundTrack]:
    ytm = ytmusic()
    seen: set[str] = set()
    out: list[FoundTrack] = []
    # Порядок фильтров: сначала официальные треки, потом видео, потом вообще всё
    for filter_val in ("songs", "videos", None):
        need = limit - len(out)
        if need <= 0:
            break
        try:
            results = ytm.search(query, filter=filter_val, limit=need) or []
        except Exception:
            logger.exception("YTMusic search failed (query=%r, filter=%s)", query, filter_val)
            continue
        out.extend(_parse_ytm_results(results, seen))
    if not out:
        logger.warning("YTMusic ничего не нашёл по запросу: %r", query)
    else:
        logger.info("YTMusic поиск %r -> %s треков", query, len(out))
    return out[:limit]


async def search(query: str, limit: int = 8) -> list[FoundTrack]:
    """Поиск с ретраями: часть соединений из облака рвётся по SSL —
    повторная попытка обычно проходит."""
    cache_key = (" ".join(query.casefold().split()), limit)
    cached = _search_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None:
        expires_at, tracks = cached
        if expires_at > now:
            _search_cache.move_to_end(cache_key)
            logger.debug("YTMusic cache hit for query=%r", query)
            return list(tracks)
        del _search_cache[cache_key]

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            tracks = await asyncio.to_thread(_ytm_search_sync, query, limit)
            if tracks:
                _search_cache[cache_key] = (
                    time.monotonic() + _SEARCH_CACHE_TTL,
                    tracks,
                )
                _search_cache.move_to_end(cache_key)
                while len(_search_cache) > _SEARCH_CACHE_MAX_SIZE:
                    _search_cache.popitem(last=False)
            return tracks
        except Exception as e:
            last_exc = e
            logger.warning("Поиск %r: попытка %s не удалась: %s", query, attempt, e)
            await asyncio.sleep(0.8 * attempt)
    logger.error("Поиск %r не удался после 3 попыток: %s", query, last_exc)
    return []


# ---------- метаданные для "прочих" ссылок через yt-dlp ----------
def _probe_sync(url: str) -> FoundTrack:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = next((e for e in info["entries"] if e), info)
        raw_duration = info.get("duration")
        title = info.get("title") or url
        artist = info.get("uploader") or ""
        return FoundTrack(
            title=title,
            artist=artist,
            source_url=info.get("webpage_url") or url,
            duration=int(raw_duration) if raw_duration else None,
            download_url=info.get("webpage_url") or url,
        )
    except Exception as e:
        logger.warning("yt-dlp probe не смог обработать %r: %s", url, e)
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
    out: list[FoundTrack] = []

    def _track(t: dict) -> None:
        if not t or not t.get("id") or t.get("type", "track") != "track":
            return  # удалённые/локальные треки и подкасты пропускаем
        out.append(FoundTrack(
            title=t["name"],
            artist=", ".join(a["name"] for a in t["artists"]),
            source_url=t["external_urls"]["spotify"],
            duration=int(t["duration_ms"] / 1000),
        ))

    if kind == "album":
        resp = sp.album(sid)["tracks"]
        while True:
            for t in resp["items"]:
                _track(t)
            if not resp.get("next") or len(out) >= MAX_COLLECTION_TRACKS:
                break
            resp = sp.next(resp)
    elif kind == "playlist":
        resp = sp.playlist_items(sid, limit=100, additional_types=("track",))
        while True:
            for item in resp["items"]:
                _track(item.get("track") or {})
            if not resp.get("next") or len(out) >= MAX_COLLECTION_TRACKS:
                break
            resp = sp.next(resp)
    return out[:MAX_COLLECTION_TRACKS]


# ---------- Яндекс Музыка ----------
def _yandex_found(t) -> FoundTrack | None:
    """Track из yandex-music -> FoundTrack. Прямой ссылки нет — источник ищется
    лениво на YT Music (как у Spotify) через ensure_download_url."""
    if t is None or not getattr(t, "available", True) or not getattr(t, "id", None):
        return None
    artist = ", ".join(a.name for a in (t.artists or []) if getattr(a, "name", None))
    dur = getattr(t, "duration_ms", None)
    return FoundTrack(
        title=t.title or "Unknown",
        artist=artist,
        source_url=f"https://music.yandex.ru/track/{t.id}",
        duration=int(dur // 1000) if dur else None,
    )


def _yandex_page_html(url: str) -> str | None:
    """HTML страницы плейлиста. Обычные HTTP-клиенты Яндекс встречает капчей,
    поэтому имитируем TLS-отпечаток Chrome через curl_cffi."""
    try:
        from curl_cffi import requests as creq
    except ImportError:
        logger.warning("curl_cffi не установлен — ссылки /playlists/<uuid> не поддерживаются")
        return None
    try:
        resp = creq.get(url, impersonate="chrome", timeout=25)
    except Exception as e:
        logger.warning("Не удалось открыть страницу Яндекса %s: %s", url, e)
        return None
    text = resp.text or ""
    if resp.status_code != 200 or "showcaptcha" in str(resp.url) or "showcaptcha" in text[:4000]:
        logger.warning("Яндекс отдал капчу/ошибку для %s (status=%s)", url, resp.status_code)
        return None
    return text


# пути в __STATE_PATCHES__ внутри HTML: слеши в JSON заэкранированы как \u002F
_PATCH_META_NEEDLE = '"path":"\\u002Fplaylist\\u002Fmeta","value":'
_PATCH_ITEM_RE = re.compile(r'"path":"\\u002Fplaylist\\u002Fitems\\u002F(\d+)","value":')
_DECODER = json.JSONDecoder()


def _patch_value_at(html: str, pos: int):
    try:
        value, _ = _DECODER.raw_decode(html, pos)
        return value
    except json.JSONDecodeError:
        return None


def _yandex_state(html: str) -> tuple[dict | None, list[dict]]:
    """Из HTML страницы достаёт __STATE_PATCHES__: мета плейлиста и
    упорядоченные items ({id, albumId, ...} — id треков плейлиста)."""
    meta = None
    i = html.find(_PATCH_META_NEEDLE)
    if i >= 0:
        meta = _patch_value_at(html, i + len(_PATCH_META_NEEDLE))
    items: dict[int, dict] = {}
    for m in _PATCH_ITEM_RE.finditer(html):
        value = _patch_value_at(html, m.end())
        if isinstance(value, dict) and value.get("id"):
            items[int(m.group(1))] = value
    return meta, [items[k] for k in sorted(items)]


def _yandex_uuid_sync(client, url: str) -> list[FoundTrack]:
    """Новые ссылки «поделиться» вида /playlists/<uuid>: по uuid API плейлист не
    отдаёт, но веб-сервер вшивает id всех треков в HTML страницы — парсим его."""
    html = _yandex_page_html(url)
    if not html:
        return []
    meta, items = _yandex_state(html)
    if not items:
        logger.warning("В HTML %s не найдено состояние плейлиста", url)
        return []
    title = (meta or {}).get("title", "?")
    total = (meta or {}).get("tracksCount") or len(items)
    logger.info("Яндекс плейлист %r: %s/%s треков из HTML-состояния", title, len(items), total)

    track_ids = [str(it["id"]) for it in items if it.get("id")]
    out: list[FoundTrack] = []
    for start in range(0, len(track_ids), 50):
        chunk = track_ids[start:start + 50]
        try:
            tracks = client.tracks(chunk) or []
        except Exception as e:
            logger.warning("Не удалось получить треки Яндекса (%s шт.): %s", len(chunk), e)
            continue
        for t in tracks:
            f = _yandex_found(t)
            if f:
                out.append(f)
    return out[:MAX_COLLECTION_TRACKS]


def _yandex_sync(url: str) -> list[FoundTrack]:
    from yandex_music import Client
    client = Client(config.YANDEX_TOKEN or None).init()

    m = YANDEX_UUID_RE.search(url)
    if m:
        return _yandex_uuid_sync(client, url)

    m = YANDEX_TRACK_RE.search(url)
    if m:
        tracks = client.tracks(m.group(1)) or []
        if isinstance(tracks, list) and tracks:
            f = _yandex_found(tracks[0])
            return [f] if f else []
        return []

    m = YANDEX_PLAYLIST_RE.search(url)
    if m:
        owner = m.group(1) or m.group(2)
        pl = client.users_playlists(kind=m.group(3), user_id=owner)
        if isinstance(pl, list):
            pl = pl[0] if pl else None
        shorts = list(getattr(pl, "tracks", None) or [])
        # yandex-music 2.x: PlaylistTrack.track; 3.x: TrackShort.track — может быть не заполнен
        slots: list[FoundTrack | None] = []
        missing_ids: list[str] = []
        for pt in shorts:
            t = getattr(pt, "track", None)
            f = _yandex_found(t) if t is not None else None
            slots.append(f)
            if f is None and getattr(pt, "id", None):
                missing_ids.append(pt.id)
        fetched: dict[str, object] = {}
        if missing_ids:  # дозапрашиваем полные треки одной пачкой
            try:
                for t in client.tracks(missing_ids) or []:
                    if t is not None and t.id:
                        fetched[str(t.id)] = t
            except Exception as e:
                logger.warning("Не удалось дозапросить %s треков Яндекса: %s", len(missing_ids), e)
        missing_iter = iter(missing_ids)  # None-слоты и их id записаны в одном порядке
        out: list[FoundTrack] = []
        for slot in slots:
            if slot is not None:
                out.append(slot)
                continue
            tid = next(missing_iter, None)
            f = _yandex_found(fetched.get(str(tid))) if tid else None
            if f is not None:
                out.append(f)
        return out[:MAX_COLLECTION_TRACKS]

    m = YANDEX_ALBUM_RE.search(url)
    if m:
        album = client.albums_with_tracks(m.group(1))
        out = []
        for vol in getattr(album, "volumes", None) or []:
            for t in vol or []:
                f = _yandex_found(t)
                if f:
                    out.append(f)
        return out[:MAX_COLLECTION_TRACKS]

    return []


async def _fetch_oembed(url: str, ssl_ctx) -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=aiohttp.TCPConnector(ssl=ssl_ctx),
    ) as http, http.get("https://open.spotify.com/oembed", params={"url": url}) as r:
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
        except aiohttp.ClientError as e:
            logger.warning("Spotify oembed fallback с отключённым SSL тоже упал: %s", e)
            return []
    except aiohttp.ClientError as e:
        logger.warning("Spotify oembed недоступен: %s", e)
        return []
    title = (data or {}).get("title")
    if not title:
        logger.warning("Spotify oembed вернул пустой title для %s", url)
        return []
    # Пытаемся вытащить исполнителя из поля author_name, если оно есть
    artist = ""
    if isinstance(data, dict):
        author = data.get("author_name")
        if isinstance(author, str) and author:
            artist = author
    return [FoundTrack(title=title, artist=artist, source_url=url)]


async def _spotify_via_embed(kind: str, sid: str) -> list[FoundTrack]:
    """Плейлисты/альбомы Spotify без API-ключей: трек-лист из публичной embed-страницы
    (JSON в __NEXT_DATA__). Отдаёт примерно первые 50 треков; полный список — через API."""
    try:
        ctx: object = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None  # дефолтный контекст
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    embed_url = f"https://open.spotify.com/embed/{kind}/{sid}"

    async def fetch(ssl_ctx) -> str:
        timeout = aiohttp.ClientTimeout(total=15)
        async with (
            aiohttp.ClientSession(timeout=timeout, headers=headers) as http,
            http.get(embed_url, ssl=ssl_ctx) as r,
        ):
            r.raise_for_status()
            return await r.text(errors="replace")

    try:
        html = await fetch(ctx)
    except aiohttp.ClientConnectorCertificateError:
        logger.warning("Spotify embed: сертификат не прошёл проверку. Повтор без проверки SSL.")
        try:
            html = await fetch(False)
        except aiohttp.ClientError as e:
            logger.warning("Spotify embed недоступен (без SSL тоже): %s", e)
            return []
    except aiohttp.ClientError as e:
        logger.warning("Spotify embed недоступен: %s", e)
        return []

    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        logger.warning("Spotify embed: не найден __NEXT_DATA__ для %s/%s", kind, sid)
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning("Spotify embed: битый JSON для %s/%s: %s", kind, sid, e)
        return []

    def find_track_list(node: object) -> list | None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "trackList" and isinstance(v, list) and v:
                    return v
                found = find_track_list(v)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = find_track_list(v)
                if found:
                    return found
        return None

    out: list[FoundTrack] = []
    for item in find_track_list(data) or []:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue
        duration = item.get("duration")
        out.append(FoundTrack(
            title=item.get("title") or "Unknown",
            artist=item.get("subtitle") or "",
            source_url=f"https://open.spotify.com/track/{uri.rsplit(':', 1)[-1]}",
            duration=int(duration / 1000) if isinstance(duration, (int, float)) and duration else None,
        ))
    if not out:
        logger.warning("Spotify embed: пустой trackList для %s/%s", kind, sid)
    return out[:MAX_COLLECTION_TRACKS]


async def resolve_url(url: str) -> list[FoundTrack]:
    """Разбор ссылки -> список треков (1 для трека, много для альбома/плейлиста)."""
    if "spotify.com" in url:
        m = SPOTIFY_RE.search(url)
        if not m:
            logger.warning("Spotify ссылка не распознана: %s", url)
            return []
        kind, sid = m.groups()
        if config.SPOTIFY_CLIENT_ID:
            try:
                if kind == "track":
                    return [await asyncio.to_thread(_sp_track_sync, sid)]
                return await asyncio.to_thread(_sp_collection_sync, kind, sid)
            except Exception as e:
                logger.warning("Spotify API ошибка (%s/%s): %s. Переходим в fallback (oembed/embed).",
                               kind, sid, e)
        if kind == "track":
            return await _spotify_via_oembed(url)
        found = await _spotify_via_embed(kind, sid)
        if found:
            return found
        logger.warning("Spotify %s: трек-лист не получен ни через API, ни через embed", kind)
        return []
    if "music.yandex" in url:
        try:
            return await asyncio.to_thread(_yandex_sync, url)
        except Exception:
            logger.exception("Яндекс Музыка не смогла обработать %s", url)
            return []
    # YouTube / YouTube Music / SoundCloud / Bandcamp / ... — всё, что умеет yt-dlp
    return [await asyncio.to_thread(_probe_sync, url)]