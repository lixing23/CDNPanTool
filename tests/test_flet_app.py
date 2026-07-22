import inspect
import unittest
from pathlib import Path

from flet_app import FletCloudDriveApp


class FletAppTest(unittest.TestCase):
    def test_file_picker_handlers_are_async(self):
        self.assertTrue(inspect.iscoroutinefunction(FletCloudDriveApp.pick_files))
        self.assertTrue(inspect.iscoroutinefunction(FletCloudDriveApp.pick_cover))
        self.assertTrue(inspect.iscoroutinefunction(FletCloudDriveApp.pick_save_dir))

    def test_uses_current_flet_datarow_select_callback_name(self):
        source = Path("flet_app.py").read_text(encoding="utf-8")
        self.assertNotIn("on_select_changed", source)
        self.assertIn("on_select_change", source)

    def test_uses_current_flet_clipboard_api(self):
        source = Path("flet_app.py").read_text(encoding="utf-8")
        self.assertNotIn("set_clipboard", source)
        self.assertIn("page.clipboard.set", source)
        self.assertTrue(inspect.iscoroutinefunction(FletCloudDriveApp.copy_text))

    def test_copy_buttons_do_not_call_async_copy_from_sync_lambda(self):
        source = Path("flet_app.py").read_text(encoding="utf-8")
        self.assertNotIn("lambda e: self.copy_text", source)
        self.assertIn("copy_selected_item_share", source)
        self.assertIn("copy_history_url", source)

    def test_logging_is_in_memory_only(self):
        source = Path("flet_app.py").read_text(encoding="utf-8")
        self.assertNotIn("LOG_FILE", source)
        self.assertNotIn("LOG_DIR", source)
        self.assertNotIn("CHUNKS_DIR", source)
        self.assertNotIn("CACHE_DIR", source)
        self.assertIn("self.log_visible = True", source)
        self.assertIn("def append_log", source)
        self.assertIn("def render_log_panel", source)
        self.assertIn("def toggle_log_panel", source)
        self.assertIn("def copy_log_content", source)

    def test_default_chunk_size_is_100mb(self):
        from settings_store import AppSettings
        self.assertEqual(AppSettings().chunk_size_mb, 100)

    def test_settings_store_is_in_memory(self):
        from settings_store import SettingsStore
        store = SettingsStore()
        self.assertEqual(store.load_upload_history(), [])
        store.add_upload_history({"name": "test"})
        self.assertEqual(len(store.load_upload_history()), 1)
        store2 = SettingsStore()
        self.assertEqual(store2.load_upload_history(), [])


if __name__ == "__main__":
    unittest.main()
