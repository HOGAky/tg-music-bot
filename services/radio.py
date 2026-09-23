import asyncio
import logging
import re

from services.searcher import FoundTrack, search, ytmusic

logger = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")


def _radio_sync(video_id: str, limit: int) -> list[FoundTrack]:
    data = ytmusic().get_watch_playlist(videoId=video_id, limit=limit + 1) or {}
    out = []
    for item in data.get("tracks", []):
        vid = item.get("videoId")
        if not vid or vid == video_id:
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        artists = item.get("artists") or []
        out.append(FoundTrack(
            title=item.get("title", ""),
            artist=", ".join(a.get("name", "") for a in artists),
            source_url=url,
            download_url=url,
        ))
        if len(out) >= limit:
            break
    return out


async def similar_tracks(source_url: str, artist: str, title: str,
                         limit: int = 5) -> list[FoundTrack]:
    """Похожие треки: радио YT Music по видео, иначе — поиск по артисту."""
    match = VIDEO_ID_RE.search(source_url or "")
    if match:
        try:
            tracks = await asyncio.to_thread(_radio_sync, match.group(1), limit)
        except Exception as e:
            logger.warning("YTMusic радио не сработало vid=%s artist=%r title=%r: %s",
                           match.group(1), artist, title, e)
            tracks = []
        if tracks:
            logger.info("Радио: по %r — %r найдено %s похожих через YTMusic",
                        artist, title, len(tracks))
            return tracks
    query = artist or title
    if not query:
        logger.warning("Радио: нет ни artist ни title для поиска похожих, source=%s", source_url)
        return []
    found = await search(query, limit=limit + 1)
    result = [f for f in found if f.source_url != source_url][:limit]
    logger.info("Радио: fallback-поиск по %r -> %s похожих", query, len(result))
    return result
