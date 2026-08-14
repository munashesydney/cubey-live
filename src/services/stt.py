"""
Local speech-to-text (STT) for Cubey's conversation history.

Transcribes the user's microphone audio and the robot's own spoken output
on-device with faster-whisper, so no paid server transcription is needed and
nothing changes about the live Gemini session's latency. Transcription runs in
background worker threads and emits results whenever they are ready; the live
audio path is never blocked.

Audio conventions (must match the rest of the app):
  - user side:  16 kHz 16-bit PCM mono (AudioRecorder output)
  - model side: 24 kHz 16-bit PCM mono (Gemini Live audio output)

faster-whisper is imported lazily so this module (and the app) still works if
the dependency is missing — transcripts are simply skipped with a warning.
"""

import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# PyAV ships with faster-whisper and has a proper low-pass resampler. Linear
# interpolation is only a last-resort fallback (it aliases on downsampling).
try:
    import av as _av
except ImportError:  # pragma: no cover - av is a faster-whisper dependency
    _av = None

USER_SAMPLE_RATE = 16000
MODEL_SAMPLE_RATE = 24000

# VAD parameters. Silence is tracked by byte duration rather than chunk count,
# so changing the live capture packet size does not change transcription turns.
_VAD_SPEECH_THRESHOLD = 0.03   # normalized RMS; marks "is this chunk speech or not"
_VAD_SILENCE_SECONDS = 2.0     # quiet duration that ends a user turn
_MIN_UTTERANCE_SECONDS = 0.5   # ignore blips shorter than this

