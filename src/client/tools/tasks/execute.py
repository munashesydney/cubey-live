"""
Execution logic for the 'tasks' tool call.
Adds, lists, updates, and deletes scheduled AI tasks.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def execute_tasks_tool(
    action: Optional[str] = None,
    title: Optional[str] = None,
    prompt: Optional[str] = None,
    model: Optional[str] = None,
    schedule_type: Optional[str] = None,
    run_at: Optional[str] = None,
    interval_seconds: Optional[int] = None,
    cron_expr: Optional[str] = None,
    task_id: Optional[int] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """
    Executes the 'tasks' tool call received from Gemini Live API.

    Dispatches on `action` (add/list/update/delete) to the task service and
    returns a plain dict suitable for send_tool_response. Never raises.
    """
    clean_action = str(action or "").lower().strip()

    try:
        clean_task_id = int(task_id) if task_id is not None else None
    except (TypeError, ValueError):
        clean_task_id = None

    try:
        clean_interval = int(interval_seconds) if interval_seconds is not None else None
    except (TypeError, ValueError):
        clean_interval = None

    logger.info(
        "⏰ AI Tool Call Executed: tasks(action='%s', task_id=%s)",
        clean_action, clean_task_id,
    )

    try:
        from src.services.task_service import (
            create_task,
            delete_task,
            list_tasks,
            update_task,
        )

        if clean_action == "add":
            return create_task(
                title=title or "",
                prompt=prompt or "",
                model=model,
                schedule_type=schedule_type,
                run_at=run_at,
                interval_seconds=clean_interval,
                cron_expr=cron_expr,
            )

        if clean_action == "list":
            return list_tasks(status=status)

        if clean_action == "update":
            if clean_task_id is None:
                return {
                    "status": "missing_task_id",
                    "task_id": None,
                    "message": "Updating a task requires its task_id.",
                }
            return update_task(
                task_id=clean_task_id,
                title=title,
                prompt=prompt,
                model=model,
                schedule_type=schedule_type,
                run_at=run_at,
                interval_seconds=clean_interval,
                cron_expr=cron_expr,
                status=status,
            )

        if clean_action == "delete":
            if clean_task_id is None:
                return {
                    "status": "missing_task_id",
                    "task_id": None,
                    "message": "Deleting a task requires its task_id.",
                }
            return delete_task(clean_task_id)

        return {
            "status": "invalid_action",
            "message": (
                f"Unknown action '{action}'. Valid actions: add, list, update, delete."
            ),
        }

    except Exception as e:
        logger.exception("tasks tool failed: %s", e)
        return {
            "status": "task_error",
            "message": f"Task operation failed: {e}",
        }
