"""
CustomTkinter GUI Dashboard for Gemini Live Voice & Interruption Simulator.
"""

import asyncio
import datetime
import logging
import tkinter as tk
import customtkinter as ctk
from typing import Callable, Optional

from src.config import AppConfig
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.client.live_client import GeminiLiveClient

logger = logging.getLogger(__name__)

# Configure CustomTkinter default styling
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class GeminiLiveApp(ctk.CTk):
    """Main CustomTkinter GUI application."""

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
        self.title("🤖 Gemini Live - Physical Robot Interruption Simulator")
        self.geometry("1100 x 720")
        self.minsize(950, 600)

        # Internal state
        self.is_session_active = False
        self.client: Optional[GeminiLiveClient] = None

        # Build UI layout
        self._create_header_section()
        self._create_main_content()
        self._create_status_bar()

    def _create_header_section(self) -> None:
        """Create header toolbar with title, connection buttons, and audio status."""
        self.header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        self.header_frame.pack(fill="x", padx=15, pady=(15, 10))

        # Title & Subtitle
        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=10)

        self.title_label = ctk.CTkLabel(
            title_box,
            text="🤖 Gemini Live Robot Simulator",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#F5E0DC"
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = ctk.CTkLabel(
            title_box,
            text="Real-time Voice Streaming + Instant Physical Event Interruption (Barge-in)",
            font=ctk.CTkFont(size=12),
            text_color="#BAC2DE"
        )
        self.subtitle_label.pack(anchor="w")

        # Controls (Right aligned)
        controls_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        controls_box.pack(side="right", padx=15, pady=10)

        # Mic Level Bar
        mic_box = ctk.CTkFrame(controls_box, fg_color="transparent")
        mic_box.pack(side="left", padx=(0, 15))
        
        ctk.CTkLabel(mic_box, text="Mic Level:", font=ctk.CTkFont(size=11), text_color="#BAC2DE").pack(anchor="w")
        self.mic_meter = ctk.CTkProgressBar(mic_box, width=100, height=10, progress_color="#A6E3A1")
        self.mic_meter.set(0.0)
        self.mic_meter.pack(pady=2)

        # Mute Toggle Switch
        self.mute_switch = ctk.CTkSwitch(
            controls_box,
            text="Mute Mic",
            command=self._toggle_mute,
            font=ctk.CTkFont(size=12)
        )
        self.mute_switch.pack(side="left", padx=10)

        # Start / Stop Session Button
        self.session_button = ctk.CTkButton(
            controls_box,
            text="▶ Start Live Session",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            width=160,
            height=36,
            command=self._toggle_session
        )
        self.session_button.pack(side="left", padx=5)

    def _create_main_content(self) -> None:
        """Create main grid with Left Interruption Control Panel and Right Log Panel."""
        self.main_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.main_grid.pack(fill="both", expand=True, padx=15, pady=5)

        # Configure columns (Left: 40% width, Right: 60% width)
        self.main_grid.columnconfigure(0, weight=4)
        self.main_grid.columnconfigure(1, weight=6)
        self.main_grid.rowconfigure(0, weight=1)

        # LEFT PANEL: Event Injection & Interruption Controls
        self.left_panel = ctk.CTkFrame(self.main_grid, corner_radius=10, fg_color="#181825")
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        # Header for Interruption Controls
        left_header = ctk.CTkLabel(
            self.left_panel,
            text="⚡ Physical Event Interruptions",
            font=ctk.CTkFont(size=16, weight="bold"),
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
        left_desc.pack(anchor="w", padx=15, pady=(0, 15))

        # Quick Interruption Preset Buttons Grid
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
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color=color,
                hover_color=hover_color,
                text_color="#11111B",
                height=45,
                command=lambda p=payload: self._trigger_interruption(p)
            )
            btn.grid(row=row, column=col, padx=4, pady=6, sticky="ew")
            self.preset_frame.columnconfigure(col, weight=1)

        # Custom Event Input Box
        custom_box = ctk.CTkFrame(self.left_panel, fg_color="#1E1E2E", corner_radius=8)
        custom_box.pack(fill="x", padx=15, pady=(20, 15))

        ctk.CTkLabel(
            custom_box,
            text="Custom Physical Event Text:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#CDD6F4"
        ).pack(anchor="w", padx=10, pady=(10, 2))

        self.custom_entry = ctk.CTkEntry(
            custom_box,
            placeholder_text="e.g. [HUMAN POKED YOUR SENSOR]",
            font=ctk.CTkFont(size=12),
            height=35
        )
        self.custom_entry.pack(fill="x", padx=10, pady=5)
        self.custom_entry.bind("<Return>", lambda event: self._send_custom_interruption())

        self.send_custom_btn = ctk.CTkButton(
            custom_box,
            text="⚡ Inject Custom Interruption",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            height=35,
            command=self._send_custom_interruption
        )
        self.send_custom_btn.pack(fill="x", padx=10, pady=(5, 10))

        # RIGHT PANEL: Live Transcript & System Logs Tabview
        self.right_panel = ctk.CTkTabview(self.main_grid, corner_radius=10, fg_color="#181825")
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        # Tab 1: Live Transcript
        self.tab_transcript = self.right_panel.add("💬 Live Transcript")
        self.transcript_box = ctk.CTkTextbox(
            self.tab_transcript,
            font=ctk.CTkFont(family="Consolas", size=13),
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

    def _create_status_bar(self) -> None:
        """Bottom status bar displaying WebSocket status and model name."""
        self.status_frame = ctk.CTkFrame(self, height=30, fg_color="#11111B")
        self.status_frame.pack(fill="x", side="bottom")

        self.status_label = ctk.CTkLabel(
            self.status_frame,
            text="Status: Idle (Disconnected)",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.status_label.pack(side="left", padx=15, pady=2)

        model_info = f"Model: {self.config.model} | Voice: {self.config.voice_name}"
        self.info_label = ctk.CTkLabel(
            self.status_frame,
            text=model_info,
            font=ctk.CTkFont(size=11),
            text_color="#6C7086"
        )
        self.info_label.pack(side="right", padx=15, pady=2)

    def set_status(self, status: str) -> None:
        """Thread-safe update to status bar."""
        self.status_label.configure(text=f"Status: {status}")
        if "Connected" in status or "Live" in status:
            self.is_session_active = True
            self.session_button.configure(
                text="⏹ Stop Session",
                fg_color="#F38BA8",
                hover_color="#E78284",
                text_color="#11111B"
            )
        elif "Disconnected" in status or "Idle" in status:
            self.is_session_active = False
            self.session_button.configure(
                text="▶ Start Live Session",
                fg_color="#A6E3A1",
                hover_color="#94E2D5",
                text_color="#11111B"
            )

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

    def _toggle_session(self) -> None:
        """Handler for Start/Stop session button."""
        if not self.is_session_active:
            self.on_start_session()
        else:
            self.on_stop_session()

    def _toggle_mute(self) -> None:
        """Handler for mute switch."""
        is_muted = bool(self.mute_switch.get())
        if self.client and self.client.recorder:
            self.client.recorder.set_muted(is_muted)
        self.append_log(f"Microphone Mute: {'ON' if is_muted else 'OFF'}")

    def _trigger_interruption(self, payload: str) -> None:
        """Handler for preset interruption buttons."""
        self.on_send_interruption(payload)

    def _send_custom_interruption(self) -> None:
        """Handler for custom interruption entry box."""
        text = self.custom_entry.get().strip()
        if text:
            if not text.startswith("["):
                text = f"[{text.upper()}]"
            self.on_send_interruption(text)
            self.custom_entry.delete(0, "end")
