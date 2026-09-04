"""
Memory Bank page — embedded in the DeveloperWindow shell.

Visualizes Cubey's durable long-term memories stored via the 'memories' tool.
"""

import logging
from typing import Optional

import customtkinter as ctk

from src.db import MemoryStatus, list_memories

logger = logging.getLogger(__name__)

# Segmented-button label -> status filter (None = all).
_STATUS_FILTERS: dict[str, Optional[MemoryStatus]] = {
    "Active": MemoryStatus.ACTIVE,
    "Archived": MemoryStatus.ARCHIVED,
    "All": None,
}

_CATEGORY_COLORS = {
    "fact": "#89B4FA",
    "preference": "#A6E3A1",
    "relationship": "#F38BA8",
    "event": "#FAB387",
    "task": "#F9E2AF",
}


class MemoryPage(ctk.CTkFrame):
    """Dedicated Memory Bank Visualization Page."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._create_layout()
        self.refresh_memories()

    def _create_layout(self) -> None:
        """Header (filter + refresh) and the scrollable memory card list."""
        # Header
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        header.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="🧠 Memory Bank",
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

        # Right controls
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
            command=self.refresh_memories
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
            command=lambda _value: self.refresh_memories()
        )
        self.filter_segment.pack(side="right")
        self.filter_segment.set("Active")

        # Scrollable memory cards
        self.cards_frame = ctk.CTkScrollableFrame(
            self, fg_color="#181825", corner_radius=10
        )
        self.cards_frame.pack(fill="both", expand=True, padx=15, pady=(5, 15))

    def on_activate(self) -> None:
        """Reload memories when tab becomes active."""
        self.refresh_memories()

    def refresh_memories(self) -> None:
        """Reload memories from the database and render cards."""
        status = _STATUS_FILTERS.get(self.filter_segment.get(), MemoryStatus.ACTIVE)
        try:
            memories = list_memories(status=status, limit=60)
        except Exception as e:
            logger.warning("Failed to load memories: %s", e)
            return

        for widget in self.cards_frame.winfo_children():
            widget.destroy()

        label = "memory" if len(memories) == 1 else "memories"
        self.count_label.configure(text=f"{len(memories)} {label}")

        if not memories:
            ctk.CTkLabel(
                self.cards_frame,
                text="No memories here yet.\n\nThe AI stores durable facts here via the 'memories' tool.",
                font=ctk.CTkFont(size=13),
                text_color="#6C7086"
            ).pack(pady=40)
            return

        for memory in memories:
            self._add_memory_card(memory)

    def _add_memory_card(self, memory) -> None:
        """Render one memory as a card with meta row + content."""
        card = ctk.CTkFrame(self.cards_frame, fg_color="#313244", corner_radius=8)
        card.pack(fill="x", padx=6, pady=4)
        card.grid_columnconfigure(0, weight=1)

        # Meta row: importance, category badge, status
        meta_parts = [f"⭐ {memory.importance}"]
        category = memory.category or "general"
        color = _CATEGORY_COLORS.get(category, "#6C7086")
        meta_parts.append(f"● {category}")
        if memory.status == MemoryStatus.ARCHIVED:
            meta_parts.append("● ARCHIVED")

        ctk.CTkLabel(
            card,
            text="  ".join(meta_parts),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=color
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))

        date = memory.created_at.strftime("%Y-%m-%d %H:%M") if memory.created_at else ""
        ctk.CTkLabel(
            card,
            text=date,
            font=ctk.CTkFont(size=11),
            text_color="#6C7086"
        ).grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))

        ctk.CTkLabel(
            card,
            text=memory.content,
            font=ctk.CTkFont(size=13),
            text_color="#CDD6F4",
            justify="left",
            wraplength=660
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 8))
