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
    FaceRecognitionPage,
    HomePage,
    LidarPage,
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
        ("lidar", "📡 LiDAR"),
        ("live", "✨ Gemini Live"),
        ("faces", "👤 Face Recognition"),
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
        face_service: Optional[Any] = None,
        on_toggle_camera: Optional[Callable[[Optional[bool]], bool]] = None,
        on_set_camera_device: Optional[Callable[[int], None]] = None,
        on_send_snapshot: Optional[Callable[[Optional[str]], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
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
        self.face_service = face_service
        self.on_toggle_camera = on_toggle_camera
        self.on_set_camera_device = on_set_camera_device
        self.on_send_snapshot = on_send_snapshot
        self.on_close = on_close

        # Heterogeneous page registry — navigation is duck-typed and lazy.
        self._pages: dict[str, Any] = {}
        self._page_factories: dict[str, Callable[[], Any]] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_page: Optional[str] = None
        self._is_closing = False

        # Window properties
        self.title("🛠️ Cubeo Developer Console")
        self.geometry("1120x700")
        self.minsize(900, 560)

        # Bind Escape and WM_DELETE_WINDOW to close Developer Console
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.after(100, self.lift)

        self._create_layout()
        self._setup_page_factories()
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

    def _setup_page_factories(self) -> None:
        """Define on-demand page constructors to avoid multi-second upfront freezes."""
        self._page_factories["home"] = lambda: HomePage(
            self.page_container, config=self.config, on_navigate=self.show_page
        )
        self._page_factories["wheels"] = lambda: WheelsPage(self.page_container)
        self._page_factories["lidar"] = lambda: LidarPage(self.page_container)
        self._page_factories["live"] = lambda: LivePage(
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
        self._page_factories["faces"] = lambda: FaceRecognitionPage(
            self.page_container,
            face_service=self.face_service,
        )
        self._page_factories["local"] = lambda: LocalChatPage(
            self.page_container,
            app_config=self.config,
            embedding_service=self.embedding_service,
        )
        self._page_factories["memories"] = lambda: MemoryPage(self.page_container)
        self._page_factories["tasks"] = lambda: TasksPage(self.page_container)

    def _get_or_create_page(self, name: str) -> Optional[Any]:
        """Lazy-instantiate page on first access."""
        if name in self._pages:
            return self._pages[name]
        if name in self._page_factories:
            try:
                page = self._page_factories[name]()
                self._pages[name] = page
                return page
            except Exception as e:
                logger.error("Failed to construct dev console page '%s': %s", name, e, exc_info=True)
                return None
        return None

    @property
    def pages(self) -> dict:
        """Dictionary of currently instantiated pages."""
        return self._pages

    def show_page(self, name: str) -> None:
        """Swap the visible page with lifecycle notification and update sidebar highlight."""
        if name not in self._page_factories and name not in self._pages:
            logger.warning("Unknown dev console page '%s'", name)
            return

        # Deactivate outgoing page
        if self._current_page and self._current_page in self._pages:
            old_page = self._pages[self._current_page]
            if hasattr(old_page, "on_deactivate"):
                try:
                    old_page.on_deactivate()
                except Exception as e:
                    logger.debug("Error in on_deactivate for '%s': %s", self._current_page, e)
            old_page.pack_forget()

        self._current_page = name
        page = self._get_or_create_page(name)
        if page is None:
            return

        page.pack(fill="both", expand=True)

        # Activate incoming page
        if hasattr(page, "on_activate"):
            try:
                page.on_activate()
            except Exception as e:
                logger.debug("Error in on_activate for '%s': %s", name, e)

        for key, btn in self._nav_buttons.items():
            if key == name:
                btn.configure(fg_color="#313244", text_color="#F5E0DC")
            else:
                btn.configure(fg_color="transparent", text_color="#CDD6F4")

        # Refresh dynamic pages on each visit.
        if name == "home" and hasattr(page, "refresh_stats"):
            page.refresh_stats()
        elif name == "memories" and hasattr(page, "refresh_memories"):
            page.refresh_memories()
        elif name == "tasks" and hasattr(page, "refresh_tasks"):
            page.refresh_tasks()

    # ------------------------------------------------------------------
    # Delegation from the main app (called from the async / controller side)
    # ------------------------------------------------------------------

    def set_vision_state(self, is_active: bool) -> None:
        """Forward camera vision state to Gemini Live page if instantiated."""
        try:
            if self.winfo_exists() and "live" in self._pages:
                self._pages["live"].set_vision_state(is_active)
        except Exception:
            pass

    def set_status(self, status: str) -> None:
        """Forward live-session status to the pages that show it."""
        try:
            if self.winfo_exists():
                if "live" in self._pages:
                    self._pages["live"].set_status(status)
                if "home" in self._pages:
                    self._pages["home"].update_status(status)
        except Exception:
            pass

    def update_mic_level(self, level: float) -> None:
        """Forward mic level to Live page ONLY if live page is instantiated and active."""
        try:
            if self.winfo_exists() and self._current_page == "live" and "live" in self._pages:
                self._pages["live"].update_mic_level(level)
        except Exception:
            pass

    def append_transcript(self, role: str, text: str) -> None:
        """Forward transcript line to the Gemini Live page if instantiated."""
        try:
            if self.winfo_exists() and "live" in self._pages:
                self._pages["live"].append_transcript(role, text)
        except Exception:
            pass

    def append_log(self, message: str) -> None:
        """Forward log line to the Gemini Live page if instantiated."""
        try:
            if self.winfo_exists() and "live" in self._pages:
                self._pages["live"].append_log(message)
        except Exception:
            pass

    def append_logs(self, messages: list[str]) -> None:
        """Forward a log batch to the Gemini Live page if instantiated."""
        try:
            if self.winfo_exists() and "live" in self._pages:
                self._pages["live"].append_logs(messages)
        except Exception:
            pass

    def destroy(self) -> None:
        """Tear down child pages, invoke close callback, and destroy window."""
        if self._is_closing:
            return
        self._is_closing = True

        # Deactivate and destroy all instantiated pages
        for name, page in list(self._pages.items()):
            try:
                if hasattr(page, "on_deactivate"):
                    page.on_deactivate()
                if hasattr(page, "destroy"):
                    page.destroy()
            except Exception as e:
                logger.debug("Error destroying page '%s': %s", name, e)
        self._pages.clear()

        if self.on_close:
            try:
                self.on_close()
            except Exception as e:
                logger.debug("Error in on_close callback: %s", e)

        super().destroy()

