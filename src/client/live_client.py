"""
Gemini Multimodal Live API Client using google-genai SDK.
Handles real-time audio streaming, AI tool calls (react tool), response handling,
text interruptions, and Volume-Thresholded Acoustic Echo Gating for barge-in.
"""

import asyncio
import logging
import time
import numpy as np
from typing import Callable, Optional
from google import genai
from google.genai import types

from src.ai.prompts.gemini_live import SYSTEM_PROMPT as ROBOT_SYSTEM_INSTRUCTION
from src.config import AppConfig
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.client.tools import ToolContext, build_gemini_tools, dispatch_tool_call
from src.services.embeddings import EmbeddingService
from src.services.stt import LocalTranscriptService

logger = logging.getLogger(__name__)

def compute_pcm_rms(audio_data: bytes) -> float:
    """Calculate RMS volume level of 16-bit PCM mono audio chunk (0.0 to 1.0)."""
    if not audio_data:
        return 0.0
    arr = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    if len(arr) == 0:
        return 0.0
    rms = np.sqrt(np.mean(arr ** 2)) / 32768.0
    return float(rms)

class GeminiLiveClient:
    """Manages WebSocket connection to Gemini Multimodal Live API with Tool Calling."""

    def __init__(
        self,
        config: AppConfig,
        recorder: AudioRecorder,
        player: AudioPlayer,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str, str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_tool_reaction: Optional[Callable[[str], None]] = None,
        on_session_ended: Optional[Callable[[], None]] = None,
        transcript_service: Optional[LocalTranscriptService] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.config = config
        self.recorder = recorder
        self.player = player
        self.on_status_change = on_status_change
        self.on_transcript = on_transcript
        self.on_log = on_log
        self.on_tool_reaction = on_tool_reaction
        self.on_session_ended = on_session_ended
        self.transcript_service = transcript_service
        self.embedding_service = embedding_service

        self.is_connected = False
        self._session = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._model_text_buffer: list[str] = []

        self._pause_mic_until: float = 0.0

        # Minimum RMS volume threshold required to interrupt AI while AI is speaking
        self.voice_interruption_threshold = 0.12

        # Initialize official GenAI client
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

        try:
            live_config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                tools=build_gemini_tools("live_model"),  # Register react tool
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
            self.log(f"Initiating WebSocket connection with Tool Use (Model: {self.config.model})...")
            async with self.genai_client.aio.live.connect(
                model=self.config.model,
                config=live_config
            ) as session:
                self._session = session
                self.is_connected = True
                self.set_status("Connected & Live 🟢")
                self.log(f"Connected to model '{self.config.model}' with registered 'react' tool.")

                # Start audio recorder and player
                self.player.start()
                self.recorder.start(self._loop)

                # Create concurrent async tasks
                send_task = asyncio.create_task(self._send_audio_loop())
                receive_task = asyncio.create_task(self._receive_responses_loop())
                self._tasks = [send_task, receive_task]

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
            if self.on_session_ended:
                try:
                    self.on_session_ended()
                except Exception as e:
                    logger.exception("on_session_ended callback failed: %s", e)

    def stop_session(self) -> None:
        """Signal live session to shut down."""
        self._stop_event.set()

    async def interrupt_with_text(self, text_payload: str) -> None:
        """
        Send text interruption context to Gemini Live:
        Gemini receives the event and decides autonomously whether to invoke the 'react' tool.
        """
        if not self.is_connected or self._session is None:
            self.log("⚠️ Cannot send interruption: Live session is not connected.")
            return

        self.player.clear()
        self._pause_mic_until = time.time() + 0.5
        
        self.log(f"⚡ [EVENT CONTEXT SENT TO AI]: '{text_payload}'")

        if self.on_transcript:
            self.on_transcript("User Event", f"⚡ {text_payload}")

        try:
            await self._session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=f"[PHYSICAL EVENT DETECTED]: {text_payload}")]
                ),
                turn_complete=True
            )
            self.log("Sent client content payload to Gemini Live API.")
        except Exception as e:
            self.log(f"❌ Failed to send text payload: {e}")

    async def _send_audio_loop(self) -> None:
        """
        Task continuously reading mic PCM chunks and streaming to WebSocket.
        Applies Volume-Thresholded Mic Gating while AI speaker is actively playing.
        """
        try:
            while self.is_connected and self._session is not None:
                audio_data = await self.recorder.audio_queue.get()
                
                # Check mic pause window (after text interruption dispatch)
                if time.time() < self._pause_mic_until:
                    self.recorder.audio_queue.task_done()
                    continue

                # Threshold mic gate while AI speaker is actively playing audio
                if self.player.is_speaking:
                    rms = compute_pcm_rms(audio_data)
                    # If RMS is below threshold (0.12), drop chunk to suppress speaker echo feedback.
                    # If RMS >= 0.12, genuine human voice is speaking loud enough to interrupt!
                    if rms < self.voice_interruption_threshold:
                        self.recorder.audio_queue.task_done()
                        continue
                    else:
                        self.log(f"🎙️ [Human Voice Interruption] Mic RMS {rms:.3f} >= {self.voice_interruption_threshold}")

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
        """Task receiving real-time audio responses and processing incoming AI tool calls."""
        try:
            while self.is_connected and self._session is not None:
                async for response in self._session.receive():
                    # Handle AI Tool Calls (Function Calling)
                    tool_call = response.tool_call
                    if tool_call is not None:
                        for fn_call in tool_call.function_calls:
                            # Single dispatch through the tool registry — the
                            # live model is allowed everything the policy grants.
                            result_dict = dispatch_tool_call(
                                fn_call.name or "",
                                fn_call.args or {},
                                ToolContext(
                                    embedding_service=self.embedding_service,
                                    on_react=self._dispatch_tool_reaction,
                                ),
                            )
                            self.log(
                                f"🔧 [AI Tool Call]: {fn_call.name} -> {result_dict.get('status')}"
                            )

                            # Return tool response frame back over WebSocket
                            try:
                                await self._session.send_tool_response(
                                    function_responses=[
                                        types.FunctionResponse(
                                            name=fn_call.name,
                                            id=fn_call.id,
                                            response=result_dict
                                        )
                                    ]
                                )
                                self.log(f"✓ Returned tool_response for '{fn_call.name}'")
                            except Exception as tool_err:
                                self.log(f"❌ Error sending tool response: {tool_err}")

                    server_content = response.server_content
                    if server_content is None:
                        continue

                    # Handle model speech turn
                    model_turn = server_content.model_turn
                    if model_turn is not None:
                        for part in model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                self.player.play_chunk(part.inline_data.data)
                                if self.transcript_service:
                                    # The model replying ends the user's turn.
                                    self.transcript_service.flush_user_turn()
                                    self.transcript_service.feed_model_audio(
                                        part.inline_data.data
                                    )
                            if part.text:
                                self._model_text_buffer.append(part.text)

                    # Flush accumulated model text at the end of its turn
                    if server_content.turn_complete:
                        self._flush_model_text()
                        if self.transcript_service:
                            self.transcript_service.flush_model_turn()

                    # Handle server-side barge-in / interruption signal
                    if server_content.interrupted:
                        self._flush_model_text()
                        if self.transcript_service:
                            self.transcript_service.flush_model_turn()
                        self.player.clear()
                        self.log("⚡ [Server Barge-in Detected] Gemini cut off generation.")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"Error in receive responses loop: {e}")

    def _flush_model_text(self) -> None:
        """Emit accumulated model text as one transcript line and reset the buffer."""
        text = " ".join(part.strip() for part in self._model_text_buffer if part.strip())
        self._model_text_buffer = []
        if text and self.on_transcript:
            self.on_transcript("Model", text)

    def _dispatch_tool_reaction(self, reaction_type: str) -> None:
        """Thread-safely dispatch tool reaction to GUI on main thread."""
        if self.on_tool_reaction and self._loop:
            self._loop.call_soon_threadsafe(self.on_tool_reaction, reaction_type)
