"""
CustomTkinter GUI Shell for Gemini Live Robot.
Renders a 100% full-bleed OLED Cubeo Robot Face with strict 110.4 : 63.1 aspect ratio locking.
Opens dedicated Developer Control Window via corner button, right-click, or 'D' hotkey.
"""

import asyncio
import logging
import customtkinter as ctk
from typing import Any, Callable, Optional

from src.config import AppConfig
from src.gui.event_bridge import GuiEventBridge
from src.gui.pages.robot_face_page import RobotFacePage
from src.gui.pages.startup_page import StartupPage
from src.gui.windows.developer_window import DeveloperWindow
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_GUI_EVENT_POLL_MS = 16
_GUI_EVENT_BATCH_SIZE = 256

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
        embedding_service: EmbeddingService,
        camera_service: Optional[Any] = None,
        on_toggle_camera: Optional[Callable[[Optional[bool]], bool]] = None,
        on_set_camera_device: Optional[Callable[[int], None]] = None,
        on_send_snapshot: Optional[Callable[[Optional[str]], None]] = None,
        show_startup_screen: bool = True,
    ):
        super().__init__()
        self.config = config
        self.async_loop = async_loop
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption
        self.embedding_service = embedding_service
        self.camera_service = camera_service
        self.on_toggle_camera = on_toggle_camera
        self.on_set_camera_device = on_set_camera_device
        self.on_send_snapshot = on_send_snapshot

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
        self.is_vision_active = False
        self.dev_window: Optional[DeveloperWindow] = None
        self._is_fullscreen = False
        self._event_bridge = GuiEventBridge()

        # Build 100% full-bleed Robot Face display
        self.robot_face = RobotFacePage(
            self,
            on_open_developer_console=self.toggle_developer_console,
            target_fps=self.config.gui_face_fps,
            supersampling=self.config.gui_face_supersampling,
        )

        # Build Startup Screen if enabled; otherwise show Robot Face immediately
        if show_startup_screen:
            self.startup_page: Optional[StartupPage] = StartupPage(
                self,
                on_startup_complete=self.finish_startup,
            )
            self.startup_page.pack(fill="both", expand=True)
        else:
            self.startup_page = None
            self.robot_face.pack(fill="both", expand=True)

        # Bind hotkeys & mouse triggers for Developer Console
        self.bind("<Key-d>", lambda e: self.toggle_developer_console())
        self.bind("<Key-D>", lambda e: self.toggle_developer_console())
        self.bind("<Button-3>", lambda e: self.toggle_developer_console())  # Right-click

        # Bind Fullscreen Hotkeys (F11 to toggle, Escape to exit)
        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self.exit_fullscreen())

        # Bind Charging / Sleep Animation Test Hotkey ('C')
        self.bind("<Key-c>", lambda e: self.toggle_charging_test())
        self.bind("<Key-C>", lambda e: self.toggle_charging_test())

        # Auto-enter fullscreen if configured
        if getattr(self.config, "gui_fullscreen", False):
            self.after(150, self.enter_fullscreen)

        # This is scheduled by the Tk thread itself. Background producers only
        # touch GuiEventBridge's lock-protected Python containers.
        self.after(_GUI_EVENT_POLL_MS, self._drain_gui_events)

    # ------------------------------------------------------------------
    # Thread-safe ingress. These methods never call Tk.
    # ------------------------------------------------------------------

    def post_status(self, status: str) -> None:
        self._event_bridge.post("status", status, latest=True)

    def post_mic_level(self, level: float) -> None:
        self._event_bridge.post("mic_level", level, latest=True)

    def post_transcript(self, role: str, text: str) -> None:
        self._event_bridge.post("transcript", role, text)

    def post_log(self, message: str) -> None:
        self._event_bridge.post("log", message)

    def post_reaction(self, reaction_type: str) -> None:
        self._event_bridge.post("reaction", reaction_type)

    def post_listening(self, is_listening: bool) -> None:
        self._event_bridge.post("listening", is_listening, latest=True)

    def post_battery(self, is_charging: bool, battery_pct: int) -> None:
        self._event_bridge.post("battery", is_charging, battery_pct, latest=True)

    def post_vision_state(self, is_vision_active: bool) -> None:
        self._event_bridge.post("vision", is_vision_active, latest=True)

    def post_startup_progress(
        self, progress: float, status_text: str, active_step_index: Optional[int] = None
    ) -> None:
        self._event_bridge.post(
            "startup_progress", progress, status_text, active_step_index, latest=True
        )

    def post_startup_complete(self) -> None:
        self._event_bridge.post("startup_complete", latest=True)

    def _drain_gui_events(self) -> None:
        """Apply queued updates from the Tk main thread in bounded batches."""
        handlers = {
            "status": self.set_status,
            "mic_level": self.update_mic_level,
            "transcript": self.append_transcript,
            "log": self.append_log,
            "reaction": self.trigger_robot_reaction,
            "listening": self.set_listening_state,
            "battery": self.update_battery_state,
            "vision": self.set_vision_state,
            "startup_progress": self.update_startup_progress,
            "startup_complete": self.finish_startup,
        }
        log_messages: list[str] = []
        for event in self._event_bridge.drain(_GUI_EVENT_BATCH_SIZE):
            if event.kind == "log":
                log_messages.append(str(event.payload[0]))
                continue
            handler = handlers.get(event.kind)
            if handler is None:
                continue
            try:
                handler(*event.payload)
            except Exception:
                logger.exception("Failed to apply GUI event '%s'", event.kind)
        if log_messages:
            self.append_logs(log_messages)
        self.after(_GUI_EVENT_POLL_MS, self._drain_gui_events)

    def update_startup_progress(
        self, progress: float, status_text: str, active_step_index: Optional[int] = None
    ) -> None:
        """Update startup loading screen progress."""
        if self.startup_page is not None and self.startup_page.winfo_exists():
            self.startup_page.set_progress(progress, status_text, active_step_index)

    def finish_startup(self) -> None:
        """Transition from StartupPage to RobotFacePage."""
        if self.startup_page is not None:
            try:
                self.startup_page.pack_forget()
                self.startup_page.destroy()
            except Exception as e:
                logger.debug("Error tearing down StartupPage: %s", e)
            self.startup_page = None

        if hasattr(self, "robot_face") and not self.robot_face.winfo_ismapped():
            self.robot_face.pack(fill="both", expand=True)

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
                is_session_active=self.is_session_active,
                embedding_service=self.embedding_service,
                camera_service=self.camera_service,
                on_toggle_camera=self.on_toggle_camera,
                on_set_camera_device=self.on_set_camera_device,
                on_send_snapshot=self.on_send_snapshot,
            )
        else:
            self.dev_window.lift()
            self.dev_window.focus()

    def set_vision_state(self, is_vision_active: bool) -> None:
        """Thread-safe update to camera vision state across windows and face HUD."""
        self.is_vision_active = is_vision_active
        self.robot_face.set_vision_active(is_vision_active)
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.set_vision_state(is_vision_active)

    def set_status(self, status: str) -> None:
        """Thread-safe update to status."""
        if status.startswith("Connected") or status.startswith("Live"):
            self.is_session_active = True
        elif status.startswith("Disconnected") or status.startswith("Idle"):
            self.is_session_active = False

        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.set_status(status)

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter and dynamic face wave visualizer."""
        self.robot_face.set_mic_level(level)
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.update_mic_level(level)

    def set_listening_state(self, is_listening: bool) -> None:
        """Thread-safe update to face listening side waves."""
        self.robot_face.set_listening(is_listening)

    def append_transcript(self, role: str, text: str) -> None:
        """Forward transcript to developer window if open."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.append_transcript(role, text)

    def append_log(self, message: str) -> None:
        """Forward log message to developer window if open."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.append_log(message)

    def append_logs(self, messages: list[str]) -> None:
        """Forward a batch with a single Tk text-widget update."""
        if self.dev_window and self.dev_window.winfo_exists():
            self.dev_window.append_logs(messages)

    def trigger_robot_reaction(self, event_payload: str) -> None:
        """Trigger visual reaction animation on Cubeo face."""
        self.robot_face.trigger_reaction(event_payload)

    def _toggle_mute(self, is_muted: bool) -> None:
        """Handler for mute switch in developer window."""
        self.is_muted = is_muted
        if hasattr(self, 'client') and self.client and self.client.recorder:
            self.client.recorder.set_muted(is_muted)
        self.append_log(f"Microphone Mute: {'ON' if is_muted else 'OFF'}")

    def toggle_fullscreen(self) -> None:
        """Toggle true fullscreen kiosk mode."""
        if self._is_fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self) -> None:
        """Enter true fullscreen kiosk mode, covering OS taskbars and titlebars."""
        self._is_fullscreen = True
        try:
            self.attributes("-fullscreen", True)
            self.append_log("Entered Fullscreen Kiosk Mode (Press F11 or Esc to exit)")
        except Exception as e:
            logger.warning("Error entering fullscreen: %s", e)

    def exit_fullscreen(self) -> None:
        """Exit fullscreen back to windowed mode."""
        self._is_fullscreen = False
        try:
            self.attributes("-fullscreen", False)
            self.append_log("Exited Fullscreen Mode")
        except Exception as e:
            logger.warning("Error exiting fullscreen: %s", e)

    def update_battery_state(self, is_charging: bool, battery_pct: int) -> None:
        """Apply battery and charging state to robot face and dev window."""
        self.robot_face.set_charging(is_charging, battery_pct)

    def toggle_charging_test(self) -> None:
        """Toggle charging & sleeping animation for live developer testing ('C' hotkey)."""
        new_state = not self.robot_face.is_charging
        test_pct = 85 if new_state else 100
        self.robot_face.set_charging(new_state, test_pct)
        state_str = "CHARGING & SLEEPING (85%)" if new_state else "NORMAL (AWAKE)"
        self.post_log(f"⚡ [Dev Mode] Face animation set to: {state_str}")
