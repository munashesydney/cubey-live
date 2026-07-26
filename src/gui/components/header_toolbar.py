"""
Header Toolbar component frame.
Contains application title, microphone level bar, mute switch, and session control button.
Easily toggleable or removable for production physical robot deployment.
"""

import customtkinter as ctk
from typing import Callable

class HeaderToolbar(ctk.CTkFrame):
    """Header toolbar component."""

    def __init__(
        self,
        master,
        on_toggle_session: Callable[[], None],
        on_toggle_mute: Callable[[bool], None],
        **kwargs
    ):
        super().__init__(master, corner_radius=10, fg_color="#1E1E2E", **kwargs)
        self.on_toggle_session = on_toggle_session
        self.on_toggle_mute = on_toggle_mute

        self._create_widgets()

    def _create_widgets(self) -> None:
        """Create header toolbar layout."""
        # Title & Subtitle
        title_box = ctk.CTkFrame(self, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        self.title_label = ctk.CTkLabel(
            title_box,
            text="🤖 Cubeo Robot Simulator",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC"
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = ctk.CTkLabel(
            title_box,
            text="Gemini Live Real-time Audio + Physical Event Interruption Engine",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.subtitle_label.pack(anchor="w")

        # Controls Box (Right aligned)
        controls_box = ctk.CTkFrame(self, fg_color="transparent")
        controls_box.pack(side="right", padx=15, pady=8)

        # Mic Level Bar
        mic_box = ctk.CTkFrame(controls_box, fg_color="transparent")
        mic_box.pack(side="left", padx=(0, 12))
        
        ctk.CTkLabel(mic_box, text="Mic Level:", font=ctk.CTkFont(size=11), text_color="#BAC2DE").pack(anchor="w")
        self.mic_meter = ctk.CTkProgressBar(mic_box, width=90, height=10, progress_color="#A6E3A1")
        self.mic_meter.set(0.0)
        self.mic_meter.pack(pady=2)

        # Mute Toggle Switch
        self.mute_switch = ctk.CTkSwitch(
            controls_box,
            text="Mute Mic",
            command=self._handle_mute_toggle,
            font=ctk.CTkFont(size=12)
        )
        self.mute_switch.pack(side="left", padx=8)

        # Start / Stop Session Button
        self.session_button = ctk.CTkButton(
            controls_box,
            text="▶ Start Live Session",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            width=150,
            height=34,
            command=self.on_toggle_session
        )
        self.session_button.pack(side="left", padx=5)

    def update_mic_level(self, level: float) -> None:
        """Update mic volume meter level."""
        self.mic_meter.set(level)

    def set_session_state(self, is_active: bool) -> None:
        """Update Start/Stop button visual state."""
        if is_active:
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

    def _handle_mute_toggle(self) -> None:
        """Handle mute switch state change."""
        is_muted = bool(self.mute_switch.get())
        self.on_toggle_mute(is_muted)
