import unittest
from pathlib import Path

from settings_store import AppSettings, SettingsStore


class SettingsStoreTest(unittest.TestCase):
    def test_settings_round_trip(self):
        store = SettingsStore()
        settings = AppSettings(default_cover_path="C:/cover.png")

        store.save_settings(settings)
        loaded = store.load_settings()

        self.assertEqual(loaded.default_cover_path, "C:/cover.png")

    def test_history_limits_to_200(self):
        store = SettingsStore()

        for index in range(205):
            store.add_upload_history({"name": f"file-{index}", "share_text": f"xfb1.{index}"})

        history = store.load_upload_history()

        self.assertEqual(len(history), 200)
        self.assertEqual(history[0]["name"], "file-204")

    def test_default_settings_return_defaults(self):
        store = SettingsStore()
        settings = store.load_settings()

        self.assertEqual(settings.default_cover_path, "")
        self.assertEqual(settings.chunk_size_mb, 100)
        self.assertEqual(settings.upload_workers, 3)
        self.assertTrue(settings.stego_chunks)

    def test_invalid_chunk_settings_are_normalized(self):
        store = SettingsStore()
        store.save_settings(AppSettings(chunk_size_mb=999, upload_workers=99, stego_chunks=True))
        settings = store.load_settings()

        self.assertEqual(settings.chunk_size_mb, 200)
        self.assertEqual(settings.upload_workers, 8)
        self.assertTrue(settings.stego_chunks)

    def test_low_chunk_settings_are_normalized(self):
        store = SettingsStore()
        store.save_settings(AppSettings(chunk_size_mb=1, upload_workers=0, stego_chunks=False))
        settings = store.load_settings()

        self.assertEqual(settings.chunk_size_mb, 5)
        self.assertEqual(settings.upload_workers, 1)
        self.assertFalse(settings.stego_chunks)

    def test_new_store_instance_has_no_history(self):
        store = SettingsStore()
        store.add_upload_history({"name": "a"})
        fresh = SettingsStore()
        self.assertEqual(fresh.load_upload_history(), [])


if __name__ == "__main__":
    unittest.main()
