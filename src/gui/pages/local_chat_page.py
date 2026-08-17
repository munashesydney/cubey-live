"""
Local LLM Chat Console page — embedded in the DeveloperWindow shell.

Dedicated page for interactive streaming chat with local Qwen3.5 2B Q4 model
via llama.cpp. Persists chat history in SQLite using existing conversations
and messages tables.
"""

import datetime
import logging
import uuid
import customtkinter as ctk
from typing import Dict, List, Optional

from src.config import AppConfig, config
from src.db import (
    ConversationSource,
    MessageRole,
    create_conversation,
    create_message,
    list_conversations,
    list_messages,
)
from src.services.local_llm import LocalLLMService

logger = logging.getLogger(__name__)


class LocalChatPage(ctk.CTkFrame):
    """Dedicated Local Qwen3.5 2B Chat Page."""

    def __init__(self, master, app_config: AppConfig = config, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app_config = app_config

        # Instantiate local LLM service
        self.llm_service = LocalLLMService(
            repo_id=app_config.local_model_repo_id,
            filename=app_config.local_model_filename,
            n_ctx=app_config.local_model_n_ctx,
            default_system_prompt=app_config.local_model_system_prompt,
        )

        # Active conversation state
        self.active_conversation_id: Optional[int] = None
        self.conversations_map: Dict[str, int] = {}
        self.current_system_prompt: str = app_config.local_model_system_prompt

        # Streaming buffer state
        self._current_assistant_text: str = ""
        self._is_generating: bool = False

        self._create_layout()

        # Check local engine health and load conversations
        self.after(200, self._check_engine_status)
        self.after(300, self.refresh_conversations_list)

    def _create_layout(self) -> None:
        """Create header controls, settings drawer, chat transcript, and message input."""
        # Top Header Bar
        self.header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        self.header_frame.pack(fill="x", padx=15, pady=(12, 5))

        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)

        ctk.CTkLabel(
            title_box,
            text="🦙 Qwen3.5 2B Local Model Console",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#F5E0DC"
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            title_box,
            text=f"Engine: llama.cpp | Model: {self.app_config.local_model_filename}",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE"
        )
        self.status_label.pack(anchor="w")

        # Right Header Controls
        header_controls = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        header_controls.pack(side="right", padx=15, pady=8)

        # Conversation Selector Dropdown
        self.convo_dropdown = ctk.CTkOptionMenu(
            header_controls,
            values=["(No Conversations)"],
            command=self._on_conversation_selected,
            width=210,
            fg_color="#313244",
            button_color="#45475A",
            text_color="#CDD6F4"
        )
        self.convo_dropdown.pack(side="left", padx=5)

        # New Chat Button
        ctk.CTkButton(
            header_controls,
            text="➕ New Chat",
            width=95,
            height=32,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            command=self.start_new_chat
        ).pack(side="left", padx=4)

        # Settings Toggle Button
        self.settings_btn = ctk.CTkButton(
            header_controls,
            text="⚙️ Settings",
            width=90,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color="#313244",
            hover_color="#45475A",
            text_color="#BAC2DE",
            command=self._toggle_settings_panel
        )
        self.settings_btn.pack(side="left", padx=4)

        # Collapsible Settings Panel
        self.settings_frame = ctk.CTkFrame(self, corner_radius=8, fg_color="#181825")
        self.settings_visible = False

        sys_lbl = ctk.CTkLabel(
            self.settings_frame,
            text="System Prompt:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CDD6F4"
        )
        sys_lbl.pack(anchor="w", padx=12, pady=(8, 2))

        self.sys_prompt_entry = ctk.CTkEntry(
            self.settings_frame,
            font=ctk.CTkFont(size=11),
            fg_color="#1E1E2E",
            text_color="#CDD6F4"
        )
        self.sys_prompt_entry.insert(0, self.current_system_prompt)
        self.sys_prompt_entry.pack(fill="x", padx=12, pady=(0, 8))

        # Main Chat Transcript Box
        self.transcript_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            wrap="word",
            fg_color="#181825",
            text_color="#CDD6F4",
            corner_radius=10
        )
        self.transcript_box.pack(fill="both", expand=True, padx=15, pady=5)

        # Input & Control Footer
        self.footer_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        self.footer_frame.pack(fill="x", padx=15, pady=(5, 12))

        self.input_entry = ctk.CTkEntry(
            self.footer_frame,
            placeholder_text="Type a message to Qwen3.5 2B... (Press Enter to send)",
            font=ctk.CTkFont(size=13),
            height=42,
            fg_color="#181825",
            text_color="#CDD6F4"
        )
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(12, 8), pady=10)
        self.input_entry.bind("<Return>", lambda e: self.send_message())

        self.send_btn = ctk.CTkButton(
            self.footer_frame,
            text="▶ Send",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            width=90,
            height=40,
            command=self.send_message
        )
        self.send_btn.pack(side="left", padx=4, pady=10)

        self.stop_btn = ctk.CTkButton(
            self.footer_frame,
            text="⏹ Stop",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#F38BA8",
            hover_color="#E78284",
            text_color="#11111B",
            width=80,
            height=40,
            state="disabled",
            command=self.stop_generation
        )
        self.stop_btn.pack(side="left", padx=(4, 12), pady=10)

    def _toggle_settings_panel(self) -> None:
        """Toggle visibility of system prompt settings panel."""
        if self.settings_visible:
            self.settings_frame.pack_forget()
            self.settings_visible = False
            self.settings_btn.configure(fg_color="#313244")
        else:
            self.settings_frame.pack(fill="x", padx=15, pady=(0, 5), after=self.header_frame)
            self.settings_visible = True
            self.settings_btn.configure(fg_color="#45475A")

    def _check_engine_status(self) -> None:
        """Check connection to local Ollama / llama.cpp service."""
        is_loaded = self.llm_service.check_health()
        if is_loaded:
            self.status_label.configure(
                text=f"🟢 Loaded | Engine: llama.cpp | Model: {self.app_config.local_model_filename}",
                text_color="#A6E3A1"
            )
        else:
            self.status_label.configure(
                text=f"⏳ Idle (Model will auto-load on first message) | Model: {self.app_config.local_model_filename}",
                text_color="#BAC2DE"
            )

    def refresh_conversations_list(self) -> None:
        """Fetch saved conversations from DB and populate dropdown selector."""
        try:
            convos = list_conversations(source=ConversationSource.LOCAL, limit=50)
        except Exception as e:
            logger.warning("Failed to load conversations: %s", e)
            return

        self.conversations_map.clear()
        options: List[str] = []

        for conv in convos:
            title = conv.title or f"Conversation #{conv.id}"
            time_str = conv.started_at.strftime("%b %d %H:%M") if conv.started_at else ""
            label = f"🦙 {title[:30]} ({time_str})"

            self.conversations_map[label] = conv.id
            options.append(label)

        if not options:
            options = ["(No Saved Conversations)"]

        self.convo_dropdown.configure(values=options)
        if options and options[0] != "(No Saved Conversations)" and self.active_conversation_id is None:
            self.convo_dropdown.set(options[0])
            self._show_conversation(self.conversations_map[options[0]])

    def _on_conversation_selected(self, selected_label: str) -> None:
        """Handler for conversation dropdown selection."""
        cid = self.conversations_map.get(selected_label)
        if cid is not None:
            self._show_conversation(cid)

    def _show_conversation(self, conversation_id: int) -> None:
        """Render selected conversation messages into transcript box."""
        self.active_conversation_id = conversation_id
        try:
            messages = list_messages(conversation_id=conversation_id, limit=500)
        except Exception as e:
            logger.warning("Failed to load messages for conversation #%s: %s", conversation_id, e)
            return

        self.transcript_box.delete("1.0", "end")
        self.transcript_box.insert("end", f"=== Conversation #{conversation_id} ===\n\n")

        for msg in messages:
            role_display = "👤 User" if msg.role == MessageRole.USER else "🦙 Qwen3.5"
            timestamp = msg.created_at.strftime("%H:%M:%S") if msg.created_at else ""
            self.transcript_box.insert("end", f"[{timestamp}] {role_display}:\n{msg.content}\n\n")

        self.transcript_box.see("end")

    def start_new_chat(self) -> None:
        """Reset active conversation and clear transcript box."""
        self.active_conversation_id = None
        self.transcript_box.delete("1.0", "end")
        self.transcript_box.insert("end", "✨ Started new Qwen3.5 local chat session. Type your query below!\n\n")
        self.convo_dropdown.set("(New Conversation)")

    def send_message(self) -> None:
        """Send message from entry box to local Qwen3.5 model."""
        if self._is_generating:
            return

        text = self.input_entry.get().strip()
        if not text:
            return

        self.input_entry.delete(0, "end")
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # 1. Ensure DB conversation exists
        if self.active_conversation_id is None:
            try:
                conv = create_conversation(
                    session_id=uuid.uuid4().hex,
                    title=text[:50],
                    metadata={"type": "local_llm", "model": self.app_config.local_model_filename},
                    source=ConversationSource.LOCAL,
                )
                self.active_conversation_id = conv.id
            except Exception as e:
                logger.error("Failed to create conversation: %s", e)

        # 2. Persist User Message to DB
        if self.active_conversation_id is not None:
            try:
                create_message(self.active_conversation_id, role=MessageRole.USER, content=text)
            except Exception as e:
                logger.warning("Failed to persist user message: %s", e)

        # 3. Update UI transcript with User message
        self.transcript_box.insert("end", f"[{timestamp}] 👤 User:\n{text}\n\n")
        self.transcript_box.insert("end", f"[{timestamp}] 🦙 Qwen3.5:\n")
        self.transcript_box.see("end")

        # 4. Gather history for LLM request payload
        chat_history: List[Dict[str, str]] = []
        if self.active_conversation_id is not None:
            try:
                past_msgs = list_messages(conversation_id=self.active_conversation_id, limit=20)
                for pm in past_msgs:
                    if pm.role == MessageRole.USER:
                        chat_history.append({"role": "user", "content": pm.content})
                    elif pm.role == MessageRole.MODEL:
                        chat_history.append({"role": "assistant", "content": pm.content})
            except Exception as e:
                logger.warning("Failed to build chat history: %s", e)
                chat_history = [{"role": "user", "content": text}]
        else:
            chat_history = [{"role": "user", "content": text}]

        # 5. Lock UI for streaming generation
        self._is_generating = True
        self._current_assistant_text = ""
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        sys_prompt = self.sys_prompt_entry.get().strip() or self.current_system_prompt

        # 6. Call streaming LLM service
        self.llm_service.stream_chat_completion(
            messages=chat_history,
            system_prompt=sys_prompt,
            on_token=self._on_token_received_threadsafe,
            on_complete=self._on_complete_threadsafe,
            on_error=self._on_error_threadsafe,
        )

    def stop_generation(self) -> None:
        """Halt streaming generation."""
        if self._is_generating:
            self.llm_service.stop_generation()

    def _on_token_received_threadsafe(self, token: str) -> None:
        """Callback from background thread for received token."""
        try:
            if self.winfo_exists():
                self.after(0, self._append_token, token)
        except Exception:
            pass

    def _append_token(self, token: str) -> None:
        """Append streaming token to transcript text box on main Tkinter thread."""
        self._current_assistant_text += token
        self.transcript_box.insert("end", token)
        self.transcript_box.see("end")

    def _on_complete_threadsafe(self, full_text: str) -> None:
        """Callback from background thread when response generation completes."""
        try:
            if self.winfo_exists():
                self.after(0, self._finalize_response, full_text)
        except Exception:
            pass

    def _finalize_response(self, full_text: str) -> None:
        """Finalize assistant response, persist to DB, and restore UI controls."""
        self.transcript_box.insert("end", "\n\n")
        self.transcript_box.see("end")

        # Persist Assistant Message to DB
        if self.active_conversation_id is not None and full_text.strip():
            try:
                create_message(self.active_conversation_id, role=MessageRole.MODEL, content=full_text)
            except Exception as e:
                logger.warning("Failed to persist model response: %s", e)

        self._is_generating = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.refresh_conversations_list()

    def _on_error_threadsafe(self, err_msg: str) -> None:
        """Callback from background thread when an error occurs."""
        try:
            if self.winfo_exists():
                self.after(0, self._handle_error, err_msg)
        except Exception:
            pass

    def _handle_error(self, err_msg: str) -> None:
        """Render error message in transcript and unlock UI controls."""
        self.transcript_box.insert("end", f"\n❌ [Error]: {err_msg}\n\n")
        self.transcript_box.see("end")
        self._is_generating = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
