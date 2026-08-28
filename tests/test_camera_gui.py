"""
Unit tests for Camera GUI controls and LivePage integration.
"""

import unittest
from unittest.mock import MagicMock
import customtkinter as ctk

from src.camera.service import CameraService
from src.config import AppConfig
from src.gui.pages.live_page import LivePage
from src.gui.windows.developer_window import DeveloperWindow


class TestCameraGUI(unittest.TestCase):
    """Test camera UI controls in LivePage and DeveloperWindow."""

    def setUp(self):
        # Create a hidden CTk root for testing headless widgets
        self.root = ctk.CTk()
        self.root.withdraw()
        self.config = AppConfig(
            camera_device_index=0,
            camera_fps=30,
            camera_live_fps=1.0,
            camera_width=320,
            camera_height=240,
        )
        self.camera_service = CameraService(self.config)
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            try:
                page.destroy()
            except Exception:
                pass
        self.pages.clear()
        if self.camera_service.is_running:
            self.camera_service.stop()
        try:
            self.root.update()
            self.root.destroy()
        except Exception:
            pass

    def test_live_page_camera_widgets_initialization(self):
        """LivePage should initialize camera controls, preview viewport, and status badge."""
        mock_toggle_cam = MagicMock(return_value=True)
        mock_set_dev = MagicMock()
        mock_snapshot = MagicMock()

        page = LivePage(
            self.root,
            config=self.config,
            on_start_session=MagicMock(),
            on_stop_session=MagicMock(),
            on_send_interruption=MagicMock(),
            on_toggle_mute=MagicMock(),
            camera_service=self.camera_service,
            on_toggle_camera=mock_toggle_cam,
            on_set_camera_device=mock_set_dev,
            on_send_snapshot=mock_snapshot,
        )
        self.pages.append(page)

        self.assertIsNotNone(page.camera_switch)
        self.assertIsNotNone(page.camera_btn)
        self.assertIsNotNone(page.snapshot_btn)
        self.assertIsNotNone(page.vision_badge)
        self.assertIsNotNone(page.video_label)

        # Test toggle button click
        page._handle_camera_button_click()
        mock_toggle_cam.assert_called_once_with(True)

        # Test snapshot button click
        page._handle_snapshot_click()
        mock_snapshot.assert_called_once()

    def test_live_page_set_vision_state_updates_badge(self):
        """set_vision_state should update the visual badge."""
        page = LivePage(
            self.root,
            config=self.config,
            on_start_session=MagicMock(),
            on_stop_session=MagicMock(),
            on_send_interruption=MagicMock(),
            on_toggle_mute=MagicMock(),
            camera_service=self.camera_service,
        )
        self.pages.append(page)

        page.set_vision_state(True)
        self.assertTrue(page.is_camera_active)
        self.assertIn("PREVIEW ONLY", page.vision_badge.cget("text"))

        page.is_session_active = True
        page.set_vision_state(True)
        self.assertIn("STREAMING", page.vision_badge.cget("text"))

        page.set_vision_state(False)
        self.assertFalse(page.is_camera_active)
        self.assertIn("CAMERA OFF", page.vision_badge.cget("text"))

    def test_preview_loop_tab_visibility(self):
        """_render_preview_loop should skip rendering when camera tab is not active."""
        page = LivePage(
            self.root,
            config=self.config,
            on_start_session=MagicMock(),
            on_stop_session=MagicMock(),
            on_send_interruption=MagicMock(),
            on_toggle_mute=MagicMock(),
            camera_service=self.camera_service,
        )
        self.pages.append(page)
        self.camera_service.start()
        page.set_vision_state(True)

        # Tab is on Camera Vision initially -> render loop should configure label
        page.right_panel.set("👁️ Camera Vision")
        page._render_preview_loop()
        self.assertIsNotNone(page.video_label.cget("image"))

        # Switch tab to Live Transcript -> render loop should skip updating
        page.right_panel.set("💬 Live Transcript")
        # Should not raise and should cleanly return
        page._render_preview_loop()


if __name__ == "__main__":
    unittest.main()
