"""
Local text embedding service backed by fastembed (ONNX).

Wraps fastembed's TextEmbedding so the rest of the app never talks to it
directly and nothing breaks if the dependency is missing. The model is loaded
lazily (downloads from Hugging Face on first use, then cached).

Embedding conventions:
  - documents (stored messages)  -> embed_documents(): plain embedding
  - queries (search inputs)      -> embed_query(): fastembed applies the
    model-appropriate query instruction (BGE requires a search prefix)
"""

import logging
import threading
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Thread-safe wrapper around a fastembed embedding model."""

    def __init__(self, model_name: Optional[str] = None):
        from src.config import config

        self.model_name = model_name or config.embedding_model
        self._model = None
        self._lock = threading.Lock()
        self._dimension: Optional[int] = None

    def _get_model(self):
        """Lazily load the fastembed model (downloads on first use)."""
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError:
                logger.warning(
                    "fastembed is not installed; embeddings are unavailable. "
                    "Install it with: pip install fastembed"
                )
                raise
            logger.info("Loading embedding model '%s'...", self.model_name)
            self._model = TextEmbedding(self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        """Vector dimension of the embedding model (loads the model)."""
        if self._dimension is None:
            self._dimension = int(self.embed_documents([""])[0].shape[0])
        return self._dimension

    def embed_documents(self, texts: Iterable[str]) -> list[np.ndarray]:
        """Embed texts as *documents* (no query instruction)."""
        model = self._get_model()
        with self._lock:
            return [np.asarray(vec, dtype=np.float32) for vec in model.embed(texts)]

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single *query* (search instruction applied by fastembed)."""
        model = self._get_model()
        with self._lock:
            return np.asarray(next(iter(model.query_embed([text]))), dtype=np.float32)

    def embed(self, text: str) -> np.ndarray:
        """Embed a single text as a document (alias for one-shot use)."""
        return self.embed_documents([text])[0]
