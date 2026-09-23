import logging
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

API_URL = "https://lrclib.net/api/get"


def _ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


async def fetch_lyrics(artist: str, title: str, duration: int | None = None) -> str | None:
    """Текст песни из LRCLIB (без ключей). None — если не найден."""
    if not title:
        return None
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
                if r.status != 200:
                    return None
                data = await r.json()
    except aiohttp.ClientError:
        logger.warning("LRCLIB недоступен")
        return None
    return (data or {}).get("plainLyrics") or None
