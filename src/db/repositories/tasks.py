"""
CRUD operations for the tasks table.

Same session contract as the other repositories. `update_task` uses a UNSET
sentinel so fields can be explicitly cleared to NULL (needed when a task
switches schedule types).
"""

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Task, TaskModel, TaskScheduleType, TaskStatus
from src.db.session import get_session

logger = logging.getLogger(__name__)

# Sentinel: a field left as UNSET is unchanged; pass None explicitly to clear.
UNSET = object()


def create_task(
    title: str,
    prompt: str,
    model: TaskModel,
    schedule_type: TaskScheduleType,
    run_at: Optional[datetime] = None,
    interval_seconds: Optional[int] = None,
    cron_expr: Optional[str] = None,
    next_run_at: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> Task:
    """Create a new ACTIVE task row."""

    def _create(s: Session) -> Task:
        task = Task(
            title=title,
            prompt=prompt,
            model=model,
            schedule_type=schedule_type,
            run_at=run_at,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
            next_run_at=next_run_at,
        )
        s.add(task)
        s.flush()
        return task

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def get_task(
    task_id: int,
    session: Optional[Session] = None,
) -> Optional[Task]:
    """Fetch a single task by primary key."""

    def _get(s: Session) -> Optional[Task]:
        return s.get(Task, task_id)

    if session is not None:
        return _get(session)
    with get_session() as s:
        return _get(s)


def update_task(
    task_id: int,
    *,
    title: Any = UNSET,
    prompt: Any = UNSET,
    model: Any = UNSET,
    schedule_type: Any = UNSET,
    run_at: Any = UNSET,
    interval_seconds: Any = UNSET,
    cron_expr: Any = UNSET,
    next_run_at: Any = UNSET,
    status: Any = UNSET,
    last_run_at: Any = UNSET,
    last_status: Any = UNSET,
    last_result: Any = UNSET,
    session: Optional[Session] = None,
) -> Optional[Task]:
    """Update a task. UNSET fields are left unchanged; pass None explicitly
    to clear a field. Returns None if the task does not exist."""

    def _update(s: Session) -> Optional[Task]:
        task = s.get(Task, task_id)
        if task is None:
            return None
        if title is not UNSET:
            task.title = title
        if prompt is not UNSET:
            task.prompt = prompt
        if model is not UNSET:
            task.model = model
        if schedule_type is not UNSET:
            task.schedule_type = schedule_type
        if run_at is not UNSET:
            task.run_at = run_at
        if interval_seconds is not UNSET:
            task.interval_seconds = interval_seconds
        if cron_expr is not UNSET:
            task.cron_expr = cron_expr
        if next_run_at is not UNSET:
            task.next_run_at = next_run_at
        if status is not UNSET:
            task.status = status
        if last_run_at is not UNSET:
            task.last_run_at = last_run_at
        if last_status is not UNSET:
            task.last_status = last_status
        if last_result is not UNSET:
            task.last_result = last_result
        return task

    if session is not None:
        return _update(session)
    with get_session() as s:
        return _update(s)


def delete_task(
    task_id: int,
    session: Optional[Session] = None,
) -> bool:
    """Hard-delete a task. Returns True if a row was removed."""

    def _delete(s: Session) -> bool:
        task = s.get(Task, task_id)
        if task is None:
            return False
        s.delete(task)
        return True

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)


def list_tasks(
    status: Optional[TaskStatus] = None,
    model: Optional[TaskModel] = None,
    limit: int = 100,
    session: Optional[Session] = None,
) -> list[Task]:
    """List tasks, soonest next run first, optionally filtered."""

    def _list(s: Session) -> list[Task]:
        stmt = select(Task).order_by(Task.next_run_at.asc().nulls_last(), Task.id.asc()).limit(limit)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if model is not None:
            stmt = stmt.where(Task.model == model)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def due_tasks(
    now: datetime,
    limit: int = 20,
    session: Optional[Session] = None,
) -> list[Task]:
    """Active tasks whose next_run_at has arrived, soonest first."""

    def _due(s: Session) -> list[Task]:
        stmt = (
            select(Task)
            .where(
                Task.status == TaskStatus.ACTIVE,
                Task.next_run_at.is_not(None),
                Task.next_run_at <= now,
            )
            .order_by(Task.next_run_at.asc())
            .limit(limit)
        )
        return list(s.scalars(stmt).all())

    if session is not None:
        return _due(session)
    with get_session() as s:
        return _due(s)


def count_tasks(
    status: Optional[TaskStatus] = None,
    session: Optional[Session] = None,
) -> int:
    """Number of tasks, optionally filtered by status."""

    def _count(s: Session) -> int:
        stmt = select(func.count()).select_from(Task)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)
