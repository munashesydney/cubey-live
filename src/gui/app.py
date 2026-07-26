"""
CustomTkinter GUI Shell for Gemini Live Robot.
Features Top Navigation Bar switching between Cubeo Robot Face and Developers Dashboard.
"""

import asyncio
import logging
import customtkinter as ctk
from typing import Callable, Optional

from src.config import AppConfig
from src.gui.components import HeaderToolbar, StatusBar
from src.gui.screens import RobotFaceScreen, DeveloperScreen

logger = logging.getLogger(__name__)

# Configure CustomTkinter default styling
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class GeminiLiveApp(ctk.CTk):
    """Main CustomTkinter GUI application with view navigation and component layout."""

    def __init__(
        self,
        config: AppConfig,
        async_loop: asyncio.AbstractEventLoop,
        on_start_session: Callable[[], None],
        on_stop_session: Callable[[], None],
        on_send_interruption: Callable[[str], None],
    ):
        super().__init__()
        self.config = config
        self.async_loop = async_loop
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption

        # Window settings
        self.title("🤖 Cubeo - Gemini Live Robot Simulator")
        self.geometry("1100 x 740")
        self.minsize(950, 620)

        # Internal state
        self.is_session_active = False
        self.current_screen_name = "ROBOT_FACE"

        # Build UI layout with modular components
        self._create_header_component()
        self._create_navigation_bar()
        self._create_screen_container()
        self._create_status_bar_component()

        # Show default main screen (Robot Face)
        self.show_screen("ROBOT_FACE")

    def _create_header_component(self) -> None:
        """Create modular top header toolbar component."""
        self.header_toolbar = HeaderToolbar(
            self,
            on_toggle_session=self._toggle_session,
            on_toggle_mute=self._toggle_mute
        )
        self.header_toolbar.pack(fill="x", padx=15, pady=(12, 5))

    def _create_navigation_bar(self) -> None:
        """Create tab navigation bar to switch between Robot Face and Developers Dashboard."""
        self.nav_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.nav_frame.pack(fill="x", padx=15, pady=(4, 6))

        # Tab buttons
        self.btn_robot_face = ctk.CTkButton(
            self.nav_frame,
            text="🤖 Robot Face",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            width=140,
            height=32,
            command=lambda: self.show_screen("ROBOT_FACE")
        )
        self.btn_robot_face.pack(side="left", padx=(0, 6))

        self.btn_developers = ctk.CTkButton(
            self.nav_frame,
            text="🛠️ Developers",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#313244",
            hover_color="#45475A",
            text_color="#CDD6F4",
            width=140,
            height=32,
            command=lambda: self.show_screen("DEVELOPERS")
        )
        self.btn_developers.pack(side="left")

    def _create_screen_container(self) -> None:
        """Create dynamic container holding screen frames."""
        self.screen_container = ctk.CTkFrame(self, fg_color="transparent")
        self.screen_container.pack(fill="both", expand=True, padx=15, pady=(0, 6))

        # Instantiate screens
        self.robot_face_screen = RobotFaceScreen(self.screen_container)
        self.developer_screen = DeveloperScreen(
            self.screen_container,
            config=self.config,
            on_send_interruption=self.on_send_interruption
        )

    def _create_status_bar_component(self) -> None:
        """Create modular bottom status bar component."""
        self.status_bar = StatusBar(
            self,
            model_name=self.config.model,
            voice_name=self.config.voice_name
        )
        self.status_bar.pack(fill="x", side="bottom")

    def show_screen(self, screen_name: str) -> None:
        """Switch visible active screen (ROBOT_FACE or DEVELOPERS)."""
        self.current_screen_name = screen_name

        # Hide both screens first
        self.robot_face_screen.pack_forget()
        self.developer_screen.pack_forget()

        if screen_name == "ROBOT_FACE":
            self.robot_face_screen.pack(fill="both", expand=True)
            self.btn_robot_face.configure(fg_color="#89B4FA", text_color="#11111B")
            self.btn_developers.configure(fg_color="#313244", text_color="#CDD6F4")
        else:
            self.developer_screen.pack(fill="both", expand=True)
            self.btn_developers.configure(fg_color="#89B4FA", text_color="#11111B")
            self.btn_robot_face.configure(fg_color="#313244", text_color="#CDD6F4")

    def set_status(self, status: str) -> None:
        """Thread-safe update to status bar and session button."""
        self.status_bar.set_status(status)
        if "Connected" in status or "Live" in status:
            self.is_session_active = True
            self.header_toolbar.set_session_state(True)
        elif "Disconnected" in status or "Idle" in status:
            self.is_session_active = False
            self.header_toolbar.set_session_state(False)

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter."""
        self.header_toolbar.update_mic_level(level)

    def append_transcript(self, role: str, text: str) -> None:
        """Forward transcript to developer screen."""
        self.developer_screen.append_transcript(role, text)

    def append_log(self, message: str) -> None:
        """Forward log message to developer screen."""
        self.developer_screen.append_log(message)

    def trigger_robot_reaction(self, event_payload: str) -> None:
        """Trigger visual reaction animation on Cubeo face."""
        self.robot_face_screen.trigger_reaction(event_payload)

    def _toggle_session(self) -> None:
        """Handler for Start/Stop session button."""
        if not self.is_session_active:
            self.on_start_session()
        else:
            self.on_stop_session()

    def _toggle_mute(self, is_muted: bool) -> None:
        """Handler for mute switch."""
        if hasattr(self, 'client') and self.client and self.client.recorder:
            self.client.recorder.set_muted(is_muted)
        self.append_log(f"Microphone Mute: {'ON' if is_muted else 'OFF'}")
