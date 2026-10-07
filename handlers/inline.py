from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedAudio,
    InlineQueryResultsButton,
)

from database import repo
from database.session import SessionFactory

router = Router(name="inline")

RESULTS_LIMIT = 20
CACHE_TIME = 30


@router.inline_query()
async def inline_search(query: InlineQuery):
    """Inline отдаёт только треки с готовым file_id — их Telegram пришлёт мгновенно."""
    async with SessionFactory() as session:
        tracks = await repo.search_cached_tracks(session, query.query.strip(), RESULTS_LIMIT)

    results = [
        InlineQueryResultCachedAudio(id=str(t.id), audio_file_id=t.file_id)
        for t in tracks if t.file_id
    ]
    await query.answer(
        results,
        cache_time=CACHE_TIME,
        is_personal=True,
        button=None if results else InlineQueryResultsButton(
            text="Нет готовых треков — найти в боте", start_parameter="inline"),
    )
