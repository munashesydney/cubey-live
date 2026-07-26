"""
Gemini Multimodal Live API Client using google-genai SDK.
Handles real-time audio streaming, response handling, and text interruptions (barge-in).
"""

import asyncio
import logging
import time
from typing import Callable, Optional
from google import genai
from google.genai import types

from src.config import AppConfig
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer

logger = logging.getLogger(__name__)

# System instruction for the robot AI persona with strict interruption handling
ROBOT_SYSTEM_INSTRUCTION = (
    "You are an AI embodied in a physical robot agent. You communicate naturally using speech. "
    "Respond concisely, warmly, and expressively in real time. "
    "CRITICAL INSTRUCTION: Whenever you receive any physical event enclosed in brackets, "
    "such as [HUMAN KICKED YOU], [OBSTACLE IN PATH], [WATER SPILLED ON SENSORS], or [HUMAN MOVED OUT OF FRAME], "
    "you MUST IMMEDIATELY cut off whatever you were talking about and react out loud in spoken voice "
    "right away to that specific incident (e.g. 'OUCH! Why did you kick me?!' or 'Whoa, an obstacle!'). "
    "Always speak your reaction out loud with high emotion and expressiveness."
)

class GeminiLiveClient:
    """Manages WebSocket connection to Gemini Multimodal Live API."""

    def __init__(
        self,
        config: AppConfig,
        recorder: AudioRecorder,
        player: AudioPlayer,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str, str], None]] = None, # role, text
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.recorder = recorder
        self.player = player
        self.on_status_change = on_status_change
        self.on_transcript = on_transcript
        self.on_log = on_log

        self.is_connected = False
        self._session = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

        # Temporary pause flag to prevent mic noise collision during text interruption
        self._pause_mic_until: float = 0.0

        # Initialize official GenAI client with v1alpha options for Live API
        self.genai_client = genai.Client(
            api_key=self.config.api_key,
            http_options={'api_version': 'v1alpha'}
        )

    def log(self, message: str) -> None:
        """Helper to send log to logger and UI callback."""
        logger.info(message)
        if self.on_log:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self.on_log, message)
            else:
                self.on_log(message)

    def set_status(self, status: str) -> None:
        """Update connection status."""
        self.log(f"Status: {status}")
        if self.on_status_change:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self.on_status_change, status)
            else:
                self.on_status_change(status)

    async def start_session(self) -> None:
        """Establish Live session and launch send/receive tasks."""
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self.set_status("Connecting to Gemini Live API...")

        # Configure Live API connection parameters
        try:
            live_config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.config.voice_name
                        )
                    )
                ),
                system_instruction=types.Content(
                    parts=[types.Part(text=ROBOT_SYSTEM_INSTRUCTION)]
                )
            )
        except Exception as config_err:
            msg = f"Config Error: {config_err}"
            self.log(f"❌ {msg}")
            self.set_status(msg)
            return

        try:
            self.log(f"Initiating WebSocket connection (Model: {self.config.model})...")
            async with self.genai_client.aio.live.connect(
                model=self.config.model,
                config=live_config
            ) as session:
                self._session = session
                self.is_connected = True
                self.set_status("Connected & Live 🟢")
                self.log(f"Connected to model '{self.config.model}' with voice '{self.config.voice_name}'")

                # Start audio recorder and player
                self.player.start()
                self.recorder.start(self._loop)

                # Create concurrent async tasks
                send_task = asyncio.create_task(self._send_audio_loop())
                receive_task = asyncio.create_task(self._receive_responses_loop())
                self._tasks = [send_task, receive_task]

                # Wait until session stop requested or task fails
                await self._stop_event.wait()
                self.log("Stopping Live session tasks...")
                
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)

        except asyncio.CancelledError:
            self.log("Live session cancelled.")
        except Exception as e:
            err_msg = str(e)
            if "APIError" in type(e).__name__ or "disabled" in err_msg.lower():
                err_msg = f"API Error: {e}"
            self.log(f"❌ Session Error: {err_msg}")
            self.set_status(f"Error: {err_msg[:60]}")
            logger.exception("Gemini Live session connection failed:")
        finally:
            self.is_connected = False
            self._session = None
            self.recorder.stop()
            self.player.stop()
            if not self._stop_event.is_set():
                self.set_status("Disconnected (Error) 🔴")
            else:
                self.set_status("Disconnected 🔴")

    def stop_session(self) -> None:
        """Signal live session to shut down."""
        self._stop_event.set()

    async def interrupt_with_text(self, text_payload: str) -> None:
        """
        Instant Text Interruption (Barge-in):
        1. Instantly flushes current speaker audio queue (<10ms cutoff).
        2. Briefly pauses mic audio stream to prevent ambient noise collision.
        3. Sends LiveClientContent payload over WebSocket with turn_complete=True.
        4. Forces Gemini to immediately react out loud in speech to the event.
        """
        if not self.is_connected or self._session is None:
            self.log("⚠️ Cannot send interruption: Live session is not connected.")
            return

        # 1. Flush local speaker playback buffer immediately
        self.player.clear()
        
        # 2. Briefly pause microphone audio sending (500ms) to ensure clean turn boundary
        self._pause_mic_until = time.time() + 0.5
        
        self.log(f"⚡ [INTERRUPTION TRIGGERED]: '{text_payload}'")

        if self.on_transcript:
            self.on_transcript("User Event", f"⚡ {text_payload}")

        try:
            # 3. Send text interruption (ClientContent) over open WebSocket using SDK's dedicated method
            await self._session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=f"[PHYSICAL EVENT DETECTED]: {text_payload}")]
                ),
                turn_complete=True
            )
            self.log("Sent client content interruption payload to Gemini Live API.")
        except Exception as e:
            self.log(f"❌ Failed to send text interruption: {e}")

    async def _send_audio_loop(self) -> None:
        """Task continuously reading mic PCM chunks and streaming to WebSocket."""
        try:
            while self.is_connected and self._session is not None:
                audio_data = await self.recorder.audio_queue.get()
                
                # Check if mic audio sending is temporarily paused for text interruption
                if time.time() < self._pause_mic_until:
                    self.recorder.audio_queue.task_done()
                    continue

                if audio_data and self._session:
                    await self._session.send_realtime_input(
                        audio=types.Blob(data=audio_data, mime_type="audio/pcm")
                    )
                self.recorder.audio_queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"Error in send audio loop: {e}")

    async def _receive_responses_loop(self) -> None:
        """Task receiving real-time audio responses from WebSocket."""
        try:
            while self.is_connected and self._session is not None:
                async for response in self._session.receive():
                    server_content = response.server_content
                    if server_content is None:
                        continue

                    # Handle model speech / audio turn
                    model_turn = server_content.model_turn
                    if model_turn is not None:
                        for part in model_turn.parts:
                            # Audio PCM chunk
                            if part.inline_data and part.inline_data.data:
                                self.player.play_chunk(part.inline_data.data)

                    # Handle server-side barge-in / interruption signal
                    if server_content.interrupted:
                        self.player.clear()
                        self.log("⚡ [Server Barge-in Detected] Gemini cut off generation.")

                    if server_content.turn_complete:
                        logger.debug("Turn complete.")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"Error in receive responses loop: {e}")
