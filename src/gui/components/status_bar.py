"""
Status Bar component frame.
Displays WebSocket connection status, active model name, and voice settings.
"""

import customtkinter as ctk

class StatusBar(ctk.CTkFrame):
    """Bottom status bar component."""

    def __init__(self, master, model_name: str, voice_name: str, **kwargs):
        super().__init__(master, height=28, fg_color="#11111B", **kwargs)
        self.model_name = model_name
        self.voice_name = voice_name

        self._create_widgets()

    def _create_widgets(self) -> None:
        """Create status bar labels."""
        self.status_label = ctk.CTkLabel(
            self,
            text="Status: Idle (Disconnected)",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.status_label.pack(side="left", padx=15, pady=2)

        model_info = f"Model: {self.model_name} | Voice: {self.voice_name}"
        self.info_label = ctk.CTkLabel(
            self,
            text=model_info,
            font=ctk.CTkFont(size=11),
            text_color="#6C7086"
        )
        self.info_label.pack(side="right", padx=15, pady=2)

    def set_status(self, status: str) -> None:
        """Update connection status label."""
        self.status_label.configure(text=f"Status: {status}")
