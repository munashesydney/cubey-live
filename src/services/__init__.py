"""
Cubey services package.

On-device AI and robot hardware services:
  - wheels_service:  UART motor control & cliff safety telemetry
  - embeddings:      fastembed semantic embeddings for message search
  - message_service: semantic search over stored messages
  - memory_service:  durable memories (add/update/search)
  - local_llm:       llama.cpp local chat model
"""

__all__ = [
    "EmbeddingService",
    "TelemetryData",
    "WheelsService",
    "add_memory",
    "search_memories",
    "search_messages",
    "update_memory",
]


def __getattr__(name: str):
    if name in ("WheelsService", "TelemetryData"):
        from src.services.wheels_service import TelemetryData, WheelsService
        return WheelsService if name == "WheelsService" else TelemetryData
    if name == "EmbeddingService":
        from src.services.embeddings import EmbeddingService
        return EmbeddingService
    if name in ("add_memory", "search_memories", "update_memory"):
        from src.services import memory_service
        return getattr(memory_service, name)
    if name == "search_messages":
        from src.services.message_service import search_messages
        return search_messages
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
