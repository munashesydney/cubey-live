"""
Application Controller orchestrating asyncio worker threads, audio pipeline, Gemini Live client, and GUI.
"""

import asyncio
import logging
import threading
from typing import Optional

from src.config import config
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.client.live_client import GeminiLiveClient
from src.gui.app import GeminiLiveApp

logger = logging.getLogger(__name__)

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

        self._session_task: Optional[asyncio.Task] = None

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
            on_level_change=self._on_mic_level_changed
        )

        # 3. Create Gemini Live Client with Tool Reaction Callback
        self.client = GeminiLiveClient(
            config=self.config,
            recorder=self.recorder,
            player=self.player,
            on_status_change=self._on_status_changed,
            on_transcript=self._on_transcript_received,
            on_log=self._on_log_received,
            on_tool_reaction=self._on_tool_reaction_triggered
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
            self.gui.trigger_robot_reaction(reaction_type)

    def _on_mic_level_changed(self, level: float) -> None:
        """Callback from audio recorder (runs thread-safely)."""
        if self.gui and self.gui.winfo_exists():
            self.gui.update_mic_level(level)

    def _on_status_changed(self, status: str) -> None:
        """Callback when Live client connection status changes."""
        if self.gui and self.gui.winfo_exists():
            self.gui.set_status(status)

    def _on_transcript_received(self, role: str, text: str) -> None:
        """Callback when transcript text is received from Gemini or User event."""
        if self.gui and self.gui.winfo_exists():
            self.gui.append_transcript(role, text)

    def _on_log_received(self, message: str) -> None:
        """Callback for log messages."""
        if self.gui and self.gui.winfo_exists():
            self.gui.append_log(message)
