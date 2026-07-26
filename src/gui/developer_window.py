"""
Developer Control Window module.
Dedicated pop-up window (CTkToplevel) containing live session controls, mic meters, physical event interruption grid, transcripts, and logs.
"""

import datetime
import logging
import customtkinter as ctk
from typing import Callable, Optional

from src.config import AppConfig

logger = logging.getLogger(__name__)

class DeveloperWindow(ctk.CTkToplevel):
    """Developer Control Dashboard Window."""

    def __init__(
        self,
        master,
        config: AppConfig,
        on_start_session: Callable[[], None],
        on_stop_session: Callable[[], None],
        on_send_interruption: Callable[[str], None],
        on_toggle_mute: Callable[[bool], None],
        is_session_active: bool = False,
        **kwargs
    ):
        super().__init__(master, **kwargs)
        self.config = config
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption
        self.on_toggle_mute = on_toggle_mute
        self.is_session_active = is_session_active

        # Window properties
        self.title("🛠️ Cubeo Developer Console")
        self.geometry("960x640")
        self.minsize(850, 520)

        # Focus window on launch
        self.after(100, self.lift)

        self._create_layout()

    def _create_layout(self) -> None:
        """Create header controls and main developer grid."""
        # Top Header Bar
        self.header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        self.header_frame.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="🛠️ Developer Control Console",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC"
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text=f"Model: {self.config.model} | Voice: {self.config.voice_name}",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        ).pack(anchor="w")

        # Right Header Controls
        controls_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        controls_box.pack(side="right", padx=15, pady=8)

        # Mic Level Meter
        mic_box = ctk.CTkFrame(controls_box, fg_color="transparent")
        mic_box.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(mic_box, text="Mic Level:", font=ctk.CTkFont(size=11), text_color="#BAC2DE").pack(anchor="w")
        self.mic_meter = ctk.CTkProgressBar(mic_box, width=90, height=10, progress_color="#A6E3A1")
        self.mic_meter.set(0.0)
        self.mic_meter.pack(pady=2)

        # Mute Switch
        self.mute_switch = ctk.CTkSwitch(
            controls_box,
            text="Mute Mic",
            command=self._handle_mute_toggle,
            font=ctk.CTkFont(size=12)
        )
        self.mute_switch.pack(side="left", padx=8)

        # Session Start/Stop Button
        self.session_button = ctk.CTkButton(
            controls_box,
            text="▶ Start Live Session",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            width=150,
            height=34,
            command=self._toggle_session
        )
        self.session_button.pack(side="left", padx=5)
        self._update_session_button_state()

        # Main Split Grid
        self.main_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.main_grid.pack(fill="both", expand=True, padx=15, pady=5)

        self.main_grid.columnconfigure(0, weight=4)
        self.main_grid.columnconfigure(1, weight=6)
        self.main_grid.rowconfigure(0, weight=1)

        # Left Panel: Interruption Controls
        self.left_panel = ctk.CTkFrame(self.main_grid, corner_radius=10, fg_color="#181825")
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        left_header = ctk.CTkLabel(
            self.left_panel,
            text="⚡ Physical Event Interruptions",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#FAB387"
        )
        left_header.pack(anchor="w", padx=15, pady=(15, 5))

        left_desc = ctk.CTkLabel(
            self.left_panel,
            text="Clicking an event button immediately cuts off AI voice output\nand forces Gemini to react instantly to the new state.",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
            justify="left"
        )
        left_desc.pack(anchor="w", padx=15, pady=(0, 12))

        # Event Preset Buttons Grid
        self.preset_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.preset_frame.pack(fill="x", padx=15, pady=5)

        events = [
            ("🥾 HUMAN KICKED YOU", "[HUMAN KICKED YOU]", "#F38BA8", "#E78284"),
            ("🏃 MOVED OUT OF FRAME", "[HUMAN MOVED OUT OF FRAME]", "#FAB387", "#E5C890"),
            ("🛑 OBSTACLE DETECTED", "[OBSTACLE IN PATH]", "#F9E2AF", "#E5C890"),
            ("🔋 CRITICAL BATTERY 5%", "[CRITICAL BATTERY 5%]", "#CBA6F7", "#CA9EE6"),
            ("💦 WATER SPILLED", "[WATER SPILLED ON SENSORS]", "#89B4FA", "#85C1DC"),
            ("✋ HUMAN WAVING", "[HUMAN WAVING HAND]", "#A6E3A1", "#81C8BE"),
        ]

        for idx, (label, payload, color, hover_color) in enumerate(events):
            row = idx // 2
            col = idx % 2
            btn = ctk.CTkButton(
                self.preset_frame,
                text=label,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=color,
                hover_color=hover_color,
                text_color="#11111B",
                height=42,
                command=lambda p=payload: self.on_send_interruption(p)
            )
            btn.grid(row=row, column=col, padx=4, pady=5, sticky="ew")
            self.preset_frame.columnconfigure(col, weight=1)

        # Custom Event Input Box
        custom_box = ctk.CTkFrame(self.left_panel, fg_color="#1E1E2E", corner_radius=8)
        custom_box.pack(fill="x", padx=15, pady=(15, 12))

        ctk.CTkLabel(
            custom_box,
            text="Custom Physical Event Text:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CDD6F4"
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self.custom_entry = ctk.CTkEntry(
            custom_box,
            placeholder_text="e.g. [HUMAN POKED YOUR SENSOR]",
            font=ctk.CTkFont(size=12),
            height=34
        )
        self.custom_entry.pack(fill="x", padx=10, pady=4)
        self.custom_entry.bind("<Return>", lambda event: self._send_custom_interruption())

        self.send_custom_btn = ctk.CTkButton(
            custom_box,
            text="⚡ Inject Custom Interruption",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            height=34,
            command=self._send_custom_interruption
        )
        self.send_custom_btn.pack(fill="x", padx=10, pady=(4, 8))

        # Right Panel: Transcript & Logs Tabview
        self.right_panel = ctk.CTkTabview(self.main_grid, corner_radius=10, fg_color="#181825")
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        # Tab 1: Live Transcript
        self.tab_transcript = self.right_panel.add("💬 Live Transcript")
        self.transcript_box = ctk.CTkTextbox(
            self.tab_transcript,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            fg_color="#1E1E2E",
            text_color="#CDD6F4"
        )
        self.transcript_box.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab 2: System Logs
        self.tab_logs = self.right_panel.add("📜 System Logs")
        self.log_box = ctk.CTkTextbox(
            self.tab_logs,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            fg_color="#1E1E2E",
            text_color="#A6ADC8"
        )
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        # Bottom Status Bar
        self.status_bar = ctk.CTkFrame(self, height=26, fg_color="#11111B")
        self.status_bar.pack(fill="x", side="bottom")

        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="Status: Idle (Disconnected)",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.status_label.pack(side="left", padx=15, pady=2)

    def set_status(self, status: str) -> None:
        """Thread-safe update to connection status."""
        self.status_label.configure(text=f"Status: {status}")
        if "Connected" in status or "Live" in status:
            self.is_session_active = True
        elif "Disconnected" in status or "Idle" in status:
            self.is_session_active = False
        self._update_session_button_state()

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter."""
        self.mic_meter.set(level)

    def append_transcript(self, role: str, text: str) -> None:
        """Thread-safe append to transcript text box."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {role}: {text}\n"
        self.transcript_box.insert("end", formatted)
        self.transcript_box.see("end")

    def append_log(self, message: str) -> None:
        """Thread-safe append to system log box."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")

    def _update_session_button_state(self) -> None:
        """Update button appearance based on session state."""
        if self.is_session_active:
            self.session_button.configure(
                text="⏹ Stop Session",
                fg_color="#F38BA8",
                hover_color="#E78284",
                text_color="#11111B"
            )
        else:
            self.session_button.configure(
                text="▶ Start Live Session",
                fg_color="#A6E3A1",
                hover_color="#94E2D5",
                text_color="#11111B"
            )

    def _toggle_session(self) -> None:
        """Handler for Start/Stop session button."""
        if not self.is_session_active:
            self.on_start_session()
        else:
            self.on_stop_session()

    def _handle_mute_toggle(self) -> None:
        """Handler for mute switch."""
        is_muted = bool(self.mute_switch.get())
        self.on_toggle_mute(is_muted)

    def _send_custom_interruption(self) -> None:
        """Handler for custom interruption entry box."""
        text = self.custom_entry.get().strip()
        if text:
            if not text.startswith("["):
                text = f"[{text.upper()}]"
            self.on_send_interruption(text)
            self.custom_entry.delete(0, "end")
