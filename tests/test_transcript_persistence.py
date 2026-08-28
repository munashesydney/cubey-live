"""Tests for transcript persistence isolation from Gemini Live audio."""

from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

from src.db import MessageRole
from src.services.transcript_persistence import TranscriptPersistenceService


class _FakeEmbeddingService:
    model_name = "test-embedding"

    def __init__(self, embedded: threading.Event) -> None:
        self.embedded = embedded

    def embed(self, text: str):
        self.embedded.set()
        return b"vector"


class TranscriptPersistenceTests(unittest.TestCase):
    def test_message_handoff_returns_while_database_is_blocked(self) -> None:
        database_started = threading.Event()
        release_database = threading.Event()
        embedded = threading.Event()

        def slow_create(conversation_id, *, role, content):
            database_started.set()
            release_database.wait(timeout=1)
            return SimpleNamespace(id=7, content=content)

        service = TranscriptPersistenceService(_FakeEmbeddingService(embedded))
        with (
            patch(
                "src.services.transcript_persistence.create_message",
                side_effect=slow_create,
            ),
            patch("src.services.transcript_persistence.save_message_embedding"),
        ):
            service.start()
            try:
                started = time.monotonic()
                queued = service.enqueue_message(42, MessageRole.USER, "hello")
                elapsed = time.monotonic() - started

                self.assertTrue(queued)
                self.assertLess(elapsed, 0.05)
                self.assertTrue(database_started.wait(timeout=1))
            finally:
                release_database.set()
                service.stop()

    def test_embeddings_wait_until_realtime_session_ends(self) -> None:
        persisted = threading.Event()
        embedded = threading.Event()
        conversation_ended = threading.Event()
        operations: list[str] = []

        def create(conversation_id, *, role, content):
            operations.append("message")
            persisted.set()
            return SimpleNamespace(id=8, content=content)

        def end(conversation_id):
            operations.append("end")
            conversation_ended.set()
            return SimpleNamespace(status="completed")

        service = TranscriptPersistenceService(_FakeEmbeddingService(embedded))
        with (
            patch("src.services.transcript_persistence.create_message", side_effect=create),
            patch("src.services.transcript_persistence.end_conversation", side_effect=end),
            patch("src.services.transcript_persistence.save_message_embedding"),
        ):
            service.start()
            try:
                service.set_realtime_active(True)
                self.assertTrue(
                    service.enqueue_message(42, MessageRole.USER, "remember this")
                )
                self.assertTrue(service.enqueue_end(42))

                self.assertTrue(persisted.wait(timeout=1))
                self.assertTrue(conversation_ended.wait(timeout=1))
                self.assertFalse(embedded.wait(timeout=0.1))
                self.assertEqual(operations, ["message", "end"])

                service.set_realtime_active(False)
                self.assertTrue(embedded.wait(timeout=1))
            finally:
                service.set_realtime_active(False)
                service.stop()


if __name__ == "__main__":
    unittest.main()
