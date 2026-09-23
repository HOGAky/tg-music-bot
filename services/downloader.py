import asyncio
import os
from dataclasses import dataclass

import yt_dlp

from config import config

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
    if os.path.exists("cookies.txt"):  # опционально: обходим ограничения YouTube
        opts["cookiefile"] = "cookies.txt"

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
    # защита от параллельного скачивания одного и того же URL
    async with _locks_guard:
        lock = _locks.setdefault(f"{url}#{quality}", asyncio.Lock())
    async with lock:
        async with _semaphore:
            return await asyncio.to_thread(_download_sync, url, config.CACHE_DIR, quality)