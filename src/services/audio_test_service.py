"""
Audio Test Service — Live Microphone Diagnostics & Denoise Evaluation for Cubey Robot.

Provides real-time dual-stream audio metrics (Raw vs Denoised), VU levels in dB,
waveform telemetry for oscilloscopes, on-the-fly denoiser toggling, and in-memory
WAV clip recording for acoustic verification through the web interface.
"""

import base64
import io
import logging
import math
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioTestSnapshot:
    """Real-time microphone diagnostic snapshot broadcast to web clients."""
    raw_rms: float = 0.0
    raw_db: float = -60.0
    raw_pct: float = 0.0
    denoised_rms: float = 0.0
    denoised_db: float = -60.0
    denoised_pct: float = 0.0
    noise_reduction_db: float = 0.0
    vad_prob: float = 0.0
    is_denoiser_enabled: bool = True
    waveform_raw: List[float] = field(default_factory=list)
    waveform_denoised: List[float] = field(default_factory=list)
    sample_rate: int = 16000
    channels: int = 1
    device_name: str = "Default Input"
    is_recording_test: bool = False
    test_record_progress_pct: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_rms": round(self.raw_rms, 4),
            "raw_db": round(self.raw_db, 1),
            "raw_pct": round(self.raw_pct, 1),
            "denoised_rms": round(self.denoised_rms, 4),
            "denoised_db": round(self.denoised_db, 1),
            "denoised_pct": round(self.denoised_pct, 1),
            "noise_reduction_db": round(self.noise_reduction_db, 1),
            "vad_prob": round(self.vad_prob, 2),
            "is_denoiser_enabled": self.is_denoiser_enabled,
            "waveform_raw": self.waveform_raw,
            "waveform_denoised": self.waveform_denoised,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "device_name": self.device_name,
            "is_recording_test": self.is_recording_test,
            "test_record_progress_pct": round(self.test_record_progress_pct, 1),
            "timestamp": self.timestamp,
        }


