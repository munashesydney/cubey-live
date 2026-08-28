"""
Unit tests for StartupPage and startup loading transitions.
"""

import time
import unittest
from unittest.mock import MagicMock
import customtkinter as ctk

from src.config import AppConfig
from src.gui.pages.startup_page import StartupPage
from src.gui.windows.app_window import GeminiLiveApp


class TestStartupPage(unittest.TestCase):
    """Test StartupPage widgets, progress updates, and completion transitions."""

    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.config = AppConfig()

    def tearDown(self):
        try:
            self.root.update()
            self.root.destroy()
        except Exception:
            pass

    def test_startup_page_initialization(self):
        """StartupPage should create title, progress bar, and minimal status labels."""
        page = StartupPage(self.root)
        page.pack()

        self.assertIsNotNone(page.title_label)
        self.assertIn("C U B E Y", page.title_label.cget("text"))
        self.assertIsNotNone(page.progress_bar)
        self.assertEqual(page.progress_bar.get(), 0.0)
        self.assertIsNotNone(page.status_label)
        page.destroy()

    def test_set_progress_updates_labels_and_bar(self):
        """set_progress should update progress bar value and status text."""
        page = StartupPage(self.root)
        page.pack()

        page.set_progress(0.45, "Testing progress update...", 1)
        self.assertAlmostEqual(page.progress_bar.get(), 0.45, places=2)
        self.assertEqual(page.status_label.cget("text"), "Testing progress update...")
        page.destroy()

    def test_complete_triggers_callback(self):
        """complete should invoke on_startup_complete callback after brief delay."""
        mock_cb = MagicMock()
        page = StartupPage(self.root, on_startup_complete=mock_cb)
        page.pack()

        page.complete()
        self.root.update()
        time.sleep(0.4)
        self.root.update()

        mock_cb.assert_called_once()
        page.destroy()

    def test_app_window_startup_transition(self):
        """GeminiLiveApp should show StartupPage initially and transition to RobotFacePage."""
        if hasattr(self, "root") and self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None

        mock_async_loop = MagicMock()
        mock_embedding = MagicMock()

        app = GeminiLiveApp(
            config=self.config,
            async_loop=mock_async_loop,
            on_start_session=MagicMock(),
            on_stop_session=MagicMock(),
            on_send_interruption=MagicMock(),
            embedding_service=mock_embedding,
            show_startup_screen=True,
        )

        self.assertIsNotNone(app.startup_page)
        app.update()
        self.assertTrue(app.startup_page.winfo_ismapped())

        # Post startup progress
        app.post_startup_progress(0.75, "Loading test step...", 3)
        app._drain_gui_events()
        app.update()
        self.assertAlmostEqual(app.startup_page.progress_bar.get(), 0.75, places=2)

        # Post startup complete -> should transition
        app.finish_startup()
        app.update()
        self.assertIsNone(app.startup_page)
        self.assertTrue(app.robot_face.winfo_ismapped())

        app.destroy()


if __name__ == "__main__":
    unittest.main()