# Suppress whisper hallucinating text on silence/non-speech audio.
_HALLUCINATION_SILENCE_THRESHOLD = 0.5


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit PCM mono bytes from src_rate to dst_rate.

    Uses PyAV's high-quality resampler (available wherever faster-whisper is
    installed); falls back to linear interpolation if PyAV is unavailable.
    """
    if src_rate == dst_rate:
        return data
    samples = np.frombuffer(data, dtype=np.int16)
    if len(samples) == 0:
        return b""
    if _av is not None:
        try:
            resampler = _av.AudioResampler(format="s16", layout="mono", rate=dst_rate)
            frame = _av.AudioFrame.from_ndarray(
                samples.reshape(1, -1), format="s16", layout="mono"
            )
            frame.sample_rate = src_rate
            out_frames = list(resampler.resample(frame))
            if out_frames:
                arr = np.concatenate([f.to_ndarray() for f in out_frames], axis=1)
                return arr[0].astype(np.int16).tobytes()
        except Exception as e:
            logger.warning("PyAV resampler failed (%s); using linear interpolation", e)
    out_len = int(round(len(samples) * dst_rate / src_rate))
    if out_len < 1:
        return b""
    src_idx = np.arange(out_len) * (len(samples) - 1) / (out_len - 1)
    resampled = np.interp(src_idx, np.arange(len(samples)), samples.astype(np.float32))
    return resampled.astype(np.int16).tobytes()


class LocalTranscriptService:
    """
    Background STT pipeline for both sides of a conversation.

    Accuracy-first design (everything runs in the background, so latency is
    not the priority). Whisper is far more accurate on coherent, turn-sized
    audio than on fragments, so both sides buffer and transcribe whole turns:

    - User audio accumulates into a turn buffer. A turn ends after ~2 s of
      silence or when `flush_user_turn` is called (the model started
      replying), and the whole turn is transcribed in one shot.
    - Model audio is buffered whole per turn and transcribed in one chunk on
      `flush_model_turn`, with faster-whisper's Silero `vad_filter` stripping
      pauses/trailing silence before decoding.
    - All whisper inference disables `condition_on_previous_text` (prevents
      repetition loops) and runs serialized, and results are emitted in
      conversation order: a model turn waits for any in-flight user turn.
    """

    def __init__(
        self,
        model_size: str = "small",
        compute_type: str = "int8",
        device: str = "cpu",
        language: Optional[str] = "en",
        on_result: Optional[Callable[[str, str], None]] = None,
    ):
        self.model_size = model_size
        self.compute_type = compute_type
        self.device = device
        self.language = language
        self.on_result = on_result

        self._user_queue: "queue.Queue[bytes]" = queue.Queue()
        self._model_queue: "queue.Queue[bytes]" = queue.Queue()
        self._flush_model = threading.Event()
        self._flush_user = threading.Event()
        self._stop = threading.Event()

        # Serializes whisper inference (CTranslate2 models are not safe for
        # concurrent use) and result emission.
        self._whisper_lock = threading.Lock()
        self._emit_lock = threading.Lock()

        # Set while the user worker is transcribing a turn, and a handshake
        # event that the model worker waits on so a model reply is always
        # transcribed after the user turn that triggered it.
        self._user_transcribing = threading.Event()
        self._user_turn_done = threading.Event()
        self._user_turn_done.set()

        self._model = None  # lazy faster-whisper model
        self._user_thread: Optional[threading.Thread] = None
        self._model_thread: Optional[threading.Thread] = None

        # VAD state
        self._user_buffer = bytearray()
        self._silence_bytes = 0
        self._model_buffer = bytearray()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background transcription workers (idempotent)."""
        if self._user_thread and self._user_thread.is_alive():
            return
        self._stop.clear()
        self._user_thread = threading.Thread(
            target=self._user_worker, daemon=True, name="stt-user"
        )
        self._model_thread = threading.Thread(
            target=self._model_worker, daemon=True, name="stt-model"
        )
        self._user_thread.start()
        self._model_thread.start()
        logger.info(
            "Local transcript service started (faster-whisper '%s' %s)",
            self.model_size, self.compute_type,
        )

    def stop(self) -> None:
        """Stop workers, transcribing any trailing audio (idempotent)."""
        self._stop.set()
        self._flush_model.set()
        self._user_queue.put(b"")  # wake the user worker so it can flush
        for thread in (self._user_thread, self._model_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        logger.info("Local transcript service stopped")

    def feed_user_audio(self, pcm_16k: bytes) -> None:
        """Feed a 16 kHz PCM chunk from the microphone (any thread)."""
        if pcm_16k:
            self._user_queue.put(pcm_16k)

    def feed_model_audio(self, pcm_24k: bytes) -> None:
        """Feed a 24 kHz PCM chunk of the robot's own output (any thread)."""
        if pcm_24k:
            self._model_queue.put(pcm_24k)

    def flush_user_turn(self) -> None:
        """Signal that the user's current turn is over (e.g. the model began
        replying), so its audio can be transcribed now. Idempotent."""
        self._flush_user.set()
        # Model turns must wait for this user turn to be transcribed.
        self._user_turn_done.clear()

    def flush_model_turn(self) -> None:
        """Signal that the current model turn is over; transcribe its audio."""
        self._flush_model.set()

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def _user_worker(self) -> None:
        """Accumulate mic audio into turns and transcribe each turn in order."""
        while not self._stop.is_set():
            try:
                chunk = self._user_queue.get(timeout=0.2)
            except queue.Empty:
                chunk = None
            # A flush request (the model began replying) must be honored
            # promptly even while mic chunks keep flowing — the microphone
            # feeds continuously, so the queue almost never goes empty.
            if self._flush_user.is_set():
                self._flush_user.clear()
                if chunk:
                    self._feed_user_chunk(chunk)
                # Capture any chunks that arrived around the flush signal.
                while True:
                    try:
                        extra = self._user_queue.get_nowait()
                    except queue.Empty:
                        break
                    if not extra:
                        break
                    self._feed_user_chunk(extra)
                self._flush_user_turn()
            elif chunk:
                self._feed_user_chunk(chunk)
        self._flush_user_turn()

    def _model_worker(self) -> None:
        """Buffer model audio and transcribe one turn per flush request."""
        while True:
            if self._flush_model.is_set():
                self._flush_model.clear()
                self._process_model_turn()
            if self._stop.is_set():
                break
            try:
                chunk = self._model_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk:
                self._model_buffer += chunk

    # ------------------------------------------------------------------
    # VAD + transcription
    # ------------------------------------------------------------------

    def _feed_user_chunk(self, chunk: bytes) -> None:
        samples = np.frombuffer(chunk, dtype=np.int16)
        if len(samples) == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
        if rms >= _VAD_SPEECH_THRESHOLD:
            self._user_buffer += chunk
            self._silence_bytes = 0
        elif self._user_buffer:
            self._user_buffer += chunk  # trailing silence for whisper context
            self._silence_bytes += len(chunk)
            silence_limit = int(_VAD_SILENCE_SECONDS * USER_SAMPLE_RATE * 2)
            if self._silence_bytes >= silence_limit:
                self._flush_user_turn()
        # Leading silence before any speech is dropped.

    def _flush_user_turn(self) -> None:
        try:
            if not self._user_buffer:
                return
            audio = bytes(self._user_buffer)
            self._user_buffer.clear()
            self._silence_bytes = 0
            min_bytes = int(_MIN_UTTERANCE_SECONDS * USER_SAMPLE_RATE * 2)
            if len(audio) < min_bytes:
                return
            self._user_transcribing.set()
            try:
                text = self._transcribe(audio, vad_filter=True)
                if text:
                    self._emit("user", text)
            except Exception as e:
                logger.warning("User STT failed: %s", e)
            finally:
                self._user_transcribing.clear()
        finally:
            # Always release the model worker, even if there was no audio.
            self._user_turn_done.set()

    def _process_model_turn(self) -> None:
        # Keep the transcript in order: a model reply belongs after the user
        # turn that triggered it. The client clears `_user_turn_done` when the
        # model begins replying; the user worker re-sets it after its
        # transcription completes.
        while not self._user_turn_done.is_set() and not self._stop.is_set():
            time.sleep(0.1)
        # Capture anything that arrived while we waited, so a turn is never
        # transcribed missing its tail chunks (and tails never leak into the
        # next turn's buffer).
        while True:
            try:
                chunk = self._model_queue.get_nowait()
            except queue.Empty:
                break
            if chunk:
                self._model_buffer += chunk
        if not self._model_buffer:
            return
        audio = bytes(self._model_buffer)
        self._model_buffer.clear()
        min_bytes = int(_MIN_UTTERANCE_SECONDS * MODEL_SAMPLE_RATE * 2)
        if len(audio) < min_bytes:
            return
        try:
            pcm_16k = resample_pcm16(audio, MODEL_SAMPLE_RATE, USER_SAMPLE_RATE)
            text = self._transcribe(pcm_16k, vad_filter=True)
            if text:
                self._emit("model", text)
        except Exception as e:
            logger.warning("Model STT failed: %s", e)

    def _get_model(self):
        """Lazily load the faster-whisper model (downloads on first use)."""
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.warning(
                    "faster-whisper is not installed; skipping local transcripts. "
                    "Install it with: pip install faster-whisper"
                )
                raise
            logger.info(
                "Loading faster-whisper model '%s' (%s on %s)...",
                self.model_size, self.compute_type, self.device,
            )
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def _transcribe(self, audio: bytes, *, vad_filter: bool = False) -> str:
        """
        Transcribe 16 kHz 16-bit PCM mono bytes to text.

        `vad_filter` lets faster-whisper's Silero VAD strip non-speech
        (pauses, trailing silence) before decoding — used on the model side,
        whose whole-turn buffers are the main hallucination source. Decoding
        never conditions on previous text, which prevents repetition loops.
        """
        model = self._get_model()
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        with self._whisper_lock:
            segments, _info = model.transcribe(
                samples,
                language=self.language,
                condition_on_previous_text=False,
                hallucination_silence_threshold=_HALLUCINATION_SILENCE_THRESHOLD,
                vad_filter=vad_filter,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        return text

    def _emit(self, role: str, text: str) -> None:
        with self._emit_lock:
            logger.info("STT [%s]: %s", role, text)
            if self.on_result:
                try:
                    self.on_result(role, text)
                except Exception as e:
                    logger.warning("STT on_result callback failed: %s", e)
