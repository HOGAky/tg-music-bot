import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from services import downloader, searcher
from services.searcher import FoundTrack


class SearchCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        searcher._search_cache.clear()

    async def asyncTearDown(self):
        searcher._search_cache.clear()

    async def test_reuses_results_for_normalized_query(self):
        track = FoundTrack(
            title="Song",
            artist="Artist",
            source_url="https://example.test/song",
        )
        with patch.object(
            searcher,
            "_ytm_search_sync",
            return_value=[track],
        ) as search_sync:
            first = await searcher.search("  Example   Song ")
            second = await searcher.search("example song")

        self.assertEqual(first, [track])
        self.assertEqual(second, [track])
        search_sync.assert_called_once_with("  Example   Song ", 8)

    async def test_does_not_cache_empty_search_results(self):
        with patch.object(
            searcher,
            "_ytm_search_sync",
            return_value=[],
        ) as search_sync:
            await searcher.search("missing song")
            await searcher.search("missing song")

        self.assertEqual(search_sync.call_count, 2)

    async def test_expired_results_are_refetched(self):
        track = FoundTrack(
            title="Song",
            artist="Artist",
            source_url="https://example.test/song",
        )
        with patch.object(
            searcher,
            "_ytm_search_sync",
            return_value=[track],
        ) as search_sync:
            await searcher.search("song")
            key = ("song", 8)
            searcher._search_cache[key] = (0, [track])
            await searcher.search("song")

        self.assertEqual(search_sync.call_count, 2)

    async def test_cache_evicts_oldest_entry_over_capacity(self):
        track = FoundTrack(
            title="Song",
            artist="Artist",
            source_url="https://example.test/song",
        )
        with (
            patch.object(searcher, "_ytm_search_sync", return_value=[track]),
            patch.object(searcher, "_SEARCH_CACHE_MAX_SIZE", 2),
        ):
            await searcher.search("first")
            await searcher.search("second")
            await searcher.search("third")

        self.assertEqual(list(searcher._search_cache), [("second", 8), ("third", 8)])

    async def test_youtube_music_requests_only_remaining_results(self):
        class FakeYtm:
            def __init__(self):
                self.calls = []

            def search(self, query, *, filter, limit):
                self.calls.append((query, filter, limit))
                return [
                    {
                        "videoId": str(index),
                        "title": f"Song {index}",
                        "artists": [{"name": "Artist"}],
                    }
                    for index in range(limit)
                ]

        fake_ytm = FakeYtm()
        with patch.object(searcher, "ytmusic", return_value=fake_ytm):
            tracks = searcher._ytm_search_sync("song", 5)

        self.assertEqual(len(tracks), 5)
        self.assertEqual(fake_ytm.calls, [("song", "songs", 5)])


class DownloadCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_download_is_reused_on_next_request(self):
        def fake_download(url, out_dir, quality):
            source = Path(out_dir) / "source.mp3"
            source.write_bytes(b"audio data")
            return downloader.DownloadResult(
                path=str(source),
                title="Song",
                duration=180,
                thumbnail=None,
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(downloader.config, "CACHE_DIR", directory),
            patch.object(
                downloader.storage,
                "key_for",
                return_value="stable.mp3",
            ),
            patch.object(
                downloader.storage,
                "file_exists",
                return_value=False,
            ),
            patch.object(downloader.storage, "upload_file", return_value=None),
            patch.object(
                type(downloader.storage),
                "enabled",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                downloader,
                "_download_sync",
                side_effect=fake_download,
            ) as download_sync,
        ):
            first, second = await asyncio.gather(
                downloader.download("https://example.test/song"),
                downloader.download("https://example.test/song"),
            )
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(Path(first.path).is_file())
            self.assertTrue(Path(second.path).is_file())
            self.assertTrue(second.local_disposable)
            download_sync.assert_called_once()

    async def test_same_key_downloads_are_serialized_and_lock_is_released(self):
        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            async with downloader._download_lock("shared-key"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(worker() for _ in range(8)))

        self.assertEqual(max_active, 1)
        self.assertNotIn("shared-key", downloader._locks)
