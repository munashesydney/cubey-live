"""
Developer Control Dashboard Screen module.
Contains physical event interruption controls, custom event injectors, transcripts, and system logs.
"""

import datetime
import logging
import customtkinter as ctk
from typing import Callable, Optional

from src.config import AppConfig

logger = logging.getLogger(__name__)

class DeveloperScreen(ctk.CTkFrame):
    """Developer Control Dashboard Screen."""

    def __init__(
        self,
        master,
        config: AppConfig,
        on_send_interruption: Callable[[str], None],
        **kwargs
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config = config
        self.on_send_interruption = on_send_interruption

        self._create_layout()

    def _create_layout(self) -> None:
        """Build grid with Left Interruption Control Panel and Right Log Panel."""
        self.columnconfigure(0, weight=4)
        self.columnconfigure(1, weight=6)
        self.rowconfigure(0, weight=1)

        # LEFT PANEL: Event Injection & Interruption Controls
        self.left_panel = ctk.CTkFrame(self, corner_radius=10, fg_color="#181825")
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

        # RIGHT PANEL: Live Interaction Transcript & Logs Tabview
        self.right_panel = ctk.CTkTabview(self, corner_radius=10, fg_color="#181825")
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
