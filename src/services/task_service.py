"""
Task service — task management for the 'tasks' tool and scheduler.

Creates, updates, deletes, and lists scheduled tasks, computing each task's
next run from its schedule. Returns plain dicts suitable for tool responses.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from src.db import (
    TaskModel,
    TaskScheduleType,
    TaskStatus,
    create_task as _create_task_row,
    delete_task as _delete_task_row,
    get_task as _get_task_row,
    list_tasks as _list_task_rows,
    update_task as _update_task_row,
)
from src.db.repositories.tasks import UNSET
from src.services.task_scheduler import compute_next_run_at

logger = logging.getLogger(__name__)


def _parse_run_at(value) -> Optional[datetime]:
    """Accept a datetime or an ISO-8601 string (naive -> machine-local)."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()  # naive -> local tz
        return parsed
    raise ValueError(f"run_at must be an ISO-8601 string or datetime, got {type(value).__name__}")


def _coerce_model(model) -> TaskModel:
    if isinstance(model, TaskModel):
        return model
    try:
        return TaskModel(str(model).lower().strip())
    except ValueError as e:
        raise ValueError(f"Unknown task model '{model}' (expected 'local' or 'gemini')") from e


def _coerce_schedule(schedule_type) -> TaskScheduleType:
    if isinstance(schedule_type, TaskScheduleType):
        return schedule_type
    try:
        return TaskScheduleType(str(schedule_type).lower().strip())
    except ValueError as e:
        raise ValueError(
            f"Unknown schedule_type '{schedule_type}' (expected 'one_shot', 'interval', or 'cron')"
        ) from e


def _validate_schedule(schedule_type: TaskScheduleType, run_at, interval_seconds, cron_expr) -> None:
    if schedule_type == TaskScheduleType.ONE_SHOT and run_at is None:
        raise ValueError("schedule_type 'one_shot' requires run_at")
    if schedule_type == TaskScheduleType.INTERVAL and not interval_seconds:
        raise ValueError("schedule_type 'interval' requires interval_seconds")
    if schedule_type == TaskScheduleType.CRON and not (cron_expr or "").strip():
        raise ValueError("schedule_type 'cron' requires cron_expr")


