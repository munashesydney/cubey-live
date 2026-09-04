"""
Unit tests for DeveloperWindow lazy loading, page lifecycle, and performance optimizations.
"""

import unittest
from unittest.mock import MagicMock
import customtkinter as ctk

from src.config import AppConfig
from src.gui.windows.developer_window import DeveloperWindow


class TestDeveloperWindow(unittest.TestCase):
    """Test DeveloperWindow lazy instantiation, tab lifecycle, and clean teardown."""

    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.config = AppConfig()
        self.embedding_service = MagicMock()
        self.camera_service = MagicMock()
        self.camera_service.is_running = False

    def tearDown(self):
        try:
            self.root.update()
            self.root.destroy()
        except Exception:
            pass

    def _create_dev_window(self, on_close=None) -> DeveloperWindow:
        win = DeveloperWindow(
            master=self.root,
            config=self.config,
            on_start_session=MagicMock(),
            on_stop_session=MagicMock(),
            on_send_interruption=MagicMock(),
            on_toggle_mute=MagicMock(),
            embedding_service=self.embedding_service,
            is_session_active=False,
            camera_service=self.camera_service,
            on_close=on_close,
        )
        win.withdraw()
        return win

    def test_lazy_loading_only_creates_initial_tab(self):
        """Only the default active tab (Home) should be instantiated on launch."""
        win = self._create_dev_window()
        self.root.update()

        # Check pages dictionary — only 'home' should exist
        self.assertIn("home", win.pages)
        self.assertIsNotNone(win.pages["home"])

        # Other heavy pages (wheels, lidar, live, tasks, memories) should NOT be built yet
        self.assertNotIn("wheels", win.pages)
        self.assertNotIn("lidar", win.pages)
        self.assertNotIn("live", win.pages)
        self.assertNotIn("tasks", win.pages)
        self.assertNotIn("memories", win.pages)

        win.destroy()

    def test_tab_switch_triggers_lazy_instantiation_and_lifecycle(self):
        """Switching to an uninstantiated tab should build it and trigger on_activate."""
        win = self._create_dev_window()
        self.root.update()

        home_page = win.pages["home"]

        # Switch to wheels
        win.show_page("wheels")
        self.root.update()

        self.assertIn("wheels", win.pages)
        wheels_page = win.pages["wheels"]
        self.assertIsNotNone(wheels_page)
        self.assertTrue(getattr(wheels_page, "_is_active", False))

        # Switch to live
        win.show_page("live")
        self.root.update()

        self.assertIn("live", win.pages)
        live_page = win.pages["live"]
        self.assertTrue(getattr(live_page, "_is_active", False))
        self.assertFalse(getattr(wheels_page, "_is_active", True))

        win.destroy()

    def test_clean_destruction_triggers_on_close_and_destroys_pages(self):
        """Destroying the window should invoke on_close and deactivate all pages."""
        on_close_mock = MagicMock()
        win = self._create_dev_window(on_close=on_close_mock)
        self.root.update()

        win.destroy()
        self.root.update()

        on_close_mock.assert_called_once()
        self.assertFalse(win.winfo_exists())
