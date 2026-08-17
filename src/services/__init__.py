"""
Cubey services package.

On-device AI services, each backed by a local model:
  - embeddings:      fastembed semantic embeddings for message search
  - message_service: semantic search over stored messages
  - memory_service:  durable memories (add/update/search)
  - local_llm:       llama.cpp local chat model
"""

from src.services.embeddings import EmbeddingService
from src.services.memory_service import add_memory, search_memories, update_memory
from src.services.message_service import search_messages

__all__ = [
    "EmbeddingService",
    "add_memory",
    "search_memories",
    "search_messages",
    "update_memory",
]
