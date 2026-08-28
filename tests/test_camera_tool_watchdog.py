"""
Unit tests for Camera Tool timer expiration text interrupt and silence watchdog vision suppression.
"""

import asyncio
import time
import unittest
from unittest.mock import MagicMock, AsyncMock

from src.client.tools.camera.execute import (
    execute_camera_tool,
    stop_active_camera_timer,
    _on_camera_timer_expired,
)


class TestCameraToolWatchdog(unittest.TestCase):
    """Verify camera auto-off timer expiration and text interrupt prompt dispatch."""

    def tearDown(self):
        stop_active_camera_timer()

    def test_camera_timer_expiry_dispatches_text_interrupt(self):
        """When the camera timer expires, it should turn off the camera and send a text interrupt to Gemini Live."""
        mock_toggle = MagicMock()
        mock_camera_svc = MagicMock()
        mock_live_client = MagicMock()
        mock_live_client.is_connected = True
        mock_live_client.interrupt_with_text = AsyncMock()

        # Set a mock event loop
        loop = asyncio.new_event_loop()
        mock_live_client._loop = loop

        # Trigger expiry
        _on_camera_timer_expired(
            on_toggle_camera=mock_toggle,
            camera_service=mock_camera_svc,
            live_client=mock_live_client,
        )

        # Verify camera turned off
        mock_toggle.assert_called_once_with(False)

        # Run loop tasks to drain the scheduled coroutine
        loop.run_until_complete(asyncio.sleep(0.01))
        mock_live_client.interrupt_with_text.assert_called_once()
        sent_prompt = mock_live_client.interrupt_with_text.call_args[0][0]
        self.assertIn("The camera observation feed has ended", sent_prompt)
        self.assertIn("report and summarize what you observed", sent_prompt)

        loop.close()

    def test_execute_camera_tool_starts_timer(self):
        """execute_camera_tool(action='turn_on') should activate camera and start timer."""
        mock_toggle = MagicMock()
        mock_camera_svc = MagicMock()
        mock_live_client = MagicMock()
        mock_live_client.is_connected = True

        res = execute_camera_tool(
            action="turn_on",
            duration_seconds=15,
            on_toggle_camera=mock_toggle,
            camera_service=mock_camera_svc,
            live_client=mock_live_client,
        )

        self.assertEqual(res.get("status"), "camera_activated")
        self.assertEqual(res.get("duration_seconds"), 15)
        mock_toggle.assert_called_once_with(True)

    def test_inactivity_watchdog_suppressed_during_camera_streaming(self):
        """Inactivity watchdog should not time out while camera streaming is active."""
        from src.config import AppConfig
        from src.client.live_client import GeminiLiveClient

        config = AppConfig()
        config.live_inactivity_timeout_seconds = 0.05

        mock_recorder = MagicMock()
        mock_player = MagicMock()
        mock_player.is_speaking = False

        client = GeminiLiveClient(
            config=config,
            recorder=mock_recorder,
            player=mock_player,
        )
        client.is_connected = True
        client.stop_session = MagicMock()
        client._is_camera_streaming = True
        client._model_turn_active = False

        loop = asyncio.new_event_loop()

        async def run_test():
            watchdog = asyncio.create_task(client._inactivity_watchdog_loop())
            # Wait longer than timeout
            await asyncio.sleep(0.12)
            # Stop session should NOT have been called because camera streaming is active
            client.stop_session.assert_not_called()

            # Now disable camera streaming
            client._is_camera_streaming = False
            await asyncio.sleep(0.12)
            # Now stop session SHOULD have been called
            client.stop_session.assert_called_once()
            client._stop_event.set()
            await watchdog

        loop.run_until_complete(run_test())
        loop.close()


if __name__ == "__main__":
    unittest.main()
