"""
Developer Console shell module.

The dev home window opened from the robot face screen. Provides a sidebar
navigating between embedded pages: Home, Wheels, Gemini Live, Local Chat, Memories,
and Tasks. Everything lives in one navigable window instead of a stack of
popups.
"""

import logging
from typing import Any, Callable, Optional

import customtkinter as ctk

from src.config import AppConfig
from src.gui.pages import (
    HomePage,
    LivePage,
    LocalChatPage,
    MemoryPage,
    TasksPage,
    WheelsPage,
)
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class DeveloperWindow(ctk.CTkToplevel):
    """Dev console shell with sidebar navigation over embedded pages."""

    # (page key, sidebar label) — order is the sidebar order.
    NAV_ITEMS = [
        ("home", "🏠 Home"),
        ("wheels", "🛞 Wheels"),
        ("live", "✨ Gemini Live"),
        ("local", "🦙 Local Chat"),
        ("memories", "🧠 Memories"),
        ("tasks", "⏰ Tasks"),
    ]

    def __init__(
        self,
        master,
        config: AppConfig,
        on_start_session: Callable[[], None],
        on_stop_session: Callable[[], None],
        on_send_interruption: Callable[[str], None],
        on_toggle_mute: Callable[[bool], None],
        embedding_service: EmbeddingService,
        is_session_active: bool = False,
        camera_service: Optional[Any] = None,
        on_toggle_camera: Optional[Callable[[Optional[bool]], bool]] = None,
        on_set_camera_device: Optional[Callable[[int], None]] = None,
        on_send_snapshot: Optional[Callable[[Optional[str]], None]] = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.config = config
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption
        self.on_toggle_mute = on_toggle_mute
        self.embedding_service = embedding_service
        self.is_session_active = is_session_active
        self.camera_service = camera_service
        self.on_toggle_camera = on_toggle_camera
        self.on_set_camera_device = on_set_camera_device
        self.on_send_snapshot = on_send_snapshot

        # Heterogeneous page registry — navigation is duck-typed.
        self._pages: dict[str, Any] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_page: Optional[str] = None

        # Window properties
        self.title("🛠️ Cubeo Developer Console")
        self.geometry("1120x700")
        self.minsize(900, 560)

        # Bind Escape to close Developer Console
        self.bind("<Escape>", lambda e: self.destroy())

        self.after(100, self.lift)

        self._create_layout()
        self._register_pages()
        self.show_page("home")

    def _create_layout(self) -> None:
        """Sidebar nav rail + page content container."""
        # Left sidebar
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color="#11111B")
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="🛠️ Dev Console",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=16, pady=(18, 2))

        ctk.CTkLabel(
            self.sidebar,
            text="Cubeo Controls",
            font=ctk.CTkFont(size=11),
            text_color="#6C7086",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        for key, label in self.NAV_ITEMS:
            btn = ctk.CTkButton(
                self.sidebar,
                text=label,
                anchor="w",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                hover_color="#1E1E2E",
                text_color="#CDD6F4",
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x", padx=10, pady=2)
            self._nav_buttons[key] = btn

        # Page content container
        self.page_container = ctk.CTkFrame(self, fg_color="transparent")
        self.page_container.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=0)

    def _register_pages(self) -> None:
        """Create every page once; they keep their state while hidden."""
        self._pages["home"] = HomePage(
            self.page_container, config=self.config, on_navigate=self.show_page
        )
        self._pages["wheels"] = WheelsPage(self.page_container)
        self._pages["live"] = LivePage(
            self.page_container,
            config=self.config,
            on_start_session=self.on_start_session,
            on_stop_session=self.on_stop_session,
            on_send_interruption=self.on_send_interruption,
            on_toggle_mute=self.on_toggle_mute,
            is_session_active=self.is_session_active,
            camera_service=self.camera_service,
            on_toggle_camera=self.on_toggle_camera,
            on_set_camera_device=self.on_set_camera_device,
            on_send_snapshot=self.on_send_snapshot,
        )
        self._pages["local"] = LocalChatPage(
            self.page_container,
            app_config=self.config,
            embedding_service=self.embedding_service,
        )
        self._pages["memories"] = MemoryPage(self.page_container)
        self._pages["tasks"] = TasksPage(self.page_container)

    def show_page(self, name: str) -> None:
        """Swap the visible page and update the sidebar highlight."""
        if name not in self._pages:
            logger.warning("Unknown dev console page '%s'", name)
            return

        if self._current_page:
            self._pages[self._current_page].pack_forget()
        self._current_page = name

        page = self._pages[name]
        page.pack(fill="both", expand=True)

        for key, btn in self._nav_buttons.items():
            if key == name:
                btn.configure(fg_color="#313244", text_color="#F5E0DC")
            else:
                btn.configure(fg_color="transparent", text_color="#CDD6F4")

        # Refresh dynamic pages on each visit.
        if name == "home":
            page.refresh_stats()
        elif name == "memories":
            page.refresh_memories()
        elif name == "tasks":
            page.refresh_tasks()

    # ------------------------------------------------------------------
    # Delegation from the main app (called from the async / controller side)
    # ------------------------------------------------------------------

    def set_vision_state(self, is_active: bool) -> None:
        """Forward camera vision state to Gemini Live page."""
        try:
            if self.winfo_exists() and "live" in self._pages:
                self._pages["live"].set_vision_state(is_active)
        except Exception:
            pass

    def set_status(self, status: str) -> None:
        """Forward live-session status to the pages that show it."""
        try:
            if self.winfo_exists():
                self._pages["live"].set_status(status)
                self._pages["home"].update_status(status)
        except Exception:
            pass

    def update_mic_level(self, level: float) -> None:
        """Forward mic level to the Gemini Live page."""
        try:
            if self.winfo_exists():
                self._pages["live"].update_mic_level(level)
        except Exception:
            pass

    def append_transcript(self, role: str, text: str) -> None:
        """Forward transcript line to the Gemini Live page."""
        try:
            if self.winfo_exists():
                self._pages["live"].append_transcript(role, text)
        except Exception:
            pass

    def append_log(self, message: str) -> None:
        """Forward log line to the Gemini Live page."""
        try:
            if self.winfo_exists():
                self._pages["live"].append_log(message)
        except Exception:
            pass

    def append_logs(self, messages: list[str]) -> None:
        """Forward a log batch to the Gemini Live page."""
        try:
            if self.winfo_exists():
                self._pages["live"].append_logs(messages)
        except Exception:
            pass
