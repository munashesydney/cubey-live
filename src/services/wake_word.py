"""
Wake Word Detection Service using Sherpa-ONNX (Next-Gen Kaldi).
Provides open-vocabulary, offline keyword spotting for Cubey on Raspberry Pi 5 / desktop.
No custom model training required; keywords are defined at runtime in keywords.txt.
"""

import logging
import math
import os
import queue
import shutil
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Union

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger(__name__)

# Official pre-trained open-vocabulary Zipformer-GigaSpeech KWS model (approx 15MB tar.bz2, 3.3M params)
_DEFAULT_MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
_DEFAULT_MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{_DEFAULT_MODEL_NAME}.tar.bz2"
)


class WakeWordService:
    """
    Continuous background wake-word detection service powered by Sherpa-ONNX.
    Accepts 16kHz PCM audio chunks from AudioRecorder and invokes callbacks upon detection.
    """

    def __init__(
        self,
        model_dir: Optional[Union[str, Path]] = None,
        wake_words: Union[str, List[str]] = "HEY CUBEY, OK CUBEY, HI CUBEY, CUBEY, WAKE UP, HEY Q BEE, Q BEE, HEY CUBE Y, CUBE Y, HELLO CUBEY",
        threshold: float = 0.15,
        score: float = 2.0,
        gain: float = 2.0,
        num_threads: int = 2,
        on_wake_word: Optional[Callable[[str], None]] = None,
        auto_download: bool = True,
    ):
        self.model_dir = Path(model_dir) if model_dir else Path("data/models/sherpa-onnx-kws")
        self.wake_words = self._parse_wake_words(wake_words)
        self.threshold = threshold
        self.score = score
        self.gain = max(0.1, float(gain))
        self.num_threads = num_threads
        self.on_wake_word = on_wake_word
        self.auto_download = auto_download

        self.spotter = None
        self.stream = None
        self._is_running = False
        self._is_paused = False
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._last_detection_time = 0.0
        self._cooldown_seconds = 2.0  # Prevent double triggers within 2s
        self._token_to_display_map: dict = {}

    @staticmethod
    def _parse_wake_words(words: Union[str, List[str]]) -> List[str]:
        """Normalize comma-separated string or list of wake words into clean uppercase phrases."""
        if isinstance(words, str):
            items = words.split(",")
        else:
            items = list(words)
        cleaned = [w.strip().upper() for w in items if w.strip()]
        return cleaned or ["HEY CUBEY", "OK CUBEY", "HI CUBEY", "CUBEY", "WAKE UP"]

    def _tokenize_phrase(self, phrase: str, tokens_path: Optional[str] = None) -> str:
        """
        Encodes a raw text phrase (e.g. 'HEY CUBEY') into the space-delimited BPE token string
        required by Sherpa-ONNX (e.g. ' \u2581HE Y \u2581C U B E Y').
        Uses sentencepiece if available, or greedy longest-prefix matching against tokens.txt.
        """
        # Try sentencepiece if bpe.model is present
        bpe_path = self.model_dir / "bpe.model"
        if not bpe_path.is_file():
            for f in self.model_dir.rglob("*bpe*.model"):
                if f.is_file():
                    bpe_path = f
                    break

        if bpe_path.is_file():
            try:
                import sentencepiece as spm
                sp = spm.SentencePieceProcessor()
                sp.load(str(bpe_path))
                pieces = sp.encode_as_pieces(phrase.strip().upper())
                if pieces:
                    return " ".join(pieces)
            except Exception as e:
                logger.debug("Sentencepiece encoding fallback: %s", e)

        # Fallback: Vocabulary-based greedy longest-prefix tokenizer on tokens.txt
        vocab = set()
        t_path = Path(tokens_path) if tokens_path else (self.model_dir / "tokens.txt")
        if not t_path.is_file():
            for f in self.model_dir.rglob("*tokens*.txt"):
                if f.is_file():
                    t_path = f
                    break

        if t_path.is_file():
            try:
                with open(t_path, "r", encoding="utf-8") as fp:
                    for line in fp:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            vocab.add(parts[0])
            except Exception as e:
                logger.warning("Could not load tokens.txt for tokenization: %s", e)

        if not vocab:
            # If no vocab loaded, return raw phrase as fallback
            return phrase

        words = phrase.strip().upper().split()
        all_pieces = []
        for word in words:
            target = "\u2581" + word
            i = 0
            while i < len(target):
                matched = False
                for j in range(len(target), i, -1):
                    sub = target[i:j]
                    if sub in vocab:
                        all_pieces.append(sub)
                        i = j
                        matched = True
                        break
                if not matched:
                    all_pieces.append(target[i])
                    i += 1
        return " ".join(all_pieces)

    def _ensure_model_files(self) -> dict:
        """
        Locates or auto-downloads the ONNX model files.
        Returns a dict with paths to tokens, encoder, decoder, joiner.
        """
        self.model_dir.mkdir(parents=True, exist_ok=True)

        def find_file(prefix: str, suffix: str) -> Optional[Path]:
            # Direct match
            direct = self.model_dir / f"{prefix}{suffix}"
            if direct.is_file():
                return direct
            # Search in subdirectory if extracted as folder
            for f in self.model_dir.rglob(f"*{prefix}*{suffix}"):
                if f.is_file():
                    return f
            return None

        tokens_path = find_file("tokens", ".txt")
        encoder_path = find_file("encoder", ".onnx")
        decoder_path = find_file("decoder", ".onnx")
        joiner_path = find_file("joiner", ".onnx")

        has_all = all([tokens_path, encoder_path, decoder_path, joiner_path])

        if not has_all:
            if not self.auto_download:
                raise FileNotFoundError(
                    f"Sherpa-ONNX KWS model files missing in {self.model_dir} and auto_download is disabled."
                )

            logger.info("Downloading Sherpa-ONNX KWS model from %s...", _DEFAULT_MODEL_URL)
            tar_path = self.model_dir / f"{_DEFAULT_MODEL_NAME}.tar.bz2"
            try:
                urllib.request.urlretrieve(_DEFAULT_MODEL_URL, tar_path)
                logger.info("Extracting model archive to %s...", self.model_dir)
                with tarfile.open(tar_path, "r:bz2") as tar:
                    tar.extractall(path=self.model_dir)
                try:
                    tar_path.unlink()
                except Exception:
                    pass
                logger.info("Sherpa-ONNX KWS model ready.")
            except Exception as e:
                logger.error("Failed to auto-download Sherpa-ONNX KWS model: %s", e)
                raise RuntimeError(
                    f"Failed to download wake-word model: {e}. "
                    f"Please run setup_pi.sh or manually download {_DEFAULT_MODEL_URL} to {self.model_dir}"
                ) from e

            tokens_path = find_file("tokens", ".txt")
            encoder_path = find_file("encoder", ".onnx")
            decoder_path = find_file("decoder", ".onnx")
            joiner_path = find_file("joiner", ".onnx")

            if not all([tokens_path, encoder_path, decoder_path, joiner_path]):
                raise FileNotFoundError(
                    f"Downloaded model archive did not contain expected ONNX files in {self.model_dir}."
                )

        return {
            "tokens": str(tokens_path),
            "encoder": str(encoder_path),
            "decoder": str(decoder_path),
            "joiner": str(joiner_path),
        }

    def _generate_keywords_file(self, tokens_path: Optional[str] = None) -> Path:
        """
        Creates or updates keywords.txt for Sherpa-ONNX by tokenizing configured wake words,
        adding boosting scores and acoustic thresholds.
        """
        keywords_path = self.model_dir / "keywords.txt"
        lines = []
        self._token_to_display_map.clear()

        for word in self.wake_words:
            tokenized = self._tokenize_phrase(word, tokens_path=tokens_path)
            # Format: TOKEN1 TOKEN2 ... :score #threshold
            line = f"{tokenized} :{self.score:.1f} #{self.threshold:.2f}"
            lines.append(line)
            # Map raw tokens to human-readable phrase
            self._token_to_display_map[tokenized] = word
            self._token_to_display_map[tokenized.replace(" ", "")] = word

        content = "\n".join(lines) + "\n"
        keywords_path.write_text(content, encoding="utf-8")
        logger.info(
            "Generated tokenized KWS keywords file at %s with %d phrases: %s",
            keywords_path,
            len(lines),
            self.wake_words,
        )
        return keywords_path

    def _clean_detected_keyword(self, raw_kw: str) -> str:
        """Translates raw tokenized spotter result into human-readable wake word string."""
        if not raw_kw:
            return ""
        # Check direct token map
        if raw_kw in self._token_to_display_map:
            return self._token_to_display_map[raw_kw]
        no_spaces = raw_kw.replace(" ", "")
        if no_spaces in self._token_to_display_map:
            return self._token_to_display_map[no_spaces]

        # Clean \u2581 SentencePiece markers
        cleaned = raw_kw.replace("\u2581", " ").strip()
        cleaned_no_spaces = cleaned.replace(" ", "")

        for w in self.wake_words:
            if w.replace(" ", "").upper() == cleaned_no_spaces.upper():
                return w

        return cleaned or raw_kw

    def initialize(self) -> bool:
        """Initializes the Sherpa-ONNX KeywordSpotter engine."""
        try:
            import sherpa_onnx
        except ImportError:
            logger.warning(
                "sherpa-onnx package is not installed. Wake word detection is disabled. "
                "Install with 'pip install sherpa-onnx'."
            )
            return False

        try:
            model_files = self._ensure_model_files()
            keywords_file = self._generate_keywords_file(tokens_path=model_files.get("tokens"))

            logger.info("Initializing Sherpa-ONNX KeywordSpotter with %d threads...", self.num_threads)
            self.spotter = sherpa_onnx.KeywordSpotter(
                tokens=model_files["tokens"],
                encoder=model_files["encoder"],
                decoder=model_files["decoder"],
                joiner=model_files["joiner"],
                keywords_file=str(keywords_file),
                num_threads=self.num_threads,
                provider="cpu",
            )
            self.stream = self.spotter.create_stream()
            logger.info("Sherpa-ONNX KeywordSpotter successfully initialized.")
            return True
        except Exception as e:
            logger.error("Failed to initialize Sherpa-ONNX KeywordSpotter: %s", e, exc_info=True)
            self.spotter = None
            self.stream = None
            return False

    def start(self) -> None:
        """Start background processing worker thread."""
        if self._is_running:
            return

        if self.spotter is None:
            if not self.initialize():
                logger.warning("WakeWordService cannot start: spotter not initialized.")
                return

        self._is_running = True
        self._is_paused = False
        self._worker_thread = threading.Thread(
            target=self._process_loop,
            daemon=True,
            name="WakeWordWorker",
        )
        self._worker_thread.start()
        logger.info("WakeWordService worker thread started.")

    def stop(self) -> None:
        """Stop background processing worker thread."""
        self._is_running = False
        self._is_paused = True
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None
        logger.info("WakeWordService stopped.")

    def pause(self) -> None:
        """Pause wake word detection (e.g. during active Gemini Live speech)."""
        with self._lock:
            self._is_paused = True
            # Clear pending audio queue
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break
            if self.spotter and self.stream:
                try:
                    self.spotter.reset_stream(self.stream)
                except Exception:
                    pass
        logger.info("WakeWordService paused.")

    def resume(self) -> None:
        """Resume wake word detection (e.g. when Gemini Live session finishes)."""
        with self._lock:
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break
            if self.spotter and self.stream:
                try:
                    self.spotter.reset_stream(self.stream)
                except Exception:
                    pass
            self._is_paused = False
        logger.info("WakeWordService resumed.")

    def process_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """
        Ingest one 16kHz PCM16 mono chunk from AudioRecorder callback.
        Non-blocking, drops frames if consumer falls behind.
        """
        if not self._is_running or self._is_paused or not pcm16_bytes:
            return

        try:
            self._audio_queue.put_nowait(pcm16_bytes)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(pcm16_bytes)
            except Exception:
                pass

    def _process_loop(self) -> None:
        """Dedicated worker loop feeding audio chunks to Sherpa-ONNX."""
        while self._is_running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._is_paused or not self.spotter or not self.stream:
                continue

            try:
                if np is None:
                    continue
                # Convert int16 PCM bytes to float32 samples in [-1.0, 1.0]
                samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                if self.gain != 1.0:
                    samples = np.clip(samples * self.gain, -1.0, 1.0)
                if len(samples) == 0:
                    continue

                display_to_dispatch: Optional[str] = None
                with self._lock:
                    if self._is_paused or not self.spotter or not self.stream:
                        continue

                    self.stream.accept_waveform(16000, samples)

                    while self.spotter.is_ready(self.stream):
                        self.spotter.decode_stream(self.stream)

                    result = self.spotter.get_result(self.stream)
                    detected_keyword = ""
                    if isinstance(result, str):
                        detected_keyword = result.strip()
                    elif result and hasattr(result, "keyword"):
                        detected_keyword = str(result.keyword).strip()
                    elif result:
                        detected_keyword = str(result).strip()

                    if detected_keyword:
                        display_keyword = self._clean_detected_keyword(detected_keyword)
                        now = time.time()
                        if now - self._last_detection_time >= self._cooldown_seconds:
                            self._last_detection_time = now
                            logger.info("🎯 Wake Word Spotted: '%s' (raw: '%s')", display_keyword, detected_keyword)
                            self.spotter.reset_stream(self.stream)
                            display_to_dispatch = display_keyword
                        else:
                            self.spotter.reset_stream(self.stream)

                if display_to_dispatch:
                    self._dispatch_detection(display_to_dispatch)

            except Exception as e:
                logger.error("Error in WakeWordService processing loop: %s", e, exc_info=True)

    def _dispatch_detection(self, keyword: str) -> None:
        """Trigger callback when wake word is spotted."""
        if self.on_wake_word:
            try:
                self.on_wake_word(keyword)
            except Exception as e:
                logger.error("Error in on_wake_word callback: %s", e, exc_info=True)
