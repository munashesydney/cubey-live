"""Deterministic tests for the latency-critical audio path (no hardware/API)."""

import asyncio
import unittest
from unittest.mock import patch

import numpy as np
from google.genai import types as genai_types

from src.audio.player import AudioPlayer
from src.audio.recorder import AudioRecorder
from src.audio.resample import resample_pcm16
from src.client.live_client import GeminiLiveClient
from src.config import AppConfig


class RecorderQueueTests(unittest.TestCase):
    def test_bounded_queue_keeps_the_freshest_audio(self) -> None:
        recorder = AudioRecorder(
            sample_rate=16_000,
            chunk_size=320,
            max_queue_ms=40,
        )
        recorder.is_recording = True

        recorder._enqueue_latest(b"oldest")
        recorder._enqueue_latest(b"middle")
        recorder._enqueue_latest(b"newest")

        self.assertEqual(recorder.audio_queue.qsize(), 2)
        self.assertEqual(recorder.audio_queue.get_nowait(), b"middle")
        self.assertEqual(recorder.audio_queue.get_nowait(), b"newest")

    def test_native_48k_callbacks_aggregate_into_20ms_gemini_packets(self) -> None:
        class ImmediateLoop:
            def call_soon_threadsafe(self, callback, *args):
                callback(*args)

        recorder = AudioRecorder(
            sample_rate=16_000,
            chunk_size=320,
            device_sample_rate=48_000,
        )
        recorder._loop = ImmediateLoop()
        recorder.is_recording = True
        native_10ms = np.zeros(480, dtype=np.int16).tobytes()

        recorder._audio_callback(native_10ms, 480, {}, None)
        self.assertEqual(recorder.audio_queue.qsize(), 0)
        recorder._audio_callback(native_10ms, 480, {}, None)

        self.assertEqual(recorder.audio_queue.qsize(), 1)
        self.assertEqual(len(recorder.audio_queue.get_nowait()), 320 * 2)


class ResamplerTests(unittest.TestCase):
    def test_downsamples_48k_device_frame_to_16k_packet(self) -> None:
        source = np.repeat(np.arange(320, dtype=np.int16), 3).tobytes()
        output = np.frombuffer(resample_pcm16(source, 48_000, 16_000), np.int16)

        np.testing.assert_array_equal(output, np.arange(320, dtype=np.int16))

    def test_upsamples_24k_output_to_48k_device_rate(self) -> None:
        source = np.array([-32768, 0, 32767], dtype=np.int16).tobytes()
        output = np.frombuffer(resample_pcm16(source, 24_000, 48_000), np.int16)

        self.assertEqual(len(output), 6)
        self.assertEqual(output[0], -32768)
        self.assertAlmostEqual(int(output[1]), -16384, delta=1)
        self.assertEqual(output[2], 0)
        self.assertAlmostEqual(int(output[3]), 16383, delta=1)


class PlayerBufferTests(unittest.TestCase):
    def test_callback_streams_audio_and_zero_fills_underflow(self) -> None:
        player = AudioPlayer(sample_rate=1000, block_size=4, max_buffer_ms=20)
        player.is_playing = True
        player.play_chunk(b"\x01\x00\x02\x00")

        output = bytearray(8)
        player._audio_callback(output, 4, None, None)

        self.assertEqual(output, b"\x01\x00\x02\x00\x00\x00\x00\x00")
        self.assertEqual(player._buffered_bytes, 0)

    def test_server_audio_burst_is_preserved_without_frame_drops(self) -> None:
        player = AudioPlayer(sample_rate=1000, block_size=2, max_buffer_ms=2)
        player.is_playing = True
        player.play_chunk(b"\x01\x00\x02\x00\x03\x00\x04\x00")

        first = bytearray(4)
        second = bytearray(4)
        player._audio_callback(first, 2, None, None)
        player._audio_callback(second, 2, None, None)

        self.assertEqual(first, b"\x01\x00\x02\x00")
        self.assertEqual(second, b"\x03\x00\x04\x00")
        self.assertEqual(player._buffered_bytes, 0)

    def test_clear_immediately_resets_speaking_state(self) -> None:
        player = AudioPlayer()
        player.is_playing = True
        player.play_chunk(b"\x01\x00")
        self.assertTrue(player.is_speaking)

        player.clear()

        self.assertFalse(player.is_speaking)


class _FakePlayer:
    def __init__(self) -> None:
        self.speaking = True
        self.clear_count = 0

    @property
    def is_speaking(self) -> bool:
        return self.speaking

    def clear(self) -> None:
        self.speaking = False
        self.clear_count += 1


class _FakeSession:
    def __init__(self, client: GeminiLiveClient, expected: int) -> None:
        self.client = client
        self.expected = expected
        self.audio = []

    async def send_realtime_input(self, *, audio) -> None:
        self.audio.append(audio)
        if len(self.audio) == self.expected:
            self.client.is_connected = False


class LiveSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_barge_in_sends_preroll_with_explicit_sample_rate(self) -> None:
        recorder = AudioRecorder(chunk_size=320, max_queue_ms=240)
        player = _FakePlayer()
        config = AppConfig(
            api_key="test",
            client_vad_enabled=False,
            interruption_rms_threshold=0.04,
            interruption_preroll_ms=120,
        )
        with patch("src.client.live_client.genai.Client"):
            client = GeminiLiveClient(config, recorder, player)

        quiet = np.full(320, 200, dtype=np.int16).tobytes()
        loud = np.full(320, 5000, dtype=np.int16).tobytes()
        for chunk in (quiet, quiet, loud):
            recorder.audio_queue.put_nowait(chunk)

        session = _FakeSession(client, expected=3)
        client._session = session
        client.is_connected = True
        await asyncio.wait_for(client._send_audio_loop(), timeout=1)

        self.assertEqual([blob.data for blob in session.audio], [quiet, quiet, loud])
        self.assertTrue(
            all(blob.mime_type == "audio/pcm;rate=16000" for blob in session.audio)
        )
        self.assertEqual(player.clear_count, 1)
        self.assertEqual(recorder.audio_queue._unfinished_tasks, 0)

    async def test_client_vad_closes_short_utterance_after_silence(self) -> None:
        class VadSession:
            def __init__(self, client):
                self.client = client
                self.events = []

            async def send_realtime_input(
                self, *, audio=None, activity_start=None, activity_end=None
            ) -> None:
                if activity_start is not None:
                    self.events.append(("start", None))
                elif activity_end is not None:
                    self.events.append(("end", None))
                    self.client.is_connected = False
                elif audio is not None:
                    self.events.append(("audio", audio))

        recorder = AudioRecorder(chunk_size=320, max_queue_ms=240)
        player = _FakePlayer()
        player.speaking = False
        config = AppConfig(
            api_key="test",
            client_vad_enabled=True,
            client_vad_rms_threshold=0.025,
            client_vad_min_speech_ms=40,
            client_vad_silence_ms=60,
            interruption_preroll_ms=40,
        )
        with patch("src.client.live_client.genai.Client"):
            client = GeminiLiveClient(config, recorder, player)

        quiet = np.full(320, 100, dtype=np.int16).tobytes()
        speech = np.full(320, 5000, dtype=np.int16).tobytes()
        for chunk in (quiet, speech, speech, quiet, quiet, quiet):
            recorder.audio_queue.put_nowait(chunk)

        session = VadSession(client)
        client._session = session
        client.is_connected = True
        await asyncio.wait_for(client._send_audio_loop(), timeout=1)

        self.assertEqual(
            [kind for kind, _ in session.events],
            ["start", "audio", "audio", "audio", "audio", "audio", "end"],
        )
        sent_audio = [blob.data for kind, blob in session.events if kind == "audio"]
        self.assertEqual(sent_audio, [speech, speech, quiet, quiet, quiet])
        self.assertEqual(recorder.audio_queue._unfinished_tasks, 0)


class LiveReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_1011_reconnects_without_ending_logical_conversation(self) -> None:
        class InternalServerError(Exception):
            code = 1011

        ended = []
        statuses = []
        recorder = AudioRecorder()
        player = AudioPlayer()
        config = AppConfig(
            api_key="test",
            live_reconnect_attempts=2,
            live_reconnect_base_delay=0,
            live_reconnect_max_delay=0,
        )
        with patch("src.client.live_client.genai.Client"):
            client = GeminiLiveClient(
                config,
                recorder,
                player,
                on_status_change=statuses.append,
                on_session_ended=lambda: ended.append(True),
            )

        attempts = 0

        async def fake_connection() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.assertEqual(ended, [])
                raise InternalServerError("1011 Internal error encountered")
            self.assertEqual(ended, [])
            client._stop_event.set()

        client._run_live_connection = fake_connection
        await client.start_session()
        await asyncio.sleep(0)

        self.assertEqual(attempts, 2)
        self.assertEqual(ended, [True])
        self.assertTrue(any(status.startswith("Reconnecting") for status in statuses))

    def test_reconnect_config_uses_latest_resumption_handle(self) -> None:
        recorder = AudioRecorder()
        player = AudioPlayer()
        with patch("src.client.live_client.genai.Client"):
            client = GeminiLiveClient(AppConfig(api_key="test"), recorder, player)

        client._session_resumption_handle = "resume-token"
        live_config = client._build_live_config()

        self.assertEqual(live_config.session_resumption.handle, "resume-token")

    async def test_receive_loop_retains_server_resumption_update(self) -> None:
        recorder = AudioRecorder()
        player = AudioPlayer()
        with patch("src.client.live_client.genai.Client"):
            client = GeminiLiveClient(AppConfig(api_key="test"), recorder, player)

        class ResumptionSession:
            async def receive(self):
                yield genai_types.LiveServerMessage(
                    session_resumption_update={
                        "resumable": True,
                        "new_handle": "new-resume-token",
                    }
                )
                client.is_connected = False

        client._session = ResumptionSession()
        client.is_connected = True
        await client._receive_responses_loop()

        self.assertEqual(client._session_resumption_handle, "new-resume-token")


if __name__ == "__main__":
    unittest.main()
