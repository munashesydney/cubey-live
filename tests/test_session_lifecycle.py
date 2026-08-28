"""
Unit tests for Gemini Live session lifecycle:
- Wake-up text interrupt on start
- Speaking-aware microphone animation visibility (hidden when model speaks, restored when idle)
- Inactivity / silence auto-close watchdog (10s timeout)
"""

import asyncio
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

for mod_name in ["sounddevice", "numpy", "google", "google.genai", "google.genai.types"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

from src.config import AppConfig
from src.client.live_client import GeminiLiveClient


class TestSessionLifecycle(unittest.TestCase):

    def setUp(self):
        self.config = AppConfig(
            api_key="test-api-key",
            live_inactivity_timeout_seconds=0.2,  # Short timeout for fast unit tests
        )
        self.recorder = MagicMock()
        self.recorder.audio_queue = asyncio.Queue()
        self.recorder.is_recording = True

        self.player = MagicMock()
        self.player.is_playing = True
        self.player.is_speaking = False

        self.status_cb = MagicMock()
        self.transcript_cb = MagicMock()
        self.listening_state_cb = MagicMock()
        self.session_ended_cb = MagicMock()

        self.client = GeminiLiveClient(
            config=self.config,
            recorder=self.recorder,
            player=self.player,
            on_status_change=self.status_cb,
            on_transcript=self.transcript_cb,
            on_listening_state_change=self.listening_state_cb,
            on_session_ended=self.session_ended_cb,
        )

    def test_initial_interrupt_stored_in_start_session(self):
        """Verify initial_interrupt parameter is stored and listening state is set True."""
        loop = asyncio.new_event_loop()
        try:
            async def run_test():
                self.client._loop = loop
                # Mock _run_live_connection to stop loop cleanly without network
                async def mock_run():
                    self.client._stop_event.set()
                self.client._run_live_connection = mock_run
                await self.client.start_session(
                    initial_interrupt="[WAKE UP - USER SAID 'HEY CUBEY']"
                )
                self.assertEqual(
                    self.client._pending_initial_interrupt,
                    "[WAKE UP - USER SAID 'HEY CUBEY']",
                )
                self.assertTrue(self.client._is_listening_visible)
                self.listening_state_cb.assert_called_with(True)

            loop.run_until_complete(run_test())
        finally:
            loop.close()

    def test_listening_state_toggle_callback(self):
        """Verify set_listening_state only dispatches when visibility changes."""
        self.client._is_listening_visible = True
        self.listening_state_cb.reset_mock()

        # Setting True when already True does not re-dispatch
        self.client.set_listening_state(True)
        self.listening_state_cb.assert_not_called()

        # Setting False dispatches False (model speaking)
        self.client.set_listening_state(False)
        self.assertFalse(self.client._is_listening_visible)
        self.listening_state_cb.assert_called_once_with(False)

        # Setting True dispatches True (model finished)
        self.listening_state_cb.reset_mock()
        self.client.set_listening_state(True)
        self.assertTrue(self.client._is_listening_visible)
        self.listening_state_cb.assert_called_once_with(True)

    def test_inactivity_watchdog_stops_session_on_silence(self):
        """Verify watchdog auto-closes session when silence exceeds timeout."""
        loop = asyncio.new_event_loop()
        try:
            async def run_test():
                self.client._loop = loop
                self.client.is_connected = True
                self.client._stop_event.clear()
                self.client._last_user_activity_at = time.monotonic()
                self.client._model_turn_active = False
                self.player.is_speaking = False

                # Run watchdog task with 0.2s timeout
                watchdog_task = asyncio.create_task(self.client._inactivity_watchdog_loop())

                await asyncio.sleep(0.35)
                self.assertTrue(self.client._stop_event.is_set())
                self.status_cb.assert_called_with("Idle (10s Silence Timeout) 💤")

                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)

            loop.run_until_complete(run_test())
        finally:
            loop.close()

    def test_inactivity_watchdog_resets_on_user_activity(self):
        """Verify user activity (speech / mic input) resets the watchdog timer."""
        loop = asyncio.new_event_loop()
        try:
            async def run_test():
                self.client._loop = loop
                self.client.is_connected = True
                self.client._stop_event.clear()
                self.client._last_user_activity_at = time.monotonic()
                self.client._model_turn_active = False
                self.player.is_speaking = False

                watchdog_task = asyncio.create_task(self.client._inactivity_watchdog_loop())

                # At 0.12s, user speaks -> record_user_activity
                await asyncio.sleep(0.12)
                self.client.record_user_activity()
                self.assertFalse(self.client._stop_event.is_set())

                # At 0.24s (total from start), still alive because timer was reset at 0.12s
                await asyncio.sleep(0.12)
                self.assertFalse(self.client._stop_event.is_set())

                # Wait another 0.25s for timeout to finally trigger
                await asyncio.sleep(0.25)
                self.assertTrue(self.client._stop_event.is_set())

                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)

            loop.run_until_complete(run_test())
        finally:
            loop.close()

    def test_inactivity_watchdog_paused_while_model_speaking(self):
        """Verify watchdog does not time out while Cubey (model) is speaking."""
        loop = asyncio.new_event_loop()
        try:
            async def run_test():
                self.client._loop = loop
                self.client.is_connected = True
                self.client._stop_event.clear()
                self.client._last_user_activity_at = time.monotonic()
                self.client._model_turn_active = True
                self.player.is_speaking = True

                watchdog_task = asyncio.create_task(self.client._inactivity_watchdog_loop())

                # Sleep 0.35s while model is speaking -> should NOT stop
                await asyncio.sleep(0.35)
                self.assertFalse(self.client._stop_event.is_set())

                # Model finishes speaking
                self.client._model_turn_active = False
                self.player.is_speaking = False

                # Now silence countdown proceeds
                await asyncio.sleep(0.35)
                self.assertTrue(self.client._stop_event.is_set())

                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)

            loop.run_until_complete(run_test())
        finally:
            loop.close()

    def test_wait_for_playback_done_restores_listening(self):
        """Verify _wait_for_playback_done_and_restore_listening waits for audio drain."""
        loop = asyncio.new_event_loop()
        try:
            async def run_test():
                self.client._loop = loop
                self.client.is_connected = True
                self.client._stop_event.clear()
                self.client._model_turn_active = False
                self.client._is_listening_visible = False
                self.player.is_speaking = True

                waiter_task = asyncio.create_task(
                    self.client._wait_for_playback_done_and_restore_listening()
                )

                await asyncio.sleep(0.08)
                # Still speaking -> listening should remain False
                self.assertFalse(self.client._is_listening_visible)

                # Playback finishes
                self.player.is_speaking = False
                await asyncio.sleep(0.1)

                # Listening should be restored to True
                self.assertTrue(self.client._is_listening_visible)
                self.listening_state_cb.assert_called_with(True)

                waiter_task.cancel()
                await asyncio.gather(waiter_task, return_exceptions=True)

            loop.run_until_complete(run_test())
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
