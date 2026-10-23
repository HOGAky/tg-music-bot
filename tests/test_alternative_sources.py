import unittest

from services import downloader


class AlternativeSourceTests(unittest.TestCase):
    def test_soundcloud_requires_owner_download_permission(self):
        track = {
            "policy": "ALLOW",
            "downloadable": True,
            "duration": 180_000,
            "media": {
                "transcodings": [
                    {"format": {"protocol": "progressive"}},
                ],
            },
        }

        self.assertIsNotNone(downloader._sc_pick_best([track], 180))
        self.assertIsNone(
            downloader._sc_pick_best([{**track, "downloadable": False}], 180)
        )

    def test_soundcloud_rejects_different_duration(self):
        track = {
            "policy": "ALLOW",
            "downloadable": True,
            "duration": 300_000,
            "media": {
                "transcodings": [
                    {"format": {"protocol": "progressive"}},
                ],
            },
        }

        self.assertIsNone(downloader._sc_pick_best([track], 180))

if __name__ == "__main__":
    unittest.main()
