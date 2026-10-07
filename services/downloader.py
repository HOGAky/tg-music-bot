import asyncio
import logging
import os
from dataclasses import dataclass

import requests
import yt_dlp

from config import config
from services.storage import storage

logger = logging.getLogger(__name__)

CACHE_LIMIT_BYTES = 1_500_000_000

# сайт soundcloud.com из облака заблокирован, но api-v2 и CDN доступны:
# через них качаем треки, которые YouTube прячет за возрастным ограничением
_SC_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}

_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


@dataclass
class DownloadResult:
    path: str
    title: str
    duration: int | None
    thumbnail: str | None
    cache_key: str | None = None


def _cache_path(key: str) -> str:
    return os.path.join(config.CACHE_DIR, key.replace("/", os.sep))


async def _cached_copy(key: str) -> DownloadResult | None:
    """mp3 из кэша: сначала локальный файл, затем S3. None — кэша нет."""
    local_path = _cache_path(key)
    if os.path.exists(local_path):
        return DownloadResult(
            path=local_path,
            title="",
            duration=None,
            thumbnail=None,
            cache_key=key,
        )
    if not await asyncio.to_thread(storage.file_exists, key):
        return None
    got = await asyncio.to_thread(storage.download_file, key, local_path)
    if got is None:
        return None
    logger.info("Трек из S3-кэша: %s", key)
    return DownloadResult(
        path=local_path,
        title="",
        duration=None,
        thumbnail=None,
        cache_key=key,
    )


async def _push_to_s3(file_path: str, key: str) -> str | None:
    if not storage.enabled:
        return None
    uploaded = await asyncio.to_thread(storage.upload_file, file_path, key)
    if uploaded:
        logger.info("Трек загружен в S3-кэш: %s", key)
    return uploaded


def _download_sync(url: str, out_dir: str, quality: int) -> DownloadResult:
    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, f"%(id)s_{quality}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(quality),
        }],
    }
    # cookies.json/cookies.txt для YouTube: кладём в data/ (volume) без пересборки
    cookies = next((p for p in ("cookies.txt", "data/cookies.txt") if os.path.exists(p)), None)
    if cookies:  # опционально: обходим ограничения YouTube
        opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    path = os.path.join(out_dir, f"{info['id']}_{quality}.mp3")
    if not os.path.exists(path):
        raise RuntimeError("файл не найден после постобработки")

    thumb_path = None
    thumb_url = info.get("thumbnail")
    if thumb_url:
        try:
            import urllib.request
            thumb_path = os.path.join(out_dir, f"{info['id']}_{quality}_t.jpg")
            urllib.request.urlretrieve(thumb_url, thumb_path)
        except Exception:
            thumb_path = None

    return DownloadResult(
        path=path,
        title=info.get("title", "Unknown"),
        duration=int(info.get("duration") or 0) or None,
        thumbnail=thumb_path,
    )


