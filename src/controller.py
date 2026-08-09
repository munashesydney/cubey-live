"""
Application Controller orchestrating asyncio worker threads, audio pipeline, Gemini Live client, and GUI.
"""

import asyncio
import logging
import threading
import uuid
from typing import Optional

from src.config import config
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.client.live_client import GeminiLiveClient
from src.db import (
    MessageRole,
    create_conversation,
    create_message,
    end_conversation,
    update_conversation,
)
from src.gui.app import GeminiLiveApp
from src.stt import LocalTranscriptService

logger = logging.getLogger(__name__)

# Keep titles short enough for the dev-console conversation list.
_TITLE_MAX_CHARS = 60


def _map_message_role(role: str) -> MessageRole:
    """Normalize transcript role names (from the live client or local STT) to a
    persisted MessageRole. Local STT emits lowercase 'user'/'model'; the live
    client emits 'User Event'/'Model'."""
    normalized = role.strip().lower()
    if normalized in ("user",):
        return MessageRole.USER
    if normalized in ("model", "ai", "assistant"):
        return MessageRole.MODEL
    if normalized in ("user event", "event"):
        return MessageRole.EVENT
    return MessageRole.SYSTEM

class ApplicationController:
    """Coordinates asyncio worker thread, live client session with AI tool calls, and CustomTkinter GUI."""

    def __init__(self):
        self.config = config
        self.async_loop: Optional[asyncio.AbstractEventLoop] = None
        self.loop_thread: Optional[threading.Thread] = None

        self.recorder: Optional[AudioRecorder] = None
        self.player: Optional[AudioPlayer] = None
        self.client: Optional[GeminiLiveClient] = None
        self.gui: Optional[GeminiLiveApp] = None

        # On-device STT that transcribes both sides of the conversation into
        # the transcript/history without touching the live audio path.
        self.transcript_service = LocalTranscriptService(
            model_size=config.stt_model_size,
            language=config.stt_language,
            on_result=self._on_transcript_received,
        )

        self._session_task: Optional[asyncio.Task] = None

        # Active DB-backed conversation for the current live session.
        self._active_conversation_id: Optional[int] = None
        self._conversation_titled: bool = False

    def start(self) -> None:
        """Start background asyncio loop thread and launch GUI mainloop."""
        logger.info("Initializing Application Controller...")

        # 1. Start dedicated background thread for asyncio event loop
        self.async_loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._run_async_loop,
            args=(self.async_loop,),
            daemon=True
        )
        self.loop_thread.start()

        # 2. Create audio pipeline components
        self.player = AudioPlayer(
            sample_rate=self.config.output_sample_rate,
            channels=self.config.channels
        )
        
        self.recorder = AudioRecorder(
            sample_rate=self.config.input_sample_rate,
            channels=self.config.channels,
            chunk_size=self.config.chunk_size,
            on_level_change=self._on_mic_level_changed,
            on_audio_chunk=self.transcript_service.feed_user_audio
        )

        # 3. Create Gemini Live Client with Tool Reaction Callback
        self.client = GeminiLiveClient(
            config=self.config,
            recorder=self.recorder,
            player=self.player,
            on_status_change=self._on_status_changed,
            on_transcript=self._on_transcript_received,
            on_log=self._on_log_received,
            on_tool_reaction=self._on_tool_reaction_triggered,
            on_session_ended=self._on_session_ended,
            transcript_service=self.transcript_service
        )

        # 4. Create CustomTkinter GUI app
        self.gui = GeminiLiveApp(
            config=self.config,
            async_loop=self.async_loop,
            on_start_session=self.start_live_session,
            on_stop_session=self.stop_live_session,
            on_send_interruption=self.send_interruption
        )
        self.gui.client = self.client

        logger.info("Starting CustomTkinter GUI Main Loop...")
        self.gui.mainloop()

    def _run_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Worker thread entrypoint for asyncio event loop."""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def start_live_session(self) -> None:
        """Schedule start_session coroutine on background asyncio thread."""
        if self.gui:
            if not self.config.api_key:
                self.gui.append_log("❌ Error: GEMINI_API_KEY is not set! Set it in your environment or .env file.")
                self.gui.set_status("Error: Missing GEMINI_API_KEY")
                return

        if self.client and self.async_loop and self.async_loop.is_running():
            self._begin_conversation()
            self.transcript_service.start()
            logger.info("Scheduling Live session start...")
            self._session_task = asyncio.run_coroutine_threadsafe(
                self.client.start_session(),
                self.async_loop
            )

    def stop_live_session(self) -> None:
        """Stop active live session."""
        if self.client:
            logger.info("Stopping Live session...")
            self.client.stop_session()

    def send_interruption(self, text_payload: str) -> None:
        """
        Schedule instant text interruption on background asyncio thread.
        Note: The GUI face reaction is NOT hardcoded here—Gemini receives the event
        and decides autonomously whether to invoke the 'react' tool.
        """
        if self.client and self.async_loop and self.async_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.client.interrupt_with_text(text_payload),
                self.async_loop
            )

    def _on_tool_reaction_triggered(self, reaction_type: str) -> None:
        """Callback invoked when Gemini Live calls the 'react' tool."""
        if self.gui and self.gui.winfo_exists():
            self.gui.after(0, self.gui.trigger_robot_reaction, reaction_type)

    def _on_mic_level_changed(self, level: float) -> None:
        """Callback from audio recorder (runs thread-safely)."""
        try:
            if self.gui and self.gui.winfo_exists():
                self.gui.after(0, self.gui.update_mic_level, level)
        except Exception:
            pass

    def _on_status_changed(self, status: str) -> None:
        """Callback when Live client connection status changes."""
        if self.gui and self.gui.winfo_exists():
            self.gui.after(0, self.gui.set_status, status)

    def _on_transcript_received(self, role: str, text: str) -> None:
        """Callback when transcript text is received from Gemini or User event.

        Runs on the asyncio worker thread. Persists the line against the active
        conversation (if any), then forwards it to the GUI.
        """
        self._persist_message(role, text)
        if self.gui and self.gui.winfo_exists():
            self.gui.after(0, self.gui.append_transcript, role, text)

    def _on_log_received(self, message: str) -> None:
        """Callback for log messages."""
        if self.gui and self.gui.winfo_exists():
            self.gui.after(0, self.gui.append_log, message)

    # ------------------------------------------------------------------
    # Database-backed conversation lifecycle
    # ------------------------------------------------------------------

    def _begin_conversation(self) -> None:
        """Create a new ACTIVE conversation row for the upcoming live session."""
        try:
            conversation = create_conversation(session_id=uuid.uuid4().hex)
            self._active_conversation_id = conversation.id
            self._conversation_titled = False
            logger.info("Started conversation #%s", conversation.id)
        except Exception as e:
            logger.error("Failed to create conversation record: %s", e)
            self._active_conversation_id = None
            self._conversation_titled = False

    def _on_session_ended(self) -> None:
        """Callback from the client when the live session fully terminates.

        Runs on the asyncio worker thread. Marks the active conversation
        COMPLETED with an ended_at timestamp.
        """
        conversation_id = self._active_conversation_id
        self._active_conversation_id = None
        self._conversation_titled = False
        self.transcript_service.stop()
        if conversation_id is None:
            return
        try:
            ended = end_conversation(conversation_id)
            logger.info("Ended conversation #%s (%s)", conversation_id, ended.status if ended else "missing")
        except Exception as e:
            logger.error("Failed to end conversation #%s: %s", conversation_id, e)

    def _persist_message(self, role: str, text: str) -> None:
        """Persist a transcript line against the active conversation."""
        conversation_id = self._active_conversation_id
        if conversation_id is None or not text.strip():
            return
        message_role = _map_message_role(role)
        try:
            create_message(conversation_id, role=message_role, content=text)
            # Auto-title the conversation from its first user/external input.
            if (
                not self._conversation_titled
                and message_role in (MessageRole.USER, MessageRole.EVENT)
            ):
                title = text.strip()[:_TITLE_MAX_CHARS]
                if title:
                    update_conversation(conversation_id, title=title)
                    self._conversation_titled = True
        except Exception as e:
            logger.warning("Failed to persist message: %s", e)
