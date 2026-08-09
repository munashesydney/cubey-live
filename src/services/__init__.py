"""
Cubey services package.

On-device AI services, each backed by a local model:
  - stt:             faster-whisper speech-to-text for conversation history
  - embeddings:      fastembed semantic embeddings for message search
  - message_service: semantic search over stored messages
  - memory_service:  durable memories (add/update/search)
  - local_llm:       llama.cpp local chat model
"""

from src.services.embeddings import EmbeddingService
from src.services.memory_service import add_memory, search_memories, update_memory
from src.services.message_service import search_messages
from src.services.stt import LocalTranscriptService

__all__ = [
    "EmbeddingService",
    "LocalTranscriptService",
    "add_memory",
    "search_memories",
    "search_messages",
    "update_memory",
]
