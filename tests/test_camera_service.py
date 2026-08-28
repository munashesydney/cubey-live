"""
Unit tests for CameraService and device discovery.
"""

import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image

from src.camera.devices import list_camera_devices
from src.camera.service import CameraService
from src.config import AppConfig


class TestCameraService(unittest.TestCase):
    """Test CameraService lifecycle, frame retrieval, and listeners."""

    def setUp(self):
        self.config = AppConfig(
            camera_device_index=0,
            camera_fps=30,
            camera_live_fps=1.0,
            camera_width=320,
            camera_height=240,
            camera_jpeg_quality=75,
        )
        self.service = CameraService(self.config)

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()

    def test_initial_state(self):
        """Service should be idle before start."""
        self.assertFalse(self.service.is_running)
        self.assertEqual(self.service.device_index, 0)
        self.assertEqual(self.service.width, 320)
        self.assertEqual(self.service.height, 240)
        self.assertIsNone(self.service.get_latest_frame_pil())
        self.assertIsNone(self.service.get_latest_frame_jpeg())

    def test_start_and_stop_lifecycle(self):
        """Starting and stopping camera service should manage thread and buffers cleanly."""
        started = self.service.start()
        self.assertTrue(started)
        self.assertTrue(self.service.is_running)

        # Allow worker thread to generate at least one frame
        time.sleep(0.15)

        pil_frame = self.service.get_latest_frame_pil()
        self.assertIsNotNone(pil_frame)
        self.assertIsInstance(pil_frame, Image.Image)
        self.assertEqual(pil_frame.size, (320, 240))

        jpeg_bytes = self.service.get_latest_frame_jpeg()
        self.assertIsNotNone(jpeg_bytes)
        self.assertTrue(len(jpeg_bytes) > 100)
        # Verify JPEG header magic bytes (0xFF, 0xD8)
        self.assertEqual(jpeg_bytes[:2], b"\xff\xd8")

        self.service.stop()
        self.assertFalse(self.service.is_running)
        self.assertIsNone(self.service.get_latest_frame_pil())

    def test_preview_listener_callback(self):
        """Registered preview listeners should receive PIL frames."""
        received_frames = []

        def on_frame(img: Image.Image):
            received_frames.append(img)

        self.service.add_preview_listener(on_frame)
        self.service.start()

        time.sleep(0.15)
        self.service.stop()

        self.service.remove_preview_listener(on_frame)
        self.assertGreater(len(received_frames), 0)
        self.assertIsInstance(received_frames[0], Image.Image)

    def test_capture_snapshot(self):
        """Snapshot should return both PIL Image and JPEG byte buffer."""
        self.service.start()
        time.sleep(0.15)

        snapshot = self.service.capture_snapshot()
        self.assertIsNotNone(snapshot)
        pil_img, jpeg_data = snapshot
        self.assertIsInstance(pil_img, Image.Image)
        self.assertIsInstance(jpeg_data, bytes)
        self.assertEqual(jpeg_data[:2], b"\xff\xd8")
        self.service.stop()

    def test_set_device(self):
        """Setting device index should update configuration."""
        self.service.set_device(1)
        self.assertEqual(self.service.device_index, 1)

    def test_list_camera_devices_returns_list(self):
        """Device enumeration should return a list of dictionaries."""
        devices = list_camera_devices(max_devices=2)
        self.assertIsInstance(devices, list)

    def test_picamera2_backend_initialization_and_capture(self):
        """Picamera2 backend should configure and stream BGR frames directly."""
        mock_picam = MagicMock()
        fake_bgr = np.zeros((240, 320, 3), dtype=np.uint8)
        mock_picam.capture_array.return_value = fake_bgr

        with patch("src.camera.service.Picamera2", return_value=mock_picam), \
             patch("platform.system", return_value="Linux"):
            service = CameraService(self.config)
            started = service.start()
            self.assertTrue(started)
            time.sleep(0.1)

            self.assertEqual(service.active_backend, "picamera2")
            mock_picam.create_preview_configuration.assert_called_once()
            mock_picam.start.assert_called_once()

            pil_frame = service.get_latest_frame_pil()
            self.assertIsNotNone(pil_frame)
            self.assertEqual(pil_frame.size, (320, 240))

            service.stop()
            mock_picam.stop.assert_called_once()
            mock_picam.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
