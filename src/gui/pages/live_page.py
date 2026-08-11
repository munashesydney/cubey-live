"""
Gemini Live page — embedded in the DeveloperWindow shell.

Holds the live session controls (mic meter, mute, start/stop), the physical
event interruption panel, and the transcript / logs / conversations tabs.
"""

import datetime
import logging
import customtkinter as ctk
from typing import Callable

from src.config import AppConfig
from src.db import (
    ConversationStatus,
    get_conversation,
    list_conversations,
    list_messages,
)

logger = logging.getLogger(__name__)


class LivePage(ctk.CTkFrame):
    """Gemini Live session control page."""

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
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app_config = config
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption
        self.on_toggle_mute = on_toggle_mute
        self.is_session_active = is_session_active

        self._create_layout()

    def _create_layout(self) -> None:
        """Header controls and main live-console grid."""
        # Top Header Bar
        self.header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        self.header_frame.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="✨ Gemini Live",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC"
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text=f"Model: {self.app_config.model} | Voice: {self.app_config.voice_name}",
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
            ("😮 SURPRISED / SHOCKED", "[SURPRISE GIFT GIVEN]", "#74C7EC", "#89DCEB"),
            ("🤨 TRICK QUESTION", "[HUMAN ASKS TRICK QUESTION]", "#F2CDCD", "#F5E0DC"),
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

        # Tab 3: Saved Conversations
        self.tab_convos = self.right_panel.add("🗂️ Conversations")
        self._build_conversations_tab()

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

    # ------------------------------------------------------------------
    # live updates (called from the controller / async side)
    # ------------------------------------------------------------------

    def set_status(self, status: str) -> None:
        """Thread-safe update to connection status."""
        try:
            if self.winfo_exists():
                self.status_label.configure(text=f"Status: {status}")
                if status.startswith("Connected") or status.startswith("Live"):
                    self.is_session_active = True
                    self.after(200, self.refresh_conversations)
                elif (
                    status.startswith("Disconnected")
                    or status.startswith("Idle")
                    or status.startswith("Error")
                ):
                    self.is_session_active = False
                    self.after(200, self.refresh_conversations)
                self._update_session_button_state()
        except Exception:
            pass

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter."""
        try:
            if self.winfo_exists() and hasattr(self, 'mic_meter') and self.mic_meter.winfo_exists():
                self.mic_meter.set(level)
        except Exception:
            pass

    def append_transcript(self, role: str, text: str) -> None:
        """Thread-safe append to transcript text box."""
        try:
            if self.winfo_exists() and hasattr(self, 'transcript_box') and self.transcript_box.winfo_exists():
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                formatted = f"[{timestamp}] {role}: {text}\n"
                self.transcript_box.insert("end", formatted)
                self.transcript_box.see("end")
        except Exception:
            pass

    def append_log(self, message: str) -> None:
        """Thread-safe append to system log box."""
        try:
            if self.winfo_exists() and hasattr(self, 'log_box') and self.log_box.winfo_exists():
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                self.log_box.insert("end", f"[{timestamp}] {message}\n")
                self.log_box.see("end")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Conversations tab
    # ------------------------------------------------------------------

    def _build_conversations_tab(self) -> None:
        """Build the saved-conversations list + message detail view."""
        toolbar = ctk.CTkFrame(self.tab_convos, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(8, 2))

        self.convo_count_label = ctk.CTkLabel(
            toolbar,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.convo_count_label.pack(side="left")

        ctk.CTkButton(
            toolbar,
            text="🔄 Refresh",
            width=90,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            command=self.refresh_conversations
        ).pack(side="right")

        body = ctk.CTkFrame(self.tab_convos, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        self.convo_list_frame = ctk.CTkScrollableFrame(
            body, fg_color="#1E1E2E", corner_radius=8
        )
        self.convo_list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.convo_detail_box = ctk.CTkTextbox(
            body,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
            fg_color="#1E1E2E",
            text_color="#CDD6F4"
        )
        self.convo_detail_box.grid(row=0, column=1, sticky="nsew")

        self.refresh_conversations()

    def refresh_conversations(self) -> None:
        """Reload the conversation list from the database."""
        try:
            # Gemini Live conversations only — local LLM chats have
            # metadata_json.type == 'local_llm' and live elsewhere.
            conversations = [
                conv
                for conv in list_conversations(limit=100)
                if (conv.metadata_json or {}).get("type") != "local_llm"
            ]
        except Exception as e:
            logger.warning("Failed to load conversations: %s", e)
            return

        for child in self.convo_list_frame.winfo_children():
            child.destroy()

        self.convo_count_label.configure(text=f"{len(conversations)} conversation(s)")

        for conv in conversations:
            btn = ctk.CTkButton(
                self.convo_list_frame,
                text=self._format_conversation(conv),
                anchor="w",
                height=38,
                corner_radius=6,
                fg_color="#313244",
                hover_color="#45475A",
                text_color="#CDD6F4",
                font=ctk.CTkFont(size=11),
                command=lambda cid=conv.id: self._show_conversation(cid)
            )
            btn.pack(fill="x", padx=4, pady=2)

    @staticmethod
    def _format_conversation(conv) -> str:
        """One-line summary used for each row in the conversation list."""
        title = (conv.title or "(untitled)")[:28]
        started = conv.started_at.strftime("%Y-%m-%d %H:%M") if conv.started_at else "?"
        icon = {
            ConversationStatus.ACTIVE: "🟢",
            ConversationStatus.COMPLETED: "⚪",
            ConversationStatus.ARCHIVED: "🗄️",
        }.get(conv.status, "·")
        return f"{icon} {title}\n{started} · {conv.session_id[:8]}"

    def _show_conversation(self, conversation_id: int) -> None:
        """Render the selected conversation's messages in the detail box."""
        try:
            conv = get_conversation(conversation_id=conversation_id)
            messages = list_messages(conversation_id=conversation_id, limit=500)
        except Exception as e:
            logger.warning("Failed to load conversation #%s: %s", conversation_id, e)
            return

        self.convo_detail_box.delete("1.0", "end")
        if conv is None:
            self.convo_detail_box.insert("end", "(conversation not found)\n")
            return

        title = conv.title or "(untitled)"
        started = conv.started_at.strftime("%Y-%m-%d %H:%M") if conv.started_at else "?"
        ended = conv.ended_at.strftime("%Y-%m-%d %H:%M") if conv.ended_at else "still active"
        self.convo_detail_box.insert(
            "end",
            f"#{conv.id} · {title} · {conv.status.value}\n"
            f"{started} → {ended} · {conv.session_id}\n"
            + "=" * 60 + "\n",
        )
        for msg in messages:
            self.convo_detail_box.insert(
                "end", f"[{msg.role.value}] {msg.content}\n\n"
            )
        self.convo_detail_box.see("1.0")

    # ------------------------------------------------------------------
    # session controls
    # ------------------------------------------------------------------

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
