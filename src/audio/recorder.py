"""
Microphone audio stream recorder capturing 16kHz 16-bit PCM mono audio.
Optimized for low-latency capture across Windows and Linux (Raspberry Pi).
"""

import asyncio
import logging
import math
import numpy as np
import sounddevice as sd
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class AudioRecorder:
    """Captures microphone audio and pushes PCM 16-bit mono chunks into an asyncio queue."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 512,
        on_level_change: Optional[Callable[[float], None]] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.on_level_change = on_level_change
        self.on_audio_chunk = on_audio_chunk
        
        self.is_recording = False
        self.is_muted = False
        self.stream: Optional[sd.RawInputStream] = None
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start capturing audio from the default microphone with low-latency settings."""
        if self.is_recording:
            return
            
        self._loop = loop
        self.is_recording = True
        
        try:
            self.stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                blocksize=self.chunk_size,
                latency='low',
                callback=self._audio_callback
            )
            self.stream.start()
            logger.info("Microphone audio recorder started (%d Hz, Low Latency)", self.sample_rate)
        except Exception as e:
            self.is_recording = False
            logger.error("Failed to start audio input stream: %s", e)
            raise

    def stop(self) -> None:
        """Stop microphone capture."""
        self.is_recording = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning("Error closing audio recorder stream: %s", e)
            self.stream = None
        logger.info("Microphone audio recorder stopped.")

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute microphone audio feeding the stream."""
        self.is_muted = muted
        logger.info("Microphone mute state changed to: %s", muted)

    def _audio_callback(self, indata: bytes, frames: int, time_info: dict, status: sd.CallbackFlags) -> None:
        """Callback invoked by sounddevice audio thread."""
        if status:
            logger.warning("Audio input status flag: %s", status)
            
        if not self.is_recording or self._loop is None:
            return
            
        if self.is_muted:
            # Send silent PCM frame if muted
            data = b'\x00' * len(indata)
        else:
            data = bytes(indata)
            
        # Push to asyncio queue thread-safely
        self._loop.call_soon_threadsafe(self.audio_queue.put_nowait, data)

        # Fan out to secondary consumers (e.g. local STT for conversation history).
        # This runs on the sounddevice audio thread; sinks must be thread-safe.
        if self.on_audio_chunk and not self.is_muted:
            try:
                self.on_audio_chunk(data)
            except Exception:
                pass
        
        # Calculate RMS level for UI meter
        if self.on_level_change and not self.is_muted:
            try:
                audio_array = np.frombuffer(data, dtype=np.int16)
                if len(audio_array) > 0:
                    mean_sq = np.mean(np.square(audio_array, dtype=np.float32))
                    rms = math.sqrt(mean_sq)
                    # Normalize RMS to 0.0 - 1.0 range
                    norm_level = min(1.0, rms / 32768.0 * 8.0)
                    self._loop.call_soon_threadsafe(self.on_level_change, norm_level)
            except Exception:
                pass
