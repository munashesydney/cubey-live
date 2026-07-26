"""
CustomTkinter GUI Shell for Gemini Live Robot.
Renders a 100% full-bleed OLED Cubeo Robot Face with strict 110.4 : 63.1 aspect ratio locking.
Opens dedicated Developer Control Window via corner button, right-click, or 'D' hotkey.
"""

import asyncio
import logging
import customtkinter as ctk
from typing import Callable, Optional

from src.config import AppConfig
from src.gui.screens.robot_face_screen import RobotFaceScreen
from src.gui.developer_window import DeveloperWindow

logger = logging.getLogger(__name__)

# Configure CustomTkinter default styling
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class GeminiLiveApp(ctk.CTk):
    """Main CustomTkinter GUI application enforcing 110.4:63.1 aspect ratio."""

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

        # Window title & initial resolution (110.4 : 63.1 ratio)
        self.title("🤖 Cubeo Robot Face")
        self.geometry("1104x631")
        self.minsize(552, 316)

        # Enforce native OS aspect ratio lock (1104:631) from all corners
        try:
            self.aspect(1104, 631, 1104, 631)
        except Exception as e:
            logger.warning("Native aspect ratio lock warning: %s", e)

        # Internal state
        self.is_session_active = False
        self.is_muted = False
        self.dev_window: Optional[DeveloperWindow] = None

        # Build 100% full-bleed Robot Face display
        self.robot_face = RobotFaceScreen(
            self,
            on_open_developer_console=self.toggle_developer_console
        )
        self.robot_face.pack(fill="both", expand=True)

        # Bind hotkeys & mouse triggers for Developer Console
        self.bind("<Key-d>", lambda e: self.toggle_developer_console())
        self.bind("<Key-D>", lambda e: self.toggle_developer_console())
        self.bind("<Button-3>", lambda e: self.toggle_developer_console())  # Right-click

    def toggle_developer_console(self) -> None:
        """Open or focus the Developer Control Window."""
        if self.dev_window is None or not self.dev_window.winfo_exists():
            self.dev_window = DeveloperWindow(
                master=self,
                config=self.config,
                on_start_session=self.on_start_session,
                on_stop_session=self.on_stop_session,
                on_send_interruption=self.on_send_interruption,
                on_toggle_mute=self._toggle_mute,
                is_session_active=self.is_session_active
            )
        else:
            self.dev_window.lift()
            self.dev_window.focus()

    def set_status(self, status: str) -> None:
        """Thread-safe update to status."""
        if "Connected" in status or "Live" in status:
            self.is_session_active = True
        elif "Disconnected" in status or "Idle" in status:
            self.is_session_active = False

        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.set_status(status)

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.update_mic_level(level)

    def append_transcript(self, role: str, text: str) -> None:
        """Forward transcript to developer window if open."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.append_transcript(role, text)

    def append_log(self, message: str) -> None:
        """Forward log message to developer window if open."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.append_log(message)

    def trigger_robot_reaction(self, event_payload: str) -> None:
        """Trigger visual reaction animation on Cubeo face."""
        self.robot_face.trigger_reaction(event_payload)

    def _toggle_mute(self, is_muted: bool) -> None:
        """Handler for mute switch in developer window."""
        self.is_muted = is_muted
        if hasattr(self, 'client') and self.client and self.client.recorder:
            self.client.recorder.set_muted(is_muted)
        self.append_log(f"Microphone Mute: {'ON' if is_muted else 'OFF'}")