class AudioTestService:
    """Coordinates real-time microphone diagnostics, denoiser toggling, and clip testing."""

    def __init__(self):
        self._lock = threading.Lock()
        self.snapshot = AudioTestSnapshot()
        self._recorder = None

        # Live listeners (WebSocket dispatchers)
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

        # 5-second test recording state
        self._is_recording_test = False
        self._test_record_start_time = 0.0
        self._test_record_duration_s = 5.0
        self._test_raw_frames: bytearray = bytearray()
        self._test_denoised_frames: bytearray = bytearray()
        self._last_test_wav_raw: Optional[bytes] = None
        self._last_test_wav_denoised: Optional[bytes] = None

        # Rate limiter for telemetry updates (30 Hz)
        self._last_broadcast_time = 0.0

    def attach_recorder(self, recorder) -> None:
        """Attach active AudioRecorder instance to pipe audio frames."""
        with self._lock:
            self._recorder = recorder
            if recorder:
                self.snapshot.sample_rate = getattr(recorder, "sample_rate", 16000)
                self.snapshot.channels = getattr(recorder, "channels", 1)
                self.snapshot.device_name = str(getattr(recorder, "device", "Default Input") or "System Default Mic")
                self.snapshot.is_denoiser_enabled = getattr(recorder.denoiser, "enabled", True) if getattr(recorder, "denoiser", None) else False
                # Hook into recorder callback
                recorder.on_diagnostic_chunk = self.feed_chunk

    def feed_chunk(self, raw_bytes: bytes, denoised_bytes: bytes, denoiser_enabled: bool) -> None:
        """Invoked by sounddevice recorder thread with raw and denoised PCM16 audio."""
        now = time.time()

        try:
            raw_arr = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
            denoised_arr = np.frombuffer(denoised_bytes, dtype=np.int16).astype(np.float32)

            if len(raw_arr) == 0:
                return

            # Compute RMS and dB for Raw stream
            raw_rms = float(np.sqrt(np.mean(np.square(raw_arr))))
            raw_db = 20.0 * math.log10(max(raw_rms, 1.0) / 32768.0)
            raw_pct = min(100.0, (raw_rms / 32768.0) * 100.0 * 6.0)

            # Compute RMS and dB for Denoised stream
            den_rms = float(np.sqrt(np.mean(np.square(denoised_arr))))
            den_db = 20.0 * math.log10(max(den_rms, 1.0) / 32768.0)
            den_pct = min(100.0, (den_rms / 32768.0) * 100.0 * 6.0)

            # Noise reduction attenuation (difference in dB)
            reduction_db = max(0.0, raw_db - den_db) if denoiser_enabled else 0.0

            # VAD estimate based on energy & spectral dynamics
            vad_prob = min(1.0, max(0.0, (raw_db + 45.0) / 30.0))

            # Downsample waveforms for 32-point oscilloscope display
            step = max(1, len(raw_arr) // 32)
            wf_raw = [round(float(v) / 32768.0, 3) for v in raw_arr[::step][:32]]
            wf_den = [round(float(v) / 32768.0, 3) for v in denoised_arr[::step][:32]]

            # Accumulate test recording frames if active
            test_progress = 0.0
            with self._lock:
                if self._is_recording_test:
                    self._test_raw_frames.extend(raw_bytes)
                    self._test_denoised_frames.extend(denoised_bytes)
                    elapsed = now - self._test_record_start_time
                    test_progress = min(100.0, (elapsed / self._test_record_duration_s) * 100.0)

                    if elapsed >= self._test_record_duration_s:
                        self._finish_test_recording()

                self.snapshot.raw_rms = raw_rms
                self.snapshot.raw_db = max(-60.0, raw_db)
                self.snapshot.raw_pct = raw_pct
                self.snapshot.denoised_rms = den_rms
                self.snapshot.denoised_db = max(-60.0, den_db)
                self.snapshot.denoised_pct = den_pct
                self.snapshot.noise_reduction_db = reduction_db
                self.snapshot.vad_prob = vad_prob
                self.snapshot.is_denoiser_enabled = denoiser_enabled
                self.snapshot.waveform_raw = wf_raw
                self.snapshot.waveform_denoised = wf_den
                self.snapshot.is_recording_test = self._is_recording_test
                self.snapshot.test_record_progress_pct = test_progress
                self.snapshot.timestamp = now

            # Broadcast to active WebSockets at ~25 Hz
            if now - self._last_broadcast_time >= 0.040:
                self._last_broadcast_time = now
                payload = self.snapshot.to_dict()
                # Include base64 audio chunk for live monitor in browser
                payload["audio_raw_b64"] = base64.b64encode(raw_bytes).decode("ascii")
                payload["audio_denoised_b64"] = base64.b64encode(denoised_bytes).decode("ascii")

                for listener in list(self._listeners):
                    try:
                        listener(payload)
                    except Exception:
                        pass

        except Exception as e:
            logger.debug("Error in audio test telemetry calculation: %s", e)

    def set_denoiser_enabled(self, enabled: bool) -> bool:
        """Toggle hardware noise suppression (RNNoise / HighPass) on or off."""
        with self._lock:
            if self._recorder and hasattr(self._recorder, "denoiser") and self._recorder.denoiser:
                self._recorder.denoiser.enabled = enabled
                self.snapshot.is_denoiser_enabled = enabled
                logger.info("Audio Denoiser toggled -> %s", enabled)
                return True
        return False

    def start_test_recording(self, duration_s: float = 5.0) -> bool:
        """Trigger a 5-second test clip recording for auditory playback."""
        with self._lock:
            self._is_recording_test = True
            self._test_record_duration_s = max(1.0, min(15.0, duration_s))
            self._test_record_start_time = time.time()
            self._test_raw_frames = bytearray()
            self._test_denoised_frames = bytearray()
            logger.info("Started %0.1fs microphone test recording...", self._test_record_duration_s)
            return True

    def _finish_test_recording(self) -> None:
        """Encode captured frames to in-memory WAV files."""
        self._is_recording_test = False
        rate = self.snapshot.sample_rate or 16000
        ch = self.snapshot.channels or 1

        # Encode Raw WAV
        try:
            raw_buf = io.BytesIO()
            with wave.open(raw_buf, "wb") as wf:
                wf.setnchannels(ch)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(rate)
                wf.writeframes(self._test_raw_frames)
            self._last_test_wav_raw = raw_buf.getvalue()
        except Exception as e:
            logger.error("Error creating raw test WAV: %s", e)

        # Encode Denoised WAV
        try:
            den_buf = io.BytesIO()
            with wave.open(den_buf, "wb") as wf:
                wf.setnchannels(ch)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(self._test_denoised_frames)
            self._last_test_wav_denoised = den_buf.getvalue()
        except Exception as e:
            logger.error("Error creating denoised test WAV: %s", e)

        logger.info(
            "Microphone test recording finished. Raw=%d bytes, Denoised=%d bytes",
            len(self._test_raw_frames), len(self._test_denoised_frames)
        )

    def get_test_wav(self, kind: str = "denoised") -> Optional[bytes]:
        """Retrieve the last recorded test WAV audio bytes."""
        with self._lock:
            if kind == "raw":
                return self._last_test_wav_raw
            return self._last_test_wav_denoised

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register WebSocket subscriber for live audio telemetry."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Unregister WebSocket subscriber."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)


# Global singleton instance
_audio_test_service: Optional[AudioTestService] = None
_audio_test_lock = threading.Lock()


def get_audio_test_service() -> AudioTestService:
    """Retrieve or initialize the global AudioTestService singleton."""
    global _audio_test_service
    with _audio_test_lock:
        if _audio_test_service is None:
            _audio_test_service = AudioTestService()
        return _audio_test_service
