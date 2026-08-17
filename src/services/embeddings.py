"""
Local text embedding service backed by fastembed (ONNX).

Wraps fastembed's TextEmbedding with persistent local model storage,
automatic self-healing cache recovery (in case of interrupted downloads),
and thread-safe lazy loading / pre-warming.
"""

import logging
from pathlib import Path
import shutil
import threading
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Thread-safe wrapper around a fastembed embedding model with self-healing cache."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        cache_dir: Optional[Path | str] = None,
    ):
        from src.config import config

        self.model_name = model_name or config.embedding_model
        self.cache_dir = (
            Path(cache_dir)
            if cache_dir is not None
            else (config.database_path.parent / "models" / "fastembed")
        )
        self._model = None
        self._lock = threading.Lock()
        self._dimension: Optional[int] = None
        self._failed_permanently = False

    def prewarm(self) -> None:
        """Eagerly load the model in the background if possible."""
        try:
            self._get_model()
        except Exception as e:
            logger.warning("Embedding prewarm failed: %s", e)

    def _get_model(self):
        """Lazily load fastembed model with self-healing recovery if cache is corrupted."""
        if self._failed_permanently:
            return None

        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model
            if self._failed_permanently:
                return None

            try:
                from fastembed import TextEmbedding
            except ImportError:
                logger.warning(
                    "fastembed is not installed; embeddings are unavailable. "
                    "Install it with: pip install fastembed"
                )
                self._failed_permanently = True
                return None

            self.cache_dir.mkdir(parents=True, exist_ok=True)

            # Attempt 1: Normal load from persistent cache
            try:
                logger.info("Loading embedding model '%s' (cache: %s)...", self.model_name, self.cache_dir)
                self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
                return self._model
            except Exception as first_err:
                logger.warning(
                    "Failed to load embedding model '%s' (%s). "
                    "Attempting self-healing cache cleanup and redownload...",
                    self.model_name,
                    first_err,
                )

            # Self-healing recovery: Purge corrupt model cache and re-download
            try:
                if self.cache_dir.exists():
                    for item in self.cache_dir.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)

                self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
                logger.info("Successfully recovered and loaded embedding model '%s'.", self.model_name)
                return self._model
            except Exception as final_err:
                logger.error(
                    "Self-healing embedding model load failed for '%s': %s. "
                    "Disabling embeddings for this session.",
                    self.model_name,
                    final_err,
                )
                self._failed_permanently = True
                return None

    @property
    def dimension(self) -> int:
        """Vector dimension of the embedding model."""
        if self._dimension is None:
            model = self._get_model()
            if model is not None:
                try:
                    self._dimension = int(self.embed_documents([""])[0].shape[0])
                except Exception:
                    self._dimension = 384
            else:
                self._dimension = 384
        return self._dimension

    def embed_documents(self, texts: Iterable[str]) -> list[np.ndarray]:
        """Embed texts as *documents* (no query instruction)."""
        model = self._get_model()
        if model is None:
            return [np.zeros(self.dimension, dtype=np.float32) for _ in texts]
        with self._lock:
            return [np.asarray(vec, dtype=np.float32) for vec in model.embed(texts)]

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single *query* (search instruction applied by fastembed)."""
        model = self._get_model()
        if model is None:
            return np.zeros(self.dimension, dtype=np.float32)
        with self._lock:
            return np.asarray(next(iter(model.query_embed([text]))), dtype=np.float32)

    def embed(self, text: str) -> np.ndarray:
        """Embed a single text as a document (alias for one-shot use)."""
        return self.embed_documents([text])[0]
