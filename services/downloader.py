import asyncio
import logging
import os
from dataclasses import dataclass

import yt_dlp

from config import config
from services.storage import storage

logger = logging.getLogger(__name__)

CACHE_LIMIT_BYTES = 1_500_000_000

_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


@dataclass
class DownloadResult:
    path: str
    title: str
    duration: int | None
    thumbnail: str | None


async def _cached_copy(key: str) -> DownloadResult | None:
    """mp3 из кэша: сначала локальный файл, затем S3. None — кэша нет."""
    local_path = os.path.join(config.CACHE_DIR, key)
    if os.path.exists(local_path):
        return DownloadResult(path=local_path, title="", duration=None, thumbnail=None)
    if not await asyncio.to_thread(storage.file_exists, key):
        return None
    got = await asyncio.to_thread(storage.download_file, key, local_path)
    if got is None:
        return None
    logger.info("Трек из S3-кэша: %s", key)
    return DownloadResult(path=local_path, title="", duration=None, thumbnail=None)


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


async def download(url: str, quality: int = 192) -> DownloadResult:
    # 1) кэш: локальный файл или S3
    cached = await _cached_copy(storage.key_for(url, quality))
    if cached is not None:
        return cached

    # защита от параллельного скачивания одного и того же URL
    async with _locks_guard:
        lock = _locks.setdefault(f"{url}#{quality}", asyncio.Lock())
    async with lock:
        # 2) повторная проверка S3: параллельный запрос мог уже выложить трек
        cached = await _cached_copy(storage.key_for(url, quality))
        if cached is not None:
            return cached

        # 3) yt-dlp
        async with _semaphore:
            result = await asyncio.to_thread(_download_sync, url, config.CACHE_DIR, quality)

        # 4) выкладываем в S3 — дальше локальный файл одноразовый (удаляется после отправки)
        await asyncio.to_thread(storage.upload_file, result.path,
                                storage.key_for(url, quality))
        return result