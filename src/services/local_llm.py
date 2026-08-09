"""
Local LLM Service module.
Handles local Qwen3.5 2B inference via native llama-cpp-python running in memory.
Uses huggingface_hub to auto-download GGUF models.
"""

import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


class LocalLLMService:
    """Service wrapper for interacting with native llama.cpp in-memory engine."""

    def __init__(
        self,
        repo_id: str = "bartowski/Qwen_Qwen3.5-2B-GGUF",
        filename: str = "Qwen_Qwen3.5-2B-Q4_K_M.gguf",
        n_ctx: int = 4096,
        default_system_prompt: str = "You are Qwen3.5, a helpful, intelligent local AI assistant embedded inside Cubey."
    ):
        self.repo_id = repo_id
        self.filename = filename
        self.n_ctx = n_ctx
        self.default_system_prompt = default_system_prompt

        self._llm: Any = None  # Lazy loaded Llama instance
        self._model_path: Optional[str] = None
        self._is_loading = False

        # Guards the one-time model download/load against concurrent callers.
        self._load_lock = threading.Lock()

        self._active_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def check_health(self) -> bool:
        """Return True if model is loaded into memory or is currently downloading."""
        return self._llm is not None or self._is_loading

    def is_generating(self) -> bool:
        """Return True if background generation thread is active."""
        return self._active_thread is not None and self._active_thread.is_alive()

    def stop_generation(self) -> None:
        """Signal active generation stream to halt."""
        self._stop_event.set()

    def _ensure_model_loaded(self) -> None:
        """Blocking call to download and load the model into memory. Runs on worker thread."""
        if self._llm is not None:
            return

        with self._load_lock:
            if self._llm is not None:  # re-check under the lock
                return

            self._is_loading = True
            try:
                logger.info("Checking for local GGUF model: %s / %s", self.repo_id, self.filename)

                # This will use cached file if it exists, otherwise downloads it
                from huggingface_hub import hf_hub_download
                import llama_cpp

                model_dir = PROJECT_ROOT / "data" / "models"
                model_dir.mkdir(parents=True, exist_ok=True)

                self._model_path = hf_hub_download(
                    repo_id=self.repo_id,
                    filename=self.filename,
                    local_dir=str(model_dir),
                )

                logger.info("Loading native llama.cpp model from %s", self._model_path)
                self._llm = llama_cpp.Llama(
                    model_path=self._model_path,
                    n_ctx=self.n_ctx,
                    n_threads=max(1, os.cpu_count() or 4),
                    verbose=False,
                )
                logger.info("Local llama.cpp model loaded successfully.")
            except ImportError:
                raise ImportError(
                    "llama-cpp-python or huggingface-hub is missing. "
                    "Install via: pip install llama-cpp-python huggingface-hub"
                ) from None
            except Exception as e:
                logger.error("Failed to initialize local llama.cpp model: %s", e)
                raise
            finally:
                self._is_loading = False

    def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        temperature: float = 0.7,
    ) -> None:
        """
        Launch streaming chat completion on a background worker thread.
        """
        if self.is_generating():
            if on_error:
                on_error("A generation request is already in progress.")
            return

        self._stop_event.clear()

        sys_prompt = system_prompt if system_prompt is not None else self.default_system_prompt
        payload_messages = []
        if sys_prompt:
            payload_messages.append({"role": "system", "content": sys_prompt})
        payload_messages.extend(messages)

        self._active_thread = threading.Thread(
            target=self._worker_stream,
            args=(payload_messages, temperature, on_token, on_complete, on_error),
            daemon=True
        )
        self._active_thread.start()

    def _worker_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        on_token: Optional[Callable[[str], None]],
        on_complete: Optional[Callable[[str], None]],
        on_error: Optional[Callable[[str], None]],
    ) -> None:
        """Worker thread executing native llama.cpp streaming generation."""
        try:
            self._ensure_model_loaded()
        except Exception as e:
            if on_error:
                on_error(f"Failed to load model: {e}")
            self._active_thread = None
            return

        full_content: List[str] = []

        try:
            # Create streaming generator
            streamer = self._llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature
            )

            for chunk in streamer:
                if self._stop_event.is_set():
                    logger.info("Local LLM generation stopped by user request.")
                    break

                delta = chunk["choices"][0]["delta"]
                if "content" in delta:
                    token = delta["content"]
                    full_content.append(token)
                    if on_token:
                        on_token(token)

            assembled_text = "".join(full_content)
            if on_complete:
                on_complete(assembled_text)

        except Exception as e:
            err_msg = f"Error during local llama.cpp generation: {e}"
            logger.error(err_msg)
            if on_error:
                on_error(err_msg)
        finally:
            self._active_thread = None
