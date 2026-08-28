"""Background persistence for latency-critical Gemini Live transcripts.

The Live asyncio loop must never wait for SQLite or local model inference.
This service therefore uses two ordered worker queues:

* the writer stores transcript rows, titles, and conversation completion;
* the embedder generates semantic vectors after the rows exist.

Embedding work is paused while a Live session is active.  Audio continuity is
more important than making semantic search immediately consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import queue
import threading
from typing import Optional

from src.db import (
    MessageRole,
    create_message,
    end_conversation,
    save_message_embedding,
    update_conversation,
)
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = 2048
_STOP = object()


@dataclass(frozen=True)
class _MessageJob:
    conversation_id: int
    role: MessageRole
    text: str
    title: Optional[str]


@dataclass(frozen=True)
class _EndConversationJob:
    conversation_id: int


@dataclass(frozen=True)
class _EmbeddingJob:
    message_id: int
    text: str


class TranscriptPersistenceService:
    """Persist Live transcripts without blocking the real-time event loop."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        *,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be >= 1")

        self.embedding_service = embedding_service
        self._writer_queue: queue.Queue[object] = queue.Queue(maxsize=queue_size)
        self._embedding_queue: queue.Queue[object] = queue.Queue(maxsize=queue_size)
        self._embeddings_allowed = threading.Event()
        self._embeddings_allowed.set()
        self._stopping = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._writer_thread: Optional[threading.Thread] = None
        self._embedding_thread: Optional[threading.Thread] = None
        self._dropped_writer_jobs = 0
        self._dropped_embedding_jobs = 0

    def start(self) -> None:
        """Start both workers (idempotent)."""
        with self._lifecycle_lock:
            if self._writer_thread and self._writer_thread.is_alive():
                return

            self._stopping.clear()
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name="transcript-writer",
                daemon=True,
            )
            self._embedding_thread = threading.Thread(
                target=self._embedding_loop,
                name="transcript-embedder",
                daemon=True,
            )
            self._writer_thread.start()
            self._embedding_thread.start()
            logger.info("Background transcript persistence workers started")

    def set_realtime_active(self, active: bool) -> None:
        """Pause CPU-heavy embeddings while Live audio has priority."""
        if active:
            self._embeddings_allowed.clear()
        else:
            self._embeddings_allowed.set()

    def enqueue_message(
        self,
        conversation_id: int,
        role: MessageRole,
        text: str,
        *,
        title: Optional[str] = None,
    ) -> bool:
        """Queue one transcript without waiting for SQLite or FastEmbed."""
        clean = text.strip()
        if not clean:
            return True
        return self._put_writer(
            _MessageJob(
                conversation_id=conversation_id,
                role=role,
                text=text,
                title=title,
            )
        )

    def enqueue_end(self, conversation_id: int) -> bool:
        """Queue completion after all earlier transcript writes."""
        return self._put_writer(_EndConversationJob(conversation_id))

    def stop(self, timeout: float = 5.0) -> None:
        """Stop accepting work and best-effort flush both ordered queues."""
        with self._lifecycle_lock:
            writer = self._writer_thread
            embedder = self._embedding_thread
            if writer is None and embedder is None:
                return
            self._stopping.set()
            self._embeddings_allowed.set()

            # Blocking is safe here: stop is called from application shutdown,
            # never from the Live event loop.
            self._writer_queue.put(_STOP)
            if writer:
                writer.join(timeout=timeout)

            self._embedding_queue.put(_STOP)
            if embedder:
                embedder.join(timeout=timeout)

            if writer and writer.is_alive():
                logger.warning("Transcript writer did not finish before shutdown timeout")
            if embedder and embedder.is_alive():
                logger.warning("Transcript embedder did not finish before shutdown timeout")

            self._writer_thread = None
            self._embedding_thread = None

    def _put_writer(self, job: object) -> bool:
        if self._stopping.is_set():
            logger.warning("Discarded transcript persistence job during shutdown")
            return False
        try:
            self._writer_queue.put_nowait(job)
            return True
        except queue.Full:
            self._dropped_writer_jobs += 1
            logger.error(
                "Transcript persistence queue is full; dropped %d job(s)",
                self._dropped_writer_jobs,
            )
            return False

    def _put_embedding(self, job: _EmbeddingJob) -> None:
        try:
            self._embedding_queue.put_nowait(job)
        except queue.Full:
            self._dropped_embedding_jobs += 1
            logger.warning(
                "Deferred embedding queue is full; dropped %d embedding job(s)",
                self._dropped_embedding_jobs,
            )

    def _writer_loop(self) -> None:
        while True:
            job = self._writer_queue.get()
            try:
                if job is _STOP:
                    return
                if isinstance(job, _MessageJob):
                    self._write_message(job)
                elif isinstance(job, _EndConversationJob):
                    self._end_conversation(job)
            except Exception:
                logger.exception("Unexpected transcript writer failure")
            finally:
                self._writer_queue.task_done()

    def _write_message(self, job: _MessageJob) -> None:
        try:
            message = create_message(
                job.conversation_id,
                role=job.role,
                content=job.text,
            )
            if job.title:
                update_conversation(job.conversation_id, title=job.title)
            if job.role in (MessageRole.USER, MessageRole.MODEL):
                self._put_embedding(_EmbeddingJob(message.id, message.content))
        except Exception as exc:
            logger.warning(
                "Failed to persist transcript for conversation #%s: %s",
                job.conversation_id,
                exc,
            )

    @staticmethod
    def _end_conversation(job: _EndConversationJob) -> None:
        try:
            ended = end_conversation(job.conversation_id)
            logger.info(
                "Ended conversation #%s (%s)",
                job.conversation_id,
                ended.status if ended else "missing",
            )
        except Exception as exc:
            logger.error("Failed to end conversation #%s: %s", job.conversation_id, exc)

    def _embedding_loop(self) -> None:
        while True:
            job = self._embedding_queue.get()
            try:
                if job is _STOP:
                    return
                if not isinstance(job, _EmbeddingJob):
                    continue

                # Wake periodically on shutdown, but never start new local
                # inference while a Live conversation owns the audio path.
                while not self._embeddings_allowed.wait(timeout=0.25):
                    if self._stopping.is_set():
                        break
                self._embed_message(job)
            except Exception:
                logger.exception("Unexpected transcript embedding worker failure")
            finally:
                self._embedding_queue.task_done()

    def _embed_message(self, job: _EmbeddingJob) -> None:
        try:
            vector = self.embedding_service.embed(job.text)
            save_message_embedding(
                message_id=job.message_id,
                model_name=self.embedding_service.model_name,
                embedding=vector,
            )
        except Exception as exc:
            logger.warning("Failed to embed message #%s: %s", job.message_id, exc)
