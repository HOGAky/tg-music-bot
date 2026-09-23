import logging
import re
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

API_URL = "https://lrclib.net/api/get"

_NOISE_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_SEP_RE = re.compile(r"\s*[—\-–|/\\,:]\s*feat\.?\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    """Убираем скобки с feat/remix/official/video, лишние пробелы и разделители."""
    if not s:
        return ""
    out = _NOISE_RE.sub(" ", s)
    out = _SEP_RE.sub(" ", out)
    out = _WS_RE.sub(" ", out).strip(" -—–·*,.!?\t\n")
    return out


def _ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


async def _query_lrclib(artist: str, title: str, duration: int | None) -> str | None:
    params = {"artist_name": artist or "", "track_name": title}
    if duration:
        params["duration"] = str(duration)
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=aiohttp.TCPConnector(ssl=_ssl_context()),
        ) as http:
            async with http.get(API_URL, params=params) as r:
                logger.debug("LRCLIB status %s params=%s", r.status, params)
                if r.status != 200:
                    return None
                data = await r.json()
    except aiohttp.ClientError as e:
        logger.warning("LRCLIB недоступен: %s params=%s", e, params)
        return None
    except Exception as e:
        logger.exception("LRCLIB неизвестная ошибка: %s params=%s", e, params)
        return None
    result = (data or {}).get("plainLyrics")
    if not result:
        logger.debug("LRCLIB вернул пустой текст params=%s resp=%s",
                     params, {k: v for k, v in (data or {}).items() if k != "plainLyrics"})
    return result or None


async def fetch_lyrics(artist: str, title: str, duration: int | None = None) -> str | None:
    """Текст песни из LRCLIB (без ключей). None — если не найден.

    Стратегия поиска:
      1) исходные artist + title (с нормализацией)
      2) только нормализованный title (если artist пустой или нет совпадений)
      3) исходные значения без нормализации как последняя попытка
    """
    if not title:
        return None

    norm_title = _normalize(title)
    norm_artist = _normalize(artist)

    attempts: list[tuple[str, str]] = []
    if norm_artist and norm_title:
        attempts.append((norm_artist, norm_title))
    if norm_title and (norm_title != title or not norm_artist):
        attempts.append(("", norm_title))
    if artist and title and (artist != norm_artist or title != norm_title):
        attempts.append((artist.strip(), title.strip()))

    last_result: str | None = None
    for i, (a, t) in enumerate(attempts, 1):
        logger.info("Lyrics попытка %s: artist=%r title=%r dur=%s", i, a, t, duration)
        text = await _query_lrclib(a, t, duration)
        if text:
            last_result = text
            break
        # попробуем без duration — возможно он мешает точному совпадению
        if duration:
            text2 = await _query_lrclib(a, t, None)
            if text2:
                last_result = text2
                break

    if last_result:
        logger.info("Lyrics найдены для %r — %r (%s строк)",
                    artist or "<no artist>", title, last_result.count("\n") + 1)
    else:
        logger.warning("Lyrics не найдены для %r — %r (попыток: %s)",
                       artist or "<no artist>", title, len(attempts) * (2 if duration else 1))
    return last_result
