"""
Cubey services package.

On-device AI services, each backed by a local model:
  - stt:            faster-whisper speech-to-text for conversation history
  - embeddings:     fastembed semantic embeddings for message search
  - message_service: semantic search over stored messages
"""

from src.services.embeddings import EmbeddingService
from src.services.message_service import search_messages
from src.services.stt import LocalTranscriptService

__all__ = [
    "EmbeddingService",
    "LocalTranscriptService",
    "search_messages",
]
