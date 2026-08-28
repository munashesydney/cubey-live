"""
Unit tests for Gemini Live real-time camera streaming & snapshot dispatching.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import types
from PIL import Image

from src.audio.player import AudioPlayer
from src.audio.recorder import AudioRecorder
from src.camera.service import CameraService
from src.client.live_client import GeminiLiveClient
from src.config import AppConfig


class TestCameraLiveStream(unittest.IsolatedAsyncioTestCase):
    """Test GeminiLiveClient camera streaming integration and visual snapshots."""

    def setUp(self):
        self.config = AppConfig(
            api_key="test_key",
            camera_device_index=0,
            camera_fps=30,
            camera_live_fps=2.0,
            camera_width=320,
            camera_height=240,
            camera_jpeg_quality=75,
            camera_auto_start_with_live=True,
        )
        self.recorder = MagicMock(spec=AudioRecorder)
        self.player = MagicMock(spec=AudioPlayer)
        self.camera_service = CameraService(self.config)
        self.client = GeminiLiveClient(
            config=self.config,
            recorder=self.recorder,
            player=self.player,
            camera_service=self.camera_service,
        )

    def tearDown(self):
        if self.camera_service.is_running:
            self.camera_service.stop()

    def test_camera_streaming_property_and_toggle(self):
        """Camera streaming toggle should update state."""
        self.assertTrue(self.client.is_camera_streaming)
        self.client.set_camera_streaming(False)
        self.assertFalse(self.client.is_camera_streaming)
        self.client.set_camera_streaming(True)
        self.assertTrue(self.client.is_camera_streaming)

    async def test_send_visual_snapshot_dispatches_blob_and_prompt(self):
        """send_visual_snapshot should send realtime input blob and client content prompt."""
        mock_session = AsyncMock()
        self.client.is_connected = True
        self.client._session = mock_session

        fake_jpeg = b"\xff\xd8fake_jpeg_content\xff\xd9"
        custom_prompt = "[LOOK]: Check out this object!"

        await self.client.send_visual_snapshot(fake_jpeg, prompt=custom_prompt)

        # 1. Verify send_realtime_input was called with the JPEG blob
        mock_session.send_realtime_input.assert_awaited_once()
        call_kwargs = mock_session.send_realtime_input.call_args.kwargs
        self.assertIn("media", call_kwargs)
        media_blob = call_kwargs["media"]
        self.assertEqual(media_blob.mime_type, "image/jpeg")
        self.assertEqual(media_blob.data, fake_jpeg)

        # 2. Verify send_client_content was called with prompt text
        mock_session.send_client_content.assert_awaited_once()
        content_call = mock_session.send_client_content.call_args.kwargs
        self.assertIn("turns", content_call)
        self.assertEqual(content_call["turn_complete"], True)
        turns = content_call["turns"]
        self.assertEqual(turns.role, "user")
        self.assertEqual(turns.parts[0].text, custom_prompt)

    async def test_send_video_loop_streams_camera_frames(self):
        """_send_video_loop should fetch JPEG frames and send them via send_realtime_input."""
        self.camera_service.start()
        mock_session = AsyncMock()
        self.client.is_connected = True
        self.client._session = mock_session
        self.client.set_camera_streaming(True)

        # Run _send_video_loop in a short-lived task
        video_task = asyncio.create_task(self.client._send_video_loop())

        # Allow loop to execute for a short interval
        await asyncio.sleep(0.6)

        # Cancel and wait for task
        video_task.cancel()
        try:
            await video_task
        except asyncio.CancelledError:
            pass

        # Verify that send_realtime_input was invoked with image/jpeg
        self.assertGreater(mock_session.send_realtime_input.await_count, 0)
        call_kwargs = mock_session.send_realtime_input.call_args.kwargs
        self.assertIn("media", call_kwargs)
        self.assertEqual(call_kwargs["media"].mime_type, "image/jpeg")
        self.assertEqual(call_kwargs["media"].data[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
