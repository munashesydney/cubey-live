"""Offline Cubey wake-word detection powered by a custom openWakeWord model."""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np


logger = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "wakeword"
DEFAULT_MODEL_PATH = _ASSET_DIR / "cubey_multigreeting_v2.onnx"
DEFAULT_MELSPEC_MODEL_PATH = _ASSET_DIR / "melspectrogram.onnx"
DEFAULT_EMBEDDING_MODEL_PATH = _ASSET_DIR / "embedding_model.onnx"


class WakeWordService:
    """Run openWakeWord inference away from the microphone callback thread."""

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        threshold: float = 0.5,
        on_wake_word: Optional[Callable[[str], None]] = None,
        melspec_model_path: Optional[Union[str, Path]] = None,
        embedding_model_path: Optional[Union[str, Path]] = None,
        cooldown_seconds: float = 2.0,
    ) -> None:
        self.model_path = Path(model_path or DEFAULT_MODEL_PATH)
        self.melspec_model_path = Path(
            melspec_model_path or DEFAULT_MELSPEC_MODEL_PATH
        )
        self.embedding_model_path = Path(
            embedding_model_path or DEFAULT_EMBEDDING_MODEL_PATH
        )
        self.threshold = min(max(float(threshold), 0.0), 1.0)
        self.on_wake_word = on_wake_word
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))

        self.model = None
        self._model_name = self.model_path.stem
        self._is_running = False
        self._is_paused = False
        # At 20 ms per packet this caps queued work at 400 ms. If inference
        # falls behind, freshness matters more than old audio.
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=20)
        self._worker_thread: Optional[threading.Thread] = None
        self._state_lock = threading.RLock()
        self._last_detection_time = 0.0
        self._dropped_chunks = 0

    def _required_assets(self) -> tuple[Path, Path, Path]:
        return (
            self.model_path,
            self.melspec_model_path,
            self.embedding_model_path,
        )

    def initialize(self) -> bool:
        """Load the custom classifier and shared openWakeWord feature models."""

        missing = [str(path) for path in self._required_assets() if not path.is_file()]
        if missing:
            logger.error("Wake-word model asset(s) missing: %s", ", ".join(missing))
            return False

        try:
            from openwakeword.model import Model
        except ImportError:
            logger.error(
                "openwakeword is not installed; install project requirements to "
                "enable wake-word detection."
            )
            return False

        try:
            self.model = Model(
                wakeword_models=[str(self.model_path)],
                inference_framework="onnx",
                melspec_model_path=str(self.melspec_model_path),
                embedding_model_path=str(self.embedding_model_path),
            )
            logger.info(
                "Custom openWakeWord model loaded: %s (threshold %.2f)",
                self.model_path,
                self.threshold,
            )
            return True
        except Exception:
            logger.exception("Failed to initialize custom openWakeWord model")
            self.model = None
            return False

    def start(self) -> bool:
        if self._is_running:
            return True
        if self.model is None and not self.initialize():
            logger.warning("WakeWordService cannot start: model initialization failed.")
            return False

        self._is_running = True
        self._is_paused = False
        self._worker_thread = threading.Thread(
            target=self._process_loop,
            daemon=True,
            name="OpenWakeWordWorker",
        )
        self._worker_thread.start()
        logger.info("openWakeWord worker started.")
        return True

    def stop(self) -> None:
        self._is_running = False
        self._is_paused = True
        self._clear_queue()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None
        logger.info("openWakeWord worker stopped.")

    def pause(self) -> None:
        with self._state_lock:
            self._is_paused = True
            self._clear_queue()
        logger.info("openWakeWord worker paused.")

    def resume(self) -> None:
        with self._state_lock:
            self._clear_queue()
            self._reset_model()
            self._is_paused = False
        logger.info("openWakeWord worker resumed.")

    def process_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """Queue one mono 16 kHz PCM16 packet without blocking audio capture."""

        if not self._is_running or self._is_paused or not pcm16_bytes:
            return
        try:
            self._audio_queue.put_nowait(pcm16_bytes)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(pcm16_bytes)
                self._dropped_chunks += 1
                if self._dropped_chunks == 1 or self._dropped_chunks % 100 == 0:
                    logger.warning(
                        "openWakeWord inference fell behind; dropped %d stale chunk(s)",
                        self._dropped_chunks,
                    )
            except queue.Empty:
                pass

    def _process_loop(self) -> None:
        while self._is_running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._is_paused or self.model is None:
                continue

            try:
                samples = np.frombuffer(chunk, dtype=np.int16)
                if samples.size == 0:
                    continue
                with self._state_lock:
                    if self._is_paused or self.model is None:
                        continue
                    prediction = self.model.predict(samples)
                score = self._prediction_score(prediction)
                if score >= self.threshold:
                    now = time.monotonic()
                    if now - self._last_detection_time >= self.cooldown_seconds:
                        self._last_detection_time = now
                        logger.info("🎯 Cubey wake word detected (score %.3f)", score)
                        self._dispatch_detection("Cubey")
            except Exception:
                logger.exception("Error in openWakeWord processing loop")

    def _prediction_score(self, prediction: object) -> float:
        if not isinstance(prediction, dict) or not prediction:
            return 0.0
        raw_score = prediction.get(self._model_name)
        if raw_score is None:
            raw_score = next(iter(prediction.values()))
        values = np.asarray(raw_score).reshape(-1)
        return float(values[0]) if values.size else 0.0

    def _reset_model(self) -> None:
        if self.model is not None:
            try:
                self.model.reset()
            except Exception:
                logger.debug("Unable to reset openWakeWord model state", exc_info=True)

    def _clear_queue(self) -> None:
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                return

    def _dispatch_detection(self, keyword: str) -> None:
        if self.on_wake_word:
            try:
                self.on_wake_word(keyword)
            except Exception:
                logger.exception("Error in wake-word callback")
