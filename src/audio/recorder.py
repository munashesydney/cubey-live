"""
Microphone audio stream recorder capturing 16kHz 16-bit PCM mono audio.
Optimized for low-latency capture across Windows and Linux (Raspberry Pi).
"""

import asyncio
import logging
import math
import time
import numpy as np
import sounddevice as sd
from typing import Callable, Optional

from src.audio.resample import convert_device_to_pcm16_mono

logger = logging.getLogger(__name__)

class AudioRecorder:
    """Captures microphone audio and pushes PCM 16-bit mono chunks into an asyncio queue."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 320,
        max_queue_ms: int = 240,
        device=None,
        device_sample_rate: Optional[int] = None,
        device_channels: Optional[int] = None,
        device_dtype: Optional[str] = None,
        on_level_change: Optional[Callable[[float], None]] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.max_queue_ms = max_queue_ms
        self.device = device
        self.device_sample_rate = device_sample_rate or sample_rate
        self.device_channels = device_channels or channels
        self.device_dtype = device_dtype or 'int16'
        self.bytes_per_sample = 4 if self.device_dtype.lower().strip() in ("int32", "s32", "s32_le", "float32", "f32") else 2

        # Run the hardware callback at 10 ms for low device latency, then
        # aggregate into the configured 20 ms Gemini packet size below.
        self.device_chunk_size = max(
            1, round(self.device_sample_rate / 100)
        )
        self.on_level_change = on_level_change
        self.on_audio_chunk = on_audio_chunk
        
        self.is_recording = False
        self.is_muted = False
        self.stream: Optional[sd.RawInputStream] = None
        chunk_ms = max(1.0, self.chunk_size / self.sample_rate * 1000.0)
        self.max_queue_chunks = max(2, math.ceil(self.max_queue_ms / chunk_ms))
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=self.max_queue_chunks
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_level_update = 0.0
        self._dropped_chunks = 0
        self._packet_buffer = bytearray()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start capturing audio from the default microphone with low-latency settings."""
        if self.is_recording:
            return
            
        self._loop = loop
        self.clear_queue()
        self._packet_buffer.clear()
        self.is_recording = True
        
        try:
            self.stream = sd.RawInputStream(
                device=self.device,
                samplerate=self.device_sample_rate,
                channels=self.device_channels,
                dtype=self.device_dtype,
                blocksize=self.device_chunk_size,
                latency='low',
                callback=self._audio_callback
            )
            self.stream.start()
            logger.info(
                "Microphone recorder started (%d Hz %d ch %s device -> %d Hz Gemini)",
                self.device_sample_rate,
                self.device_channels,
                self.device_dtype,
                self.sample_rate,
            )
        except Exception as e:
            # A preferred endpoint can disappear between enumeration and open
            # (USB/Bluetooth changes). Retry the system default safely.
            if self.device is not None:
                if self.stream is not None:
                    try:
                        self.stream.close()
                    except Exception:
                        pass
                    self.stream = None
                logger.warning(
                    "Preferred input device %r failed (%s); retrying system default",
                    self.device,
                    e,
                )
                self.device = None
                # First try system default using the configured hardware rate & format
                try:
                    self.stream = sd.RawInputStream(
                        samplerate=self.device_sample_rate,
                        channels=self.device_channels,
                        dtype=self.device_dtype,
                        blocksize=self.device_chunk_size,
                        latency='low',
                        callback=self._audio_callback,
                    )
                    self.stream.start()
                    logger.info("Microphone recorder using system-default at %d Hz, %d ch, %s", self.device_sample_rate, self.device_channels, self.device_dtype)
                    return
                except Exception:
                    pass

                # Second fallback: standard Gemini rate
                self.device_sample_rate = self.sample_rate
                self.device_channels = self.channels
                self.device_dtype = 'int16'
                self.bytes_per_sample = 2
                self.device_chunk_size = self.chunk_size
                try:
                    self.stream = sd.RawInputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype='int16',
                        blocksize=self.chunk_size,
                        latency='low',
                        callback=self._audio_callback,
                    )
                    self.stream.start()
                    logger.info("Microphone recorder using standard 16kHz fallback")
                    return
                except Exception:
                    logger.exception("System-default input fallback also failed")
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

    def clear_queue(self) -> None:
        """Discard stale captured audio between sessions."""
        while True:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except asyncio.QueueEmpty:
                break

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
            
        converted = convert_device_to_pcm16_mono(
            bytes(indata),
            source_rate=self.device_sample_rate,
            target_rate=self.sample_rate,
            source_channels=self.device_channels,
            source_dtype=self.device_dtype,
        )

        if self.is_muted:
            # Send silent PCM frame if muted
            converted = b'\x00' * len(converted)

        self._packet_buffer.extend(converted)
        packet_bytes = self.chunk_size * self.channels * 2
        while len(self._packet_buffer) >= packet_bytes:
            data = bytes(self._packet_buffer[:packet_bytes])
            del self._packet_buffer[:packet_bytes]
            self._dispatch_packet(data)

    def _dispatch_packet(self, data: bytes) -> None:
        """Fan out one correctly-sized Gemini microphone packet."""
        if self._loop is None:
            return

        # Keep the PortAudio callback non-blocking. The actual bounded enqueue
        # happens on the asyncio thread, where asyncio.Queue is safe to mutate.
        self._loop.call_soon_threadsafe(self._enqueue_latest, data)

        # Fan out to optional secondary consumers.
        # This runs on the sounddevice audio thread; sinks must be thread-safe.
        if self.on_audio_chunk and not self.is_muted:
            try:
                self.on_audio_chunk(data)
            except Exception:
                pass
        
        # Calculate RMS level for UI meter
        now = time.monotonic()
        if (
            self.on_level_change
            and not self.is_muted
            and now - self._last_level_update >= 1 / 15
        ):
            self._last_level_update = now
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

    def _enqueue_latest(self, data: bytes) -> None:
        """Enqueue a frame without ever allowing captured audio to go stale."""
        if not self.is_recording:
            return
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
                self._dropped_chunks += 1
                if self._dropped_chunks == 1 or self._dropped_chunks % 50 == 0:
                    logger.warning(
                        "Microphone transport fell behind; dropped %d stale chunk(s)",
                        self._dropped_chunks,
                    )
            except asyncio.QueueEmpty:
                pass
        self.audio_queue.put_nowait(data)