def _task_dict(task) -> dict:
    return {
        "task_id": task.id,
        "title": task.title,
        "prompt": task.prompt,
        "model": task.model.value,
        "schedule_type": task.schedule_type.value,
        "run_at": task.run_at.isoformat(timespec="seconds") if task.run_at else None,
        "interval_seconds": task.interval_seconds,
        "cron_expr": task.cron_expr,
        "next_run_at": task.next_run_at.isoformat(timespec="seconds") if task.next_run_at else None,
        "status": task.status.value,
        "last_status": task.last_status,
        "last_result": task.last_result,
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def create_task(
    title: str,
    prompt: str,
    model,
    schedule_type,
    run_at=None,
    interval_seconds=None,
    cron_expr=None,
) -> dict:
    """Create a scheduled task. Returns {status, task_id, next_run_at} or
    {status: 'invalid_schedule', message}."""
    clean_title = str(title or "").strip()
    clean_prompt = str(prompt or "").strip()
    if not clean_title or not clean_prompt:
        return {
            "status": "missing_fields",
            "task_id": None,
            "message": "Both title and prompt are required.",
        }

    try:
        model_enum = _coerce_model(model)
        schedule_enum = _coerce_schedule(schedule_type)
        parsed_run_at = _parse_run_at(run_at)
        _validate_schedule(schedule_enum, parsed_run_at, interval_seconds, cron_expr)
        next_run = compute_next_run_at(
            schedule_enum,
            run_at=parsed_run_at,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
        )
    except ValueError as e:
        return {"status": "invalid_schedule", "task_id": None, "message": str(e)}

    task = _create_task_row(
        title=clean_title,
        prompt=clean_prompt,
        model=model_enum,
        schedule_type=schedule_enum,
        run_at=parsed_run_at,
        interval_seconds=interval_seconds,
        cron_expr=cron_expr,
        next_run_at=next_run,
    )
    logger.info("Created task #%s (%s, %s)", task.id, task.model.value, task.schedule_type.value)
    return {
        "status": "created",
        "task_id": task.id,
        "next_run_at": next_run.isoformat(timespec="seconds") if next_run else None,
        "message": "Task scheduled.",
    }


def update_task(
    task_id: int,
    title: Any = UNSET,
    prompt: Any = UNSET,
    model: Any = UNSET,
    schedule_type: Any = UNSET,
    run_at: Any = UNSET,
    interval_seconds: Any = UNSET,
    cron_expr: Any = UNSET,
    status: Any = UNSET,
) -> dict:
    """Edit an existing task; reschedules when the schedule changes.
    Returns {status: 'updated'|'not_found'|'invalid_schedule'}."""
    existing = _get_task_row(task_id)
    if existing is None:
        return {"status": "not_found", "task_id": task_id, "message": f"No task with id {task_id}."}

    try:
        model_enum = _coerce_model(model) if model is not UNSET else existing.model
        schedule_enum = (
            _coerce_schedule(schedule_type) if schedule_type is not UNSET else existing.schedule_type
        )

        # Effective values: the new value if provided, else the current one.
        eff_run_at = _parse_run_at(run_at) if run_at is not UNSET else existing.run_at
        eff_interval = (
            interval_seconds if interval_seconds is not UNSET else existing.interval_seconds
        )
        eff_cron = cron_expr if cron_expr is not UNSET else existing.cron_expr

        schedule_changed = any(
            field is not UNSET
            for field in (schedule_type, run_at, interval_seconds, cron_expr)
        )
        if schedule_changed:
            _validate_schedule(schedule_enum, eff_run_at, eff_interval, eff_cron)
            next_run = compute_next_run_at(
                schedule_enum,
                run_at=eff_run_at,
                interval_seconds=eff_interval,
                cron_expr=eff_cron,
            )
        else:
            next_run = UNSET

        status_enum = UNSET
        if status is not UNSET:
            try:
                status_enum = TaskStatus(str(status).lower().strip())
            except ValueError:
                return {
                    "status": "invalid_status",
                    "task_id": task_id,
                    "message": f"Unknown status '{status}'.",
                }

        # When switching schedule types, fields that no longer apply are cleared.
        changing_type = schedule_type is not UNSET
        clear_run_at = changing_type and schedule_enum != TaskScheduleType.ONE_SHOT
        clear_interval = changing_type and schedule_enum != TaskScheduleType.INTERVAL
        clear_cron = changing_type and schedule_enum != TaskScheduleType.CRON
    except ValueError as e:
        return {"status": "invalid_schedule", "task_id": task_id, "message": str(e)}

    _update_task_row(
        task_id,
        title=title if title is not UNSET else UNSET,
        prompt=prompt if prompt is not UNSET else UNSET,
        model=model_enum if model is not UNSET else UNSET,
        schedule_type=schedule_enum if schedule_type is not UNSET else UNSET,
        run_at=(
            eff_run_at
            if run_at is not UNSET
            else (None if clear_run_at else UNSET)
        ),
        interval_seconds=(
            eff_interval
            if interval_seconds is not UNSET
            else (None if clear_interval else UNSET)
        ),
        cron_expr=(
            eff_cron
            if cron_expr is not UNSET
            else (None if clear_cron else UNSET)
        ),
        next_run_at=next_run,
        status=status_enum,
    )

    refreshed = _get_task_row(task_id)
    return {
        "status": "updated",
        "task_id": task_id,
        "next_run_at": (
            refreshed.next_run_at.isoformat(timespec="seconds")
            if refreshed and refreshed.next_run_at
            else None
        ),
        "message": "Task updated.",
    }


def delete_task(task_id: int) -> dict:
    """Cancel (hard-delete) a task. Returns {status: 'deleted'|'not_found'}."""
    if _delete_task_row(task_id):
        logger.info("Deleted task #%s", task_id)
        return {"status": "deleted", "task_id": task_id, "message": "Task deleted."}
    return {"status": "not_found", "task_id": task_id, "message": f"No task with id {task_id}."}


def list_tasks(status=None, model=None, limit: int = 50) -> dict:
    """List tasks. Returns {status, tasks: [...]}."""
    status_enum = None
    if status is not None:
        try:
            status_enum = TaskStatus(str(status).lower().strip())
        except ValueError:
            status_enum = None  # unknown status -> no filter
    model_enum = None
    if model is not None:
        try:
            model_enum = _coerce_model(model)
        except ValueError:
            model_enum = None

    tasks = _list_task_rows(status=status_enum, model=model_enum, limit=limit)
    return {
        "status": "list_complete",
        "count": len(tasks),
        "tasks": [_task_dict(t) for t in tasks],
    }
