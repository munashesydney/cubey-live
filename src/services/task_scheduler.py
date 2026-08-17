"""
Task scheduler — the "pg-boss for SQLite" worker.

A background thread polls the tasks table for due jobs, hands each to a small
thread pool, and recomputes the next occurrence after every run. One-shot
tasks transition to DONE; interval/cron tasks keep their schedule.

Task execution dispatch:
  - local  -> LocalLLMService.generate() (headless Qwen pipeline with tools)
  - gemini -> stub; real implementation is planned for the future
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from croniter import croniter

from src.ai.prompts.local_llm.local_llm_task_runner import (
    SYSTEM_PROMPT as TASK_RUNNER_SYSTEM_PROMPT,
)
from src.client.tools import ToolContext, build_llama_tools
from src.config import config
from src.db import (
    ConversationSource,
    MessageRole,
    Task,
    TaskModel,
    TaskScheduleType,
    TaskStatus,
    create_conversation,
    create_message,
    due_tasks,
    end_conversation,
    get_task,
    update_task,
)
from src.services.embeddings import EmbeddingService
from src.services.local_llm import LocalLLMService
from src.services.local_tool_history import serialize_tool_trace

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC. Naive values are treated as machine-local."""
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def compute_next_run_at(
    schedule_type: TaskScheduleType,
    run_at: Optional[datetime] = None,
    interval_seconds: Optional[int] = None,
    cron_expr: Optional[str] = None,
    after: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    When a task should run next, given its schedule.

    - one_shot: returns `run_at` (UTC). A past run_at is "due immediately".
    - interval: `after` + interval_seconds (duration-based, tz-agnostic).
    - cron:     the next match of `cron_expr` after `after`, computed in the
                machine's local timezone (so "0 13 * * *" means 1pm local),
                stored as UTC.

    Raises on invalid cron expressions (croniter errors).
    """
    if schedule_type == TaskScheduleType.ONE_SHOT:
        return _as_utc(run_at) if run_at is not None else None

    if schedule_type == TaskScheduleType.INTERVAL:
        if not interval_seconds or interval_seconds <= 0:
            raise ValueError("interval_seconds must be a positive number")
        base = after or utcnow()
        return base + timedelta(seconds=interval_seconds)

    if schedule_type == TaskScheduleType.CRON:
        if not cron_expr or not cron_expr.strip():
            raise ValueError("cron_expr is required for cron schedules")
        base = (after or utcnow()).astimezone()  # local tz
        try:
            return croniter(cron_expr, base).get_next(datetime).astimezone(timezone.utc)
        except Exception as e:
            raise ValueError(f"Invalid cron expression '{cron_expr}': {e}") from e

    raise ValueError(f"Unknown schedule_type '{schedule_type}'")


class TaskScheduler:
    """Background poller that fires due tasks through a thread pool."""

    def __init__(
        self,
        poll_interval: float = 5.0,
        max_workers: int = 2,
    ):
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="task-run"
        )
        self._thread: Optional[threading.Thread] = None
        self._running: set[int] = set()
        self._running_lock = threading.Lock()

        # Lazily created AI services (local model loads on first local task).
        self._llm: Optional[LocalLLMService] = None
        self._embeddings: Optional[EmbeddingService] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="task-scheduler"
        )
        self._thread.start()
        logger.info("Task scheduler started (poll every %ss)", self.poll_interval)

    def stop(self) -> None:
        """Stop the poller (in-flight runs are allowed to finish)."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("Task scheduler stopped")

    # ------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception("Task scheduler tick failed: %s", e)
            self._stop.wait(self.poll_interval)

    def _tick(self) -> None:
        for task in due_tasks(utcnow()):
            with self._running_lock:
                if task.id in self._running:
                    continue
                self._running.add(task.id)
            self._executor.submit(self._run_task, task.id)

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    def _run_task(self, task_id: int) -> None:
        conversation_id: Optional[int] = None
        result_text: Optional[str] = None
        result_status = "error"
        try:
            task = get_task(task_id)
            if task is None:
                return
            # Re-check: the task may have been paused/deleted while queued.
            if task.status != TaskStatus.ACTIVE:
                return

            if task.model == TaskModel.LOCAL:
                conversation_id = self._start_local_task_conversation(task)

            result_status, result_text = self._run_agent(task, conversation_id)
            now = utcnow()

            if task.schedule_type == TaskScheduleType.ONE_SHOT:
                update_task(
                    task_id,
                    status=TaskStatus.DONE,
                    next_run_at=None,
                    last_run_at=now,
                    last_status=result_status,
                    last_result=result_text,
                )
                logger.info("Task #%s done: %s", task_id, result_status)
            else:
                next_run = compute_next_run_at(
                    task.schedule_type,
                    run_at=task.run_at,
                    interval_seconds=task.interval_seconds,
                    cron_expr=task.cron_expr,
                    after=now,
                )
                update_task(
                    task_id,
                    next_run_at=next_run,
                    last_run_at=now,
                    last_status=result_status,
                    last_result=result_text,
                )
                logger.info(
                    "Task #%s ran (%s); next run at %s",
                    task_id, result_status, next_run,
                )
        except Exception as e:
            logger.exception("Task #%s run failed: %s", task_id, e)
            if result_text is None:
                result_text = f"Scheduler error: {e}"
            try:
                update_task(
                    task_id,
                    last_run_at=utcnow(),
                    last_status="error",
                    last_result=result_text,
                )
            except Exception:
                logger.exception("Failed to record task #%s error state", task_id)
        finally:
            if conversation_id is not None and result_text is not None:
                self._finish_local_task_conversation(
                    conversation_id,
                    result_status,
                    result_text,
                )
            with self._running_lock:
                self._running.discard(task_id)

    def _run_agent(
        self,
        task: Task,
        conversation_id: Optional[int] = None,
    ) -> tuple[str, str]:
        """Spawn the AI for a task. Returns (status, result_text)."""
        if task.model == TaskModel.LOCAL:
            try:
                llm = self._get_llm()
                text = llm.generate(
                    messages=[{"role": "user", "content": task.prompt}],
                    system_prompt=TASK_RUNNER_SYSTEM_PROMPT,
                    tools=build_llama_tools("local_task_runner"),
                    tool_context=ToolContext(embedding_service=self._get_embeddings()),
                    on_tool_call=(
                        lambda name, args, result: self._persist_tool_trace(
                            conversation_id, name, args, result
                        )
                    ),
                )
                return "completed", text
            except Exception as e:
                logger.exception("Local task #%s failed: %s", task.id, e)
                return "error", str(e)

        # Gemini execution is planned but not implemented yet.
        logger.info("Task #%s (gemini) skipped: not implemented yet", task.id)
        return "not_implemented", (
            "Gemini task execution is not implemented yet; the task kept its "
            "schedule and will run once support lands."
        )

    @staticmethod
    def _persist_tool_trace(
        conversation_id: Optional[int],
        name: str,
        args: dict,
        result: dict,
    ) -> None:
        if conversation_id is None:
            return
        create_message(
            conversation_id,
            role=MessageRole.EVENT,
            content=serialize_tool_trace(name, args, result),
        )

    def _start_local_task_conversation(self, task: Task) -> Optional[int]:
        """Create the normal Local Chat conversation that will hold this run."""
        try:
            conversation = create_conversation(
                session_id=uuid.uuid4().hex,
                title=task.title,
                metadata={
                    "type": "local_llm",
                    "model": config.local_model_filename,
                    "pipeline": "task_runner",
                    "task_id": task.id,
                },
                source=ConversationSource.LOCAL,
            )
            create_message(
                conversation.id,
                role=MessageRole.USER,
                content=task.prompt,
            )
            return conversation.id
        except Exception as e:
            logger.warning("Failed to create Local Chat for task #%s: %s", task.id, e)
            return None

    @staticmethod
    def _finish_local_task_conversation(
        conversation_id: int,
        status: str,
        result_text: str,
    ) -> None:
        """Persist the task response and close its Local Chat conversation."""
        try:
            content = result_text if status == "completed" else f"Task failed: {result_text}"
            create_message(
                conversation_id,
                role=MessageRole.MODEL,
                content=content,
            )
            end_conversation(conversation_id)
        except Exception as e:
            logger.warning(
                "Failed to finish Local Chat conversation #%s: %s",
                conversation_id,
                e,
            )

    # ------------------------------------------------------------------
    # lazy services
    # ------------------------------------------------------------------

    def _get_llm(self) -> LocalLLMService:
        if self._llm is None:
            self._llm = LocalLLMService(
                repo_id=config.local_model_repo_id,
                filename=config.local_model_filename,
                n_ctx=config.local_model_n_ctx,
                default_system_prompt=config.local_model_system_prompt,
            )
        return self._llm

    def _get_embeddings(self) -> EmbeddingService:
        if self._embeddings is None:
            self._embeddings = EmbeddingService(model_name=config.embedding_model)
        return self._embeddings
