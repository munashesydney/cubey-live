"""
Task Manager page — embedded in the DeveloperWindow shell.

View and manage scheduled AI tasks created via the 'tasks' tool.
"""

import logging
from datetime import datetime
from typing import Any, Optional

import customtkinter as ctk

from src.db import TaskStatus, update_task as repo_update_task
from src.services.task_scheduler import utcnow
from src.services.task_service import (
    delete_task as service_delete_task,
    list_tasks as service_list_tasks,
    update_task as service_update_task,
)

logger = logging.getLogger(__name__)

# Segmented-button label -> status filter (None = all).
_STATUS_FILTERS: dict[str, Optional[str]] = {
    "All": None,
    "Active": "active",
    "Paused": "paused",
    "Done": "done",
}

_MODEL_BADGES = {"local": "🦙 local", "gemini": "✨ gemini"}
_STATUS_COLORS = {
    "active": "#A6E3A1",
    "paused": "#F9E2AF",
    "done": "#6C7086",
}


def _format_when(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    try:
        if value.tzinfo is not None:
            value = value.astimezone()
        return value.strftime("%Y-%m-%d %H:%M")
    except AttributeError:
        return str(value)


class TasksPage(ctk.CTkFrame):
    """Dedicated Task Manager Page."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._refreshing = False

        self._create_layout()
        self.refresh_tasks()
        # Keep the list live so scheduled runs / results show up on their own.
        self._schedule_auto_refresh()

    def _schedule_auto_refresh(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.refresh_tasks()
        except Exception:
            pass
        self.after(5000, self._schedule_auto_refresh)

    def _create_layout(self) -> None:
        """Header (filter + refresh) and the scrollable task list."""
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        header.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="⏰ Task Manager",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC"
        ).pack(anchor="w")

        self.count_label = ctk.CTkLabel(
            title_box,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.count_label.pack(anchor="w")

        controls_box = ctk.CTkFrame(header, fg_color="transparent")
        controls_box.pack(side="right", padx=15, pady=8)

        ctk.CTkButton(
            controls_box,
            text="🔄 Refresh",
            width=90,
            height=32,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            command=self.refresh_tasks
        ).pack(side="right", padx=(6, 0))

        self.filter_segment = ctk.CTkSegmentedButton(
            controls_box,
            values=list(_STATUS_FILTERS.keys()),
            font=ctk.CTkFont(size=11),
            fg_color="#313244",
            selected_color="#45475A",
            selected_hover_color="#585B70",
            unselected_color="#313244",
            unselected_hover_color="#45475A",
            text_color="#CDD6F4",
            command=lambda _value: self.refresh_tasks()
        )
        self.filter_segment.pack(side="right")
        self.filter_segment.set("All")

        self.tasks_frame = ctk.CTkScrollableFrame(
            self, fg_color="#181825", corner_radius=10
        )
        self.tasks_frame.pack(fill="both", expand=True, padx=15, pady=(5, 15))

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def refresh_tasks(self) -> None:
        """Reload tasks from the database and render cards."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            status = _STATUS_FILTERS.get(self.filter_segment.get(), None)
            result = service_list_tasks(status=status, limit=100)
            tasks = result.get("tasks", [])
        except Exception as e:
            logger.warning("Failed to load tasks: %s", e)
            return
        finally:
            self._refreshing = False

        for widget in self.tasks_frame.winfo_children():
            widget.destroy()

        label = "task" if len(tasks) == 1 else "tasks"
        self.count_label.configure(text=f"{len(tasks)} {label}")

        if not tasks:
            ctk.CTkLabel(
                self.tasks_frame,
                text="No tasks yet.\n\nThe AIs can schedule tasks via the 'tasks' tool.",
                font=ctk.CTkFont(size=13),
                text_color="#6C7086"
            ).pack(pady=40)
            return

        for task in tasks:
            self._add_task_card(task)

    def _add_task_card(self, task: dict[str, Any]) -> None:
        card = ctk.CTkFrame(self.tasks_frame, fg_color="#313244", corner_radius=8)
        card.pack(fill="x", padx=6, pady=4)
        card.grid_columnconfigure(0, weight=1)

        status_color = _STATUS_COLORS.get(str(task.get("status") or "active"), "#6C7086")

        # Row 0: title + model badge + status
        title = (str(task.get("title")) or f"Task #{task.get('task_id')}")[:44]
        ctk.CTkLabel(
            card,
            text=f"{title}  ·  {_MODEL_BADGES.get(str(task.get('model') or ''), str(task.get('model') or ''))}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#CDD6F4"
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))

        ctk.CTkLabel(
            card,
            text=f"#{(task.get('task_id') or '')}  ·  {task.get('status', '')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=status_color
        ).grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))

        # Row 1: schedule summary + next run
        schedule = self._schedule_summary(task)
        ctk.CTkLabel(
            card,
            text=f"{schedule}  |  next: {_format_when(task.get('next_run_at'))}",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 0))

        # Row 2: last run info
        last_status = task.get("last_status") or "never ran"
        last_result = (task.get("last_result") or "").strip().replace("\n", " ")[:90]
        last_line = f"last: {last_status}"
        if last_result:
            last_line += f" — {last_result}"
        ctk.CTkLabel(
            card,
            text=last_line,
            font=ctk.CTkFont(size=11),
            text_color="#6C7086",
            wraplength=600,
            justify="left"
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 0))

        # Row 3: actions
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 8))

        task_id = int(task["task_id"])
        status = str(task.get("status") or "active")

        ctk.CTkButton(
            actions,
            text="▶ Run Now",
            width=84,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            command=lambda tid=task_id: self._run_now(tid)
        ).pack(side="left", padx=(0, 5))

        toggle_text = "▶ Resume" if status == "paused" else "⏸ Pause"
        toggle_color = "#F9E2AF" if status == "paused" else "#FAB387"
        ctk.CTkButton(
            actions,
            text=toggle_text,
            width=84,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=toggle_color,
            hover_color="#E5C890",
            text_color="#11111B",
            command=lambda tid=task_id: self._toggle_pause(tid, status)
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            actions,
            text="🗑 Delete",
            width=84,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#F38BA8",
            hover_color="#E78284",
            text_color="#11111B",
            command=lambda tid=task_id: self._delete_task(tid)
        ).pack(side="left", padx=5)

    @staticmethod
    def _schedule_summary(task: dict) -> str:
        schedule_type = task.get("schedule_type")
        if schedule_type == "one_shot":
            return f"one_shot @ {_format_when(task.get('run_at'))}"
        if schedule_type == "interval":
            return f"every {task.get('interval_seconds')}s"
        if schedule_type == "cron":
            return f"cron '{task.get('cron_expr')}'"
        return schedule_type or "?"

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def _run_now(self, task_id: int) -> None:
        """Fire the task on the next scheduler tick."""
        try:
            repo_update_task(
                task_id,
                status=TaskStatus.ACTIVE,
                next_run_at=utcnow(),
            )
        except Exception as e:
            logger.warning("Run-now failed for task #%s: %s", task_id, e)
        self.refresh_tasks()

    def _toggle_pause(self, task_id: int, current_status: Optional[str]) -> None:
        new_status = "active" if current_status == "paused" else "paused"
        try:
            service_update_task(task_id, status=new_status)
        except Exception as e:
            logger.warning("Pause/resume failed for task #%s: %s", task_id, e)
        self.refresh_tasks()

    def _delete_task(self, task_id: int) -> None:
        try:
            service_delete_task(task_id)
        except Exception as e:
            logger.warning("Delete failed for task #%s: %s", task_id, e)
        self.refresh_tasks()
