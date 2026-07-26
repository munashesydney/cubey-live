"""
Speaker audio player for streaming 24kHz 16-bit PCM audio with instant interruption (flush) support.
Optimized for low-latency playback across Windows and Linux (Raspberry Pi).
"""

import logging
import queue
import threading
import sounddevice as sd
from typing import Optional

logger = logging.getLogger(__name__)

class AudioPlayer:
    """Plays PCM audio chunks to the speakers with real-time queue clearing on interruption."""
    
    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        
        self.audio_queue: queue.Queue[bytes] = queue.Queue()
        self.stream: Optional[sd.RawOutputStream] = None
        self.is_playing = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the speaker audio playback thread with low-latency settings."""
        if self.is_playing:
            return
            
        self.is_playing = True
        self._stop_event.clear()
        
        try:
            self.stream = sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                latency='low',
                blocksize=512
            )
            self.stream.start()
        except Exception as e:
            self.is_playing = False
            logger.error("Failed to start audio speaker output stream: %s", e)
            raise
            
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()
        logger.info("Audio player started (%d Hz, Low Latency)", self.sample_rate)

    def stop(self) -> None:
        """Stop playback thread and close output stream."""
        self.is_playing = False
        self._stop_event.set()
        self.clear()
        
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.warning("Error stopping audio output stream: %s", e)
            self.stream = None
            
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            
        logger.info("Audio player stopped.")

    def play_chunk(self, pcm_data: bytes) -> None:
        """Enqueue PCM 16-bit mono bytes for playback."""
        if self.is_playing and pcm_data:
            self.audio_queue.put(pcm_data)

    def clear(self) -> None:
        """Instant Interruption (Barge-in): Flush all enqueued audio chunks immediately."""
        count = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
                count += 1
            except queue.Empty:
                break
        logger.info("Instant Interruption: Cleared %d buffered audio chunks from player queue.", count)

    def _playback_loop(self) -> None:
        """Worker thread continuously reading audio chunks from queue and writing to speakers."""
        while not self._stop_event.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.05)
                if chunk and self.stream and self.is_playing:
                    self.stream.write(chunk)
                self.audio_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                if self.is_playing:
                    logger.error("Error writing audio chunk to speaker: %s", e)
