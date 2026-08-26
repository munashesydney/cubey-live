"""
Speaker audio player for streaming 24kHz 16-bit PCM audio with instant interruption (flush) support.
Optimized for low-latency playback across Windows and Linux (Raspberry Pi).
"""

import logging
from collections import deque
import threading
import time
import sounddevice as sd
from typing import Deque, Optional

from src.audio.resample import convert_pcm16_mono_to_device

logger = logging.getLogger(__name__)

class AudioPlayer:
    """Plays PCM audio chunks to the speakers with real-time queue clearing on interruption."""
    
    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        block_size: int = 240,
        max_buffer_ms: int = 750,
        device=None,
        device_sample_rate: Optional[int] = None,
        device_channels: Optional[int] = None,
        device_dtype: Optional[str] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.max_buffer_ms = max_buffer_ms
        self.device = device
        self.device_sample_rate = device_sample_rate or sample_rate
        self.device_channels = device_channels or channels
        self.device_dtype = device_dtype or 'int16'
        self.bytes_per_sample = 4 if self.device_dtype.lower().strip() in ("int32", "s32", "s32_le", "float32", "f32") else 2

        self.device_block_size = max(
            1, round(self.block_size * self.device_sample_rate / self.sample_rate)
        )
        self.max_buffer_bytes = max(
            self.device_block_size * self.device_channels * self.bytes_per_sample,
            int(self.device_sample_rate * self.device_channels * self.bytes_per_sample * max_buffer_ms / 1000),
        )

        self._chunks: Deque[memoryview] = deque()
        self._chunk_offset = 0
        self._buffered_bytes = 0
        self._buffer_lock = threading.Lock()
        self.stream: Optional[sd.RawOutputStream] = None
        self.is_playing = False
        self._is_actively_speaking = False
        self._last_audio_at = 0.0
        self._buffer_warning_emitted = False

    @property
    def is_speaking(self) -> bool:
        """Returns True if speakers are actively playing audio or have queued audio chunks."""
        with self._buffer_lock:
            buffered = self._buffered_bytes > 0
        return self.is_playing and (
            buffered
            or self._is_actively_speaking
            or time.monotonic() - self._last_audio_at < 0.04
        )

    def start(self) -> None:
        """Start the speaker audio playback thread with low-latency settings."""
        if self.is_playing:
            return
            
        self.is_playing = True
        self._is_actively_speaking = False
        self.clear()
        
        try:
            self.stream = sd.RawOutputStream(
                device=self.device,
                samplerate=self.device_sample_rate,
                channels=self.device_channels,
                dtype=self.device_dtype,
                latency='low',
                blocksize=self.device_block_size,
                callback=self._audio_callback,
            )
            self.stream.start()
        except Exception as e:
            if self.device is not None:
                if self.stream is not None:
                    try:
                        self.stream.close()
                    except Exception:
                        pass
                    self.stream = None
                logger.warning(
                    "Preferred output device %r failed (%s); retrying system default",
                    self.device,
                    e,
                )
                self.device = None
                # First try system default using the configured hardware rate & format
                try:
                    self.stream = sd.RawOutputStream(
                        samplerate=self.device_sample_rate,
                        channels=self.device_channels,
                        dtype=self.device_dtype,
                        latency='low',
                        blocksize=self.device_block_size,
                        callback=self._audio_callback,
                    )
                    self.stream.start()
                    logger.info("Audio player using system-default at %d Hz, %d ch, %s", self.device_sample_rate, self.device_channels, self.device_dtype)
                    return
                except Exception:
                    pass

                # Second fallback: standard Gemini rate
                self.device_sample_rate = self.sample_rate
                self.device_channels = self.channels
                self.device_dtype = 'int16'
                self.bytes_per_sample = 2
                self.device_block_size = self.block_size
                self.max_buffer_bytes = max(
                    self.device_block_size * self.channels * 2,
                    int(
                        self.sample_rate
                        * self.channels
                        * 2
                        * self.max_buffer_ms
                        / 1000
                    ),
                )
                try:
                    self.stream = sd.RawOutputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype='int16',
                        latency='low',
                        blocksize=self.block_size,
                        callback=self._audio_callback,
                    )
                    self.stream.start()
                    logger.info("Audio player using standard 24kHz fallback")
                    return
                except Exception:
                    logger.exception("System-default output fallback also failed")
            self.is_playing = False
            logger.error("Failed to start audio speaker output stream: %s", e)
            raise
            
        logger.info(
            "Audio player started (%d Hz Gemini -> %d Hz %d ch %s device, %d-frame callback)",
            self.sample_rate,
            self.device_sample_rate,
            self.device_channels,
            self.device_dtype,
            self.device_block_size,
        )

    def stop(self) -> None:
        """Stop playback thread and close output stream."""
        self.is_playing = False
        self._is_actively_speaking = False
        self.clear()
        
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning("Error stopping audio output stream: %s", e)
            self.stream = None
            
        logger.info("Audio player stopped.")

    def play_chunk(self, pcm_data: bytes) -> None:
        """Enqueue PCM 16-bit mono bytes for playback."""
        if self.is_playing and pcm_data:
            device_pcm = convert_pcm16_mono_to_device(
                pcm_data,
                source_rate=self.sample_rate,
                target_rate=self.device_sample_rate,
                target_channels=self.device_channels,
                target_dtype=self.device_dtype,
            )
            view = memoryview(device_pcm)
            with self._buffer_lock:
                self._chunks.append(view)
                self._buffered_bytes += len(view)
                buffered_bytes = self._buffered_bytes
                should_warn = (
                    buffered_bytes > self.max_buffer_bytes
                    and not self._buffer_warning_emitted
                )
                if should_warn:
                    self._buffer_warning_emitted = True
            # Gemini can deliver generated audio substantially faster than
            # speakers play it. Dropping old frames here corrupts words into
            # clicks/scratches; retain the complete response and let barge-in
            # clear it atomically when the user actually interrupts.
            if should_warn:
                logger.info(
                    "Gemini delivered %.0f ms of audio ahead of playback; buffering full speech",
                    buffered_bytes
                    / (self.device_sample_rate * self.device_channels * self.bytes_per_sample)
                    * 1000,
                )

    def clear(self) -> None:
        """Instant Interruption (Barge-in): Flush all enqueued audio chunks immediately."""
        self._is_actively_speaking = False
        self._last_audio_at = 0.0
        with self._buffer_lock:
            self._buffer_warning_emitted = False
            count = len(self._chunks)
            self._chunks.clear()
            self._chunk_offset = 0
            self._buffered_bytes = 0
        if count:
            logger.info("Instant Interruption: Cleared %d buffered audio chunks", count)

    def _audio_callback(self, outdata, frames: int, time_info, status) -> None:
        """Fill PortAudio's next output block directly from the network buffer."""
        if status:
            logger.debug("Audio output status flag: %s", status)

        output = memoryview(outdata).cast("B")
        output[:] = b"\x00" * len(output)
        if not self.is_playing:
            self._is_actively_speaking = False
            return

        written = 0
        with self._buffer_lock:
            while written < len(output) and self._chunks:
                chunk = self._chunks[0]
                available = len(chunk) - self._chunk_offset
                take = min(available, len(output) - written)
                output[written:written + take] = chunk[
                    self._chunk_offset:self._chunk_offset + take
                ]
                written += take
                self._chunk_offset += take
                self._buffered_bytes -= take
                if self._chunk_offset == len(chunk):
                    self._chunks.popleft()
                    self._chunk_offset = 0
            if self._buffered_bytes == 0:
                self._buffer_warning_emitted = False

        self._is_actively_speaking = written > 0
        if written:
            self._last_audio_at = time.monotonic()
