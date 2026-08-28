"""
#Audio Noise Suppression and Voice Enhancement Module.

Provides streaming noise suppression for robot microphones:
1. High-pass Rumble Filter to remove mechanical motor vibrations.
2. Neural Noise Suppression (RNNoise GRU) via pyrnnoise or ctypes librnnoise.
3. Adaptive noise gating as fallback.
"""

import ctypes
import ctypes.util
import logging
import os
import numpy as np
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Try importing pyrnnoise if installed
PYRNNOISE_AVAILABLE = False
try:
    from pyrnnoise import RNNoise as PyRNNoise
    PYRNNOISE_AVAILABLE = True
except (ImportError, Exception):
    PyRNNoise = None


class RNNoiseCTypesWrapper:
    """Direct ctypes wrapper around system librnnoise.so / librnnoise.dll."""

    def __init__(self, lib_path: str):
        self.lib = ctypes.CDLL(lib_path)
        self.lib.rnnoise_create.restype = ctypes.c_void_p
        self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_destroy.restype = None
        self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_process_frame.restype = ctypes.c_float
        self.lib.rnnoise_process_frame.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.state = self.lib.rnnoise_create(None)
        if not self.state:
            raise RuntimeError('rnnoise_create returned a null state')

    def process_frame(self, in_floats: np.ndarray) -> Tuple[np.ndarray, float]:
        out_floats = np.zeros(480, dtype=np.float32)
        in_ptr = in_floats.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        out_ptr = out_floats.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        vad_prob = float(self.lib.rnnoise_process_frame(self.state, out_ptr, in_ptr))
        return out_floats, vad_prob

    def destroy(self) -> None:
        if hasattr(self, 'state') and self.state:
            try:
                self.lib.rnnoise_destroy(self.state)
            except Exception:
                pass
            self.state = None

    def reset(self) -> None:
        self.destroy()
        self.state = self.lib.rnnoise_create(None)
        if not self.state:
            raise RuntimeError('rnnoise_create returned a null state')

    def __del__(self):
        self.destroy()


def _find_system_librnnoise() -> Optional[str]:
    """Search standard Linux / Windows / macOS paths for librnnoise."""
    candidate_paths = [
        '/usr/lib/aarch64-linux-gnu/librnnoise.so.0',
        '/usr/lib/aarch64-linux-gnu/librnnoise.so',
        '/usr/lib/arm-linux-gnueabihf/librnnoise.so.0',
        '/usr/lib/x86_64-linux-gnu/librnnoise.so.0',
        '/usr/lib/x86_64-linux-gnu/librnnoise.so',
        '/usr/local/lib/librnnoise.so',
        '/usr/local/lib/librnnoise.so.0',
        '/usr/lib/librnnoise.so',
        '/usr/lib/librnnoise.so.0',
    ]

    for path in candidate_paths:
        if os.path.isfile(path):
            return path

    found = ctypes.util.find_library('rnnoise')
    if found:
        return found
    return None


class HighPassFilter:
    """
    2nd-order Transposed Direct Form II IIR High-Pass Filter (Butterworth).
    Attenuates low-frequency motor rumble and chassis vibration (<80Hz).
    """

    def __init__(self, sample_rate: int = 16000, cutoff_hz: float = 80.0):
        self.sample_rate = sample_rate
        self.cutoff_hz = cutoff_hz

        # Audio EQ Cookbook High-Pass Filter Coefficients
        w0 = 2.0 * np.pi * cutoff_hz / sample_rate
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * 0.70710678)

        a0 = 1.0 + alpha
        self.b0 = float(((1.0 + cos_w0) / 2.0) / a0)
        self.b1 = float((-(1.0 + cos_w0)) / a0)
        self.b2 = float(((1.0 + cos_w0) / 2.0) / a0)
        self.a1 = float((-2.0 * cos_w0) / a0)
        self.a2 = float((1.0 - alpha) / a0)

        self.s1 = 0.0
        self.s2 = 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        out = np.empty_like(samples, dtype=np.float32)
        s1 = self.s1
        s2 = self.s2
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2

        for i in range(len(samples)):
            x = float(samples[i])
            y = b0 * x + s1
            s1 = b1 * x - a1 * y + s2
            s2 = b2 * x - a2 * y
            out[i] = y

        self.s1 = s1
        self.s2 = s2
        return out

    def reset(self) -> None:
        self.s1 = 0.0
        self.s2 = 0.0


