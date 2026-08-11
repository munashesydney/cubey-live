"""
Dev Home page — landing page of the dev console.

Lists every available page as a clickable card and shows a quick status
snapshot (live session status, active task / memory counts).
"""

import logging
import customtkinter as ctk
from typing import Callable

from src.config import AppConfig
from src.db import MemoryStatus, list_memories
from src.services.task_service import list_tasks

logger = logging.getLogger(__name__)

# (page key, card title, description, fg color, hover color)
_PAGE_CARDS = [
    ("live", "✨ Gemini Live", "Session controls, events, transcripts & logs", "#A6E3A1", "#94E2D5"),
    ("local", "🦙 Local Chat", "Chat with the on-device Qwen model", "#CBA6F7", "#B4BEFE"),
    ("memories", "🧠 Memories", "Cubey's durable long-term memory bank", "#74C7EC", "#89DCEB"),
    ("tasks", "⏰ Tasks", "Scheduled AI tasks and run history", "#F9E2AF", "#E5C890"),
]


class HomePage(ctk.CTkFrame):
    """Landing page: page cards + status snapshot."""

    def __init__(self, master, config: AppConfig, on_navigate: Callable[[str], None], **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app_config = config
        self.on_navigate = on_navigate
        self._create_layout()
        self.refresh_stats()

    def _create_layout(self) -> None:
        """Header, page card grid, and stats footer."""
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        header.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="🛠️ Dev Home",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#F5E0DC"
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Cubey's control center — pick a page to get started",
            font=ctk.CTkFont(size=12),
            text_color="#BAC2DE"
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            title_box,
            text="Status: Idle (Disconnected)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#F9E2AF"
        )
        self.status_label.pack(anchor="w", pady=(4, 0))

        # Page cards grid
        self.card_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.card_grid.pack(fill="both", expand=True, padx=15, pady=(10, 5))
        for i in range(2):
            self.card_grid.columnconfigure(i, weight=1)
            self.card_grid.rowconfigure(i, weight=1)

        for idx, (key, title, desc, color, hover) in enumerate(_PAGE_CARDS):
            btn = ctk.CTkButton(
                self.card_grid,
                text=f"{title}\n{desc}",
                font=ctk.CTkFont(size=15, weight="bold"),
                fg_color=color,
                hover_color=hover,
                text_color="#11111B",
                corner_radius=12,
                command=lambda k=key: self.on_navigate(k)
            )
            btn.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=6, pady=6)

        # Stats footer
        self.stats_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#6C7086",
            anchor="w"
        )
        self.stats_label.pack(fill="x", padx=21, pady=(0, 12))

    # ------------------------------------------------------------------
    # live updates from the shell
    # ------------------------------------------------------------------

    def update_status(self, status: str) -> None:
        """Show the current live session status."""
        try:
            if self.winfo_exists():
                color = "#A6E3A1" if ("Connected" in status or "Live" in status) else "#BAC2DE"
                self.status_label.configure(text=f"Status: {status}", text_color=color)
        except Exception:
            pass

    def refresh_stats(self) -> None:
        """Refresh the footer counts (active tasks + active memories)."""
        try:
            result = list_tasks(status="active", limit=1000)
            task_count = result.get("count", len(result.get("tasks", [])))
        except Exception as e:
            logger.warning("Failed to load task count: %s", e)
            task_count = 0
        try:
            memory_count = len(list_memories(status=MemoryStatus.ACTIVE, limit=1000))
        except Exception as e:
            logger.warning("Failed to load memory count: %s", e)
            memory_count = 0
        try:
            if self.winfo_exists():
                self.stats_label.configure(
                    text=f"⚡ {task_count} active task(s)   ·   🧠 {memory_count} active memor{'y' if memory_count == 1 else 'ies'}"
                )
        except Exception:
            pass
