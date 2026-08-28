"""
Unit tests for WakeWordService and Sherpa-ONNX configuration.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.wake_word import WakeWordService


class TestWakeWordService(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.model_dir = Path(self.temp_dir) / "models" / "sherpa-onnx-kws"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_wake_words_string(self):
        """String of comma-separated wake words is parsed and normalized to uppercase."""
        words_str = "hey cubey, wake up , ok robot"
        parsed = WakeWordService._parse_wake_words(words_str)
        self.assertEqual(parsed, ["HEY CUBEY", "WAKE UP", "OK ROBOT"])

    def test_parse_wake_words_list(self):
        """List of wake words is normalized."""
        words_list = ["hey cubey", "CUBEY", "  hello  "]
        parsed = WakeWordService._parse_wake_words(words_list)
        self.assertEqual(parsed, ["HEY CUBEY", "CUBEY", "HELLO"])

    def test_generate_keywords_file(self):
        """keywords.txt is correctly formatted with scores and thresholds."""
        service = WakeWordService(
            model_dir=self.model_dir,
            wake_words=["HEY CUBEY", "WAKE UP"],
            threshold=0.35,
            score=2.2,
            auto_download=False,
        )
        self.model_dir.mkdir(parents=True, exist_ok=True)
        kw_file = service._generate_keywords_file()

        self.assertTrue(kw_file.is_file())
        content = kw_file.read_text(encoding="utf-8")
        expected = "HEY CUBEY :2.2 #0.35\nWAKE UP :2.2 #0.35\n"
        self.assertEqual(content, expected)

    def test_pause_and_resume_lifecycle(self):
        """Pausing and resuming toggles state and clears queue."""
        service = WakeWordService(
            model_dir=self.model_dir,
            auto_download=False,
        )
        service._is_running = True
        service._is_paused = False

        service.pause()
        self.assertTrue(service._is_paused)

        service.resume()
        self.assertFalse(service._is_paused)

    def test_audio_chunk_ingestion(self):
        """Audio chunks are enqueued when running and unpaused."""
        service = WakeWordService(
            model_dir=self.model_dir,
            auto_download=False,
        )
        service._is_running = True
        service._is_paused = False

        dummy_pcm = b"\x00\x01" * 160
        service.process_audio_chunk(dummy_pcm)
        self.assertEqual(service._audio_queue.qsize(), 1)

        # When paused, chunks should be ignored
        service.pause()
        service.process_audio_chunk(dummy_pcm)
        self.assertEqual(service._audio_queue.qsize(), 0)

    def test_dispatch_detection_callback(self):
        """on_wake_word callback is called with recognized keyword."""
        detected = []
        service = WakeWordService(
            model_dir=self.model_dir,
            on_wake_word=lambda kw: detected.append(kw),
            auto_download=False,
        )
        service._dispatch_detection("HEY CUBEY")
        self.assertEqual(detected, ["HEY CUBEY"])


if __name__ == "__main__":
    unittest.main()