def prune_cache(limit: int = CACHE_LIMIT_BYTES) -> None:
    """LRU-чистка: удаляем самые старые файлы, пока кэш больше лимита."""
    if not os.path.isdir(config.CACHE_DIR):
        return
    files = []
    for name in os.listdir(config.CACHE_DIR):
        path = os.path.join(config.CACHE_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if os.path.isfile(path):
            files.append((stat.st_atime, stat.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= limit:
            return
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


# ---------- SoundCloud fallback для age-restricted треков ----------
def _sc_search_sync(query: str, client_id: str, limit: int = 5) -> list[dict]:
    r = requests.get("https://api-v2.soundcloud.com/search/tracks",
                     params={"q": query, "client_id": client_id, "limit": limit},
                     timeout=15, headers=_SC_HEADERS)
    r.raise_for_status()
    return (r.json() or {}).get("collection") or []


def _sc_pick_best(collection: list[dict], duration: int | None) -> dict | None:
    candidates = [t for t in collection
                  if t.get("policy") == "ALLOW"
                  and any(tr.get("format", {}).get("protocol") == "progressive"
                          for tr in (t.get("media", {}).get("transcodings") or []))]
    if not candidates:
        return None
    if duration:
        return min(candidates, key=lambda t: abs((t.get("duration") or 0) // 1000 - duration))
    return candidates[0]


def _sc_download_sync(track_json: dict, client_id: str, out_dir: str) -> str:
    """progressive-mp3: транскодинг отдаёт JSON с подписанной ссылкой на CDN."""
    prog = next(tr for tr in track_json["media"]["transcodings"]
                if tr.get("format", {}).get("protocol") == "progressive")
    r = requests.get(prog["url"], params={"client_id": client_id},
                     timeout=20, headers=_SC_HEADERS)
    r.raise_for_status()
    media_url = (r.json() or {}).get("url")
    if not media_url:
        raise RuntimeError("SoundCloud не отдал ссылку на поток")
    path = os.path.join(out_dir, f"sc_{track_json['id']}.mp3")
    with requests.get(media_url, stream=True, timeout=90, headers=_SC_HEADERS) as resp:
        resp.raise_for_status()
        size = 0
        with open(path, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
                size += len(chunk)
    if size < 10_000:
        raise RuntimeError("файл слишком мал — не похож на трек")
    return path


async def download_soundcloud_fallback(track, quality: int = 128,
                                       *, cache_key: str | None = None) -> DownloadResult:
    """Age-restricted на YouTube: ищем тот же трек на SoundCloud и качаем
    progressive-mp3 (128 kbps) напрямую через api-v2 + sndcdn."""
    client_id = config.SOUNDCLOUD_CLIENT_ID
    if not client_id:
        raise RuntimeError("SOUNDCLOUD_CLIENT_ID не задан")
    query = f"{track.artist} {track.title}".strip() or track.title
    collection = await asyncio.to_thread(_sc_search_sync, query, client_id)
    best = _sc_pick_best(collection, track.duration)
    if best is None:
        raise RuntimeError("на SoundCloud нет подходящего доступного трека")
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    path = await asyncio.to_thread(_sc_download_sync, best, client_id, config.CACHE_DIR)
    logger.info("SoundCloud fallback: %r -> %s", query, best.get("permalink_url"))
    resolved_key = cache_key or track.s3_key or storage.key_for(
        track.download_url or track.source_url or query,
        quality,
    )
    uploaded = await _push_to_s3(path, resolved_key)
    return DownloadResult(
        path=path,
        title=best.get("title") or track.title,
        duration=(best.get("duration") or 0) // 1000 or track.duration,
        thumbnail=None,
        cache_key=uploaded,
    )


async def download(url: str, quality: int = 192, *, cache_key: str | None = None) -> DownloadResult:
    resolved_key = cache_key or storage.key_for(url, quality)
    # 1) кэш: локальный файл или S3
    cached = await _cached_copy(resolved_key)
    if cached is not None:
        return cached

    # защита от параллельного скачивания одного и того же URL
    async with _locks_guard:
        lock = _locks.setdefault(f"{url}#{quality}", asyncio.Lock())
    async with lock:
        # 2) повторная проверка S3: параллельный запрос мог уже выложить трек
        cached = await _cached_copy(resolved_key)
        if cached is not None:
            return cached

        # 3) yt-dlp с ретраями: часть соединений из облака рвётся по SSL
        result = None
        for attempt in range(1, 4):
            try:
                async with _semaphore:
                    result = await asyncio.to_thread(
                        _download_sync, url, config.CACHE_DIR, quality)
                break
            except Exception as e:
                # age-ограничение ретраить бессмысленно — его обходит SoundCloud-fallback
                if "Sign in to confirm" in str(e) or attempt == 3:
                    raise
                logger.warning("Скачивание %s: попытка %s не удалась: %s", url, attempt, e)
                await asyncio.sleep(1.5 * attempt)

        # 4) выкладываем в S3 — дальше локальный файл одноразовый (удаляется после отправки)
        result.cache_key = await _push_to_s3(result.path, resolved_key)
        return result


async def download_track(track, quality: int = 192) -> DownloadResult:
    url = track.download_url or track.source_url
    return await download(url, quality=quality, cache_key=track.s3_key)
