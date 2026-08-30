"""Unit tests for the custom openWakeWord runtime service."""

import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from src.services.wake_word import (
    DEFAULT_EMBEDDING_MODEL_PATH,
    DEFAULT_MELSPEC_MODEL_PATH,
    DEFAULT_MODEL_PATH,
    WakeWordService,
)


class TestWakeWordService(unittest.TestCase):
    def test_bundled_model_assets_exist(self) -> None:
        for path in (
            DEFAULT_MODEL_PATH,
            DEFAULT_MELSPEC_MODEL_PATH,
            DEFAULT_EMBEDDING_MODEL_PATH,
        ):
            self.assertTrue(path.is_file(), path)

    def test_initialize_passes_explicit_onnx_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / name for name in ("wake.onnx", "mel.onnx", "embed.onnx")]
            for path in paths:
                path.touch()

            fake_model = MagicMock()
            model_module = types.ModuleType("openwakeword.model")
            model_module.Model = fake_model
            package_module = types.ModuleType("openwakeword")
            package_module.__path__ = []
            service = WakeWordService(
                model_path=paths[0],
                melspec_model_path=paths[1],
                embedding_model_path=paths[2],
            )
            with patch.dict(
                sys.modules,
                {"openwakeword": package_module, "openwakeword.model": model_module},
            ):
                self.assertTrue(service.initialize())

            fake_model.assert_called_once_with(
                wakeword_models=[str(paths[0])],
                inference_framework="onnx",
                melspec_model_path=str(paths[1]),
                embedding_model_path=str(paths[2]),
            )

    def test_missing_model_fails_initialization(self) -> None:
        service = WakeWordService(model_path="does-not-exist.onnx")
        self.assertFalse(service.initialize())

    def test_pause_resume_clears_audio_and_resets_model(self) -> None:
        service = WakeWordService()
        service.model = MagicMock()
        service._is_running = True
        service.process_audio_chunk(b"\x00\x00" * 320)

        service.pause()
        self.assertTrue(service._is_paused)
        self.assertEqual(service._audio_queue.qsize(), 0)
        service.resume()
        self.assertFalse(service._is_paused)
        self.assertEqual(service.model.reset.call_count, 1)

    def test_queue_drops_stale_audio_without_blocking(self) -> None:
        service = WakeWordService()
        service.model = MagicMock()
        service._is_running = True
        for _ in range(25):
            service.process_audio_chunk(b"\x00\x00" * 320)
        self.assertEqual(service._audio_queue.qsize(), 20)
        self.assertEqual(service._dropped_chunks, 5)

    def test_worker_dispatches_cubey_above_threshold(self) -> None:
        detected: list[str] = []
        event = threading.Event()

        def on_wake_word(keyword: str) -> None:
            detected.append(keyword)
            event.set()

        service = WakeWordService(threshold=0.5, on_wake_word=on_wake_word)
        service.model = MagicMock()
        service.model.predict.return_value = {service._model_name: np.array([0.9])}
        try:
            self.assertTrue(service.start())
            service.process_audio_chunk(b"\x00\x00" * 1280)
            self.assertTrue(event.wait(timeout=1.0))
        finally:
            service.stop()
        self.assertEqual(detected, ["Cubey"])

    def test_worker_ignores_score_below_threshold(self) -> None:
        callback = MagicMock()
        service = WakeWordService(threshold=0.5, on_wake_word=callback)
        service.model = MagicMock()
        service.model.predict.return_value = {service._model_name: 0.49}
        try:
            self.assertTrue(service.start())
            service.process_audio_chunk(b"\x00\x00" * 1280)
            time.sleep(0.1)
        finally:
            service.stop()
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