class AudioDenoiser:
    """
    Production-grade streaming audio noise suppressor.

    Accepts 16kHz mono 16-bit PCM bytes and applies:
    1. High-pass filter to eliminate DC bias and motor rumbling.
    2. RNNoise neural network (or adaptive spectral gating).
    3. Soft speech level normalization.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        enabled: bool = True,
        enable_highpass: bool = True,
    ):
        self.sample_rate = sample_rate
        self.enabled = enabled
        self.enable_highpass = enable_highpass

        self.highpass = HighPassFilter(sample_rate=sample_rate, cutoff_hz=80.0)
        self._rnnoise_py: Optional[Any] = None
        self._rnnoise_ctypes: Optional[RNNoiseCTypesWrapper] = None
        self.backend_name = 'disabled' if not enabled else 'initializing'

        self._noise_floor = 100.0

        if self.enabled:
            self._init_backend()

    def _init_backend(self) -> None:
        if PYRNNOISE_AVAILABLE and PyRNNoise is not None:
            try:
                try:
                    obj = PyRNNoise(sample_rate=self.sample_rate)
                except TypeError:
                    obj = PyRNNoise()
                if np is not None:
                    # Warm up with dummy chunk to ensure graph initialization succeeds
                    list(obj.denoise_chunk(np.zeros((1, 320), dtype=np.int16)))
                self._rnnoise_py = obj
                self.backend_name = 'pyrnnoise (Neural)'
                logger.info('Initialized pyrnnoise neural noise suppressor at %d Hz', self.sample_rate)
                return
            except Exception as e:
                logger.warning('Failed to initialize pyrnnoise (%s); trying fallback', e)
                self._rnnoise_py = None

        # The raw C API only accepts native 48 kHz / 480-sample frames. The
        # pyrnnoise wrapper above performs its own resampling for our normal
        # 16 kHz Gemini stream; do not feed 16 kHz samples directly to C.
        lib_path = _find_system_librnnoise() if self.sample_rate == 48000 else None
        if lib_path:
            try:
                self._rnnoise_ctypes = RNNoiseCTypesWrapper(lib_path)
                self.backend_name = f'librnnoise ({os.path.basename(lib_path)})'
                logger.info('Initialized system librnnoise (%s) at %d Hz', lib_path, self.sample_rate)
                return
            except Exception as e:
                logger.warning('Failed to load system librnnoise (%s); falling back to DSP gate', e)

        self.backend_name = 'highpass_adaptive_gate'
        logger.info('Using highpass + adaptive noise gate for noise suppression at %d Hz', self.sample_rate)

    def process(self, pcm16_bytes: bytes) -> bytes:
        if not self.enabled or not pcm16_bytes:
            return pcm16_bytes

        samples = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)

        if self.enable_highpass:
            samples = self.highpass.process(samples)

        if self._rnnoise_py is not None:
            try:
                # pyrnnoise's streaming API accepts [channels, samples] and
                # yields one or more (speech_probability, audio) frames.
                output_frames = []
                mono_chunk = np.atleast_2d(samples.astype(np.int16))
                for _, denoised_frame in self._rnnoise_py.denoise_chunk(mono_chunk):
                    output_frames.append(
                        np.asarray(denoised_frame, dtype=np.float32).reshape(-1)
                    )
                if not output_frames:
                    # The wrapper may retain a partial native RNNoise frame.
                    return b''
                samples = np.concatenate(output_frames)
            except Exception as exc:
                # Never silently turn a failed neural backend into raw-audio
                # passthrough. Log once, disable it, and use the fallback.
                logger.exception(
                    'pyrnnoise processing failed; switching to adaptive noise gate: %s',
                    exc,
                )
                self._rnnoise_py = None
                self.backend_name = 'highpass_adaptive_gate (pyrnnoise failed)'
                samples = self._apply_adaptive_gate(samples)

        elif self._rnnoise_ctypes is not None:
            pad_len = (480 - (len(samples) % 480)) % 480
            if pad_len > 0:
                padded = np.pad(samples, (0, pad_len), mode='constant')
            else:
                padded = samples

            out_chunks = []
            for i in range(0, len(padded), 480):
                block = padded[i:i+480]
                out_block, _ = self._rnnoise_ctypes.process_frame(block)
                out_chunks.append(out_block)

            denoised = np.concatenate(out_chunks)[:len(samples)]
            samples = denoised

        else:
            samples = self._apply_adaptive_gate(samples)

        clipped = np.clip(samples, -32768, 32767).astype(np.int16)
        return clipped.tobytes()

    def _apply_adaptive_gate(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return samples

        frame_energy = float(np.sqrt(np.mean(samples ** 2)))
        if frame_energy < self._noise_floor * 2.0:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * frame_energy
        self._noise_floor = max(30.0, min(self._noise_floor, 800.0))

        threshold = self._noise_floor * 1.8
        if frame_energy < threshold:
            attenuation = max(0.15, (frame_energy / threshold) ** 1.5)
            return samples * attenuation
        return samples

    def reset(self) -> None:
        self.highpass.reset()
        if self._rnnoise_py is not None:
            try:
                self._rnnoise_py.reset()
            except Exception as exc:
                logger.warning('Could not reset pyrnnoise state: %s', exc)
        elif self._rnnoise_ctypes is not None:
            try:
                self._rnnoise_ctypes.reset()
            except Exception as exc:
                logger.warning('Could not reset librnnoise state: %s', exc)
        self._noise_floor = 100.0
