"""
Gemini Multimodal Live API Client using google-genai SDK.
Handles real-time audio streaming, AI tool calls (react tool), response handling,
text interruptions, and native server-side voice barge-in.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional
from google import genai
from google.genai import types

from src.ai.prompts.gemini_live import SYSTEM_PROMPT as ROBOT_SYSTEM_INSTRUCTION
from src.config import AppConfig
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.client.tools import ToolContext, build_gemini_tools, dispatch_tool_call
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

_EVENT_LOOP_PROBE_INTERVAL_SECONDS = 0.1
_EVENT_LOOP_LAG_WARNING_SECONDS = 0.25
_EVENT_LOOP_LAG_REPORT_INTERVAL_SECONDS = 5.0


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
        embedding_service: Optional[EmbeddingService] = None,
        wheels_service: Optional[Any] = None,
    ):
        self.config = config
        self.recorder = recorder
        self.player = player
        self.on_status_change = on_status_change
        self.on_transcript = on_transcript
        self.on_log = on_log
        self.on_tool_reaction = on_tool_reaction
        self.on_session_ended = on_session_ended
        self.embedding_service = embedding_service
        self.wheels_service = wheels_service

        self.is_connected = False
        self._session = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._user_text_buffer: list[str] = []
        self._model_text_buffer: list[str] = []
        self._model_turn_active = False

        self._pause_mic_until: float = 0.0
        self._session_resumption_handle: Optional[str] = None
        self._connection_started_at = 0.0
        self._has_connected_once = False

        # Initialize official GenAI client
        self.genai_client = genai.Client(
            api_key=self.config.api_key,
            http_options={'api_version': 'v1alpha'}
        )

    def log(self, message: str) -> None:
        """Helper to send log to logger and UI callback."""
        logger.info(message)
        if self.on_log:
            try:
                self.on_log(message)
            except Exception:
                logger.exception("Live log callback failed")

    def set_status(self, status: str) -> None:
        """Update connection status."""
        self.log(f"Status: {status}")
        if self.on_status_change:
            try:
                self.on_status_change(status)
            except Exception:
                logger.exception("Live status callback failed")

    async def start_session(self) -> None:
        """Run one logical Live session across retryable WebSocket connections."""
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._model_turn_active = False
        self._user_text_buffer.clear()
        self._model_text_buffer.clear()
        self._session_resumption_handle = None
        self._has_connected_once = False
        self.set_status("Connecting to Gemini Live API...")

        fatal_error: Optional[Exception] = None
        retry_count = 0
        lag_monitor = asyncio.create_task(self._monitor_event_loop_lag())
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_live_connection()
                    if not self._stop_event.is_set():
                        raise ConnectionError("Gemini Live transport ended unexpectedly")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._stop_event.is_set():
                        break

                    connected_for = (
                        time.monotonic() - self._connection_started_at
                        if self._connection_started_at
                        else 0.0
                    )
                    if connected_for >= 30:
                        retry_count = 0

                    if (
                        not self._is_retryable_transport_error(exc)
                        or retry_count >= self.config.live_reconnect_attempts
                    ):
                        raise

                    retry_count += 1
                    delay = min(
                        self.config.live_reconnect_base_delay
                        * (2 ** (retry_count - 1)),
                        self.config.live_reconnect_max_delay,
                    )
                    self.is_connected = False
                    self._session = None
                    self.player.clear()
                    self.recorder.clear_queue()
                    self.set_status(
                        f"Reconnecting to Gemini ({retry_count}/{self.config.live_reconnect_attempts})…"
                    )
                    self.log(
                        f"Transient Live transport error: {exc}. "
                        f"Retrying in {delay:.1f}s."
                    )
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=delay
                        )
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            self.log("Live session cancelled.")
        except Exception as e:
            fatal_error = e
            err_msg = str(e)
            if "APIError" in type(e).__name__ or "disabled" in err_msg.lower():
                err_msg = f"API Error: {e}"
            self.log(f"❌ Session Error: {err_msg}")
            self.set_status(f"Error: {err_msg[:60]}")
            logger.exception("Gemini Live session connection failed:")
        finally:
            lag_monitor.cancel()
            await asyncio.gather(lag_monitor, return_exceptions=True)
            self.is_connected = False
            self._session = None
            self.recorder.stop()
            self.player.stop()
            if fatal_error is not None:
                self.set_status("Disconnected (Error) 🔴")
            else:
                self.set_status("Disconnected 🔴")
            if self.on_session_ended:
                try:
                    self.on_session_ended()
                except Exception as e:
                    logger.exception("on_session_ended callback failed: %s", e)

    async def _monitor_event_loop_lag(self) -> None:
        """Report stalls in the loop that owns real-time audio transport."""
        loop = asyncio.get_running_loop()
        expected = loop.time() + _EVENT_LOOP_PROBE_INTERVAL_SECONDS
        last_reported = 0.0
        while True:
            await asyncio.sleep(_EVENT_LOOP_PROBE_INTERVAL_SECONDS)
            now = loop.time()
            lag = max(0.0, now - expected)
            expected = now + _EVENT_LOOP_PROBE_INTERVAL_SECONDS
            if (
                lag >= _EVENT_LOOP_LAG_WARNING_SECONDS
                and now - last_reported >= _EVENT_LOOP_LAG_REPORT_INTERVAL_SECONDS
            ):
                last_reported = now
                logger.warning(
                    "Realtime event loop stalled for approximately %.0f ms",
                    lag * 1000,
                )

    def _build_live_config(self) -> types.LiveConnectConfig:
        """Create connection config with native server VAD, cloud transcription, and session resumption handle."""
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            tools=build_gemini_tools("live_model"),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.config.voice_name
                    )
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=(
                        types.StartSensitivity.START_SENSITIVITY_HIGH
                    ),
                    end_of_speech_sensitivity=(
                        types.EndSensitivity.END_SENSITIVITY_LOW
                    ),
                    prefix_padding_ms=self.config.vad_prefix_padding_ms,
                    silence_duration_ms=self.config.vad_silence_duration_ms,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
            ),
            session_resumption=types.SessionResumptionConfig(
                handle=self._session_resumption_handle
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=ROBOT_SYSTEM_INSTRUCTION)]
            ),
        )

    async def _run_live_connection(self) -> None:
        """Run one WebSocket connection; raise if either transport task fails."""
        resuming = self._session_resumption_handle is not None
        action = "Resuming" if resuming else "Initiating"
        self.log(
            f"{action} WebSocket connection with Tool Use "
            f"(Model: {self.config.model})..."
        )

        async with self.genai_client.aio.live.connect(
            model=self.config.model,
            config=self._build_live_config(),
        ) as session:
            self._session = session
            self.is_connected = True
            self._connection_started_at = time.monotonic()
            self.set_status(
                "Reconnected & Live 🟢"
                if self._has_connected_once
                else "Connected & Live 🟢"
            )
            self._has_connected_once = True
            self.log(
                f"Connected to model '{self.config.model}' with registered tools."
            )

            if not self.player.is_playing:
                self.player.start()
            if not self.recorder.is_recording:
                self.recorder.start(self._loop)
            else:
                self.recorder.clear_queue()

            send_task = asyncio.create_task(self._send_audio_loop())
            receive_task = asyncio.create_task(self._receive_responses_loop())
            stop_task = asyncio.create_task(self._stop_event.wait())
            self._tasks = [send_task, receive_task]

            try:
                done, _ = await asyncio.wait(
                    [send_task, receive_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    return

                for task in (send_task, receive_task):
                    if task not in done:
                        continue
                    error = task.exception()
                    if error is not None:
                        raise error
                raise ConnectionError("Gemini Live audio task stopped unexpectedly")
            finally:
                for task in (send_task, receive_task, stop_task):
                    task.cancel()
                await asyncio.gather(
                    send_task, receive_task, stop_task, return_exceptions=True
                )
                self.is_connected = False
                self._session = None

    @staticmethod
    def _is_retryable_transport_error(error: Exception) -> bool:
        """Return whether reconnecting is safe for this transport failure."""
        code = getattr(error, "code", None)
        try:
            numeric_code = int(code) if code is not None else None
        except (TypeError, ValueError):
            numeric_code = None
        if numeric_code in {1001, 1006, 1011, 1012, 1013}:
            return True

        text = f"{type(error).__name__}: {error}".lower()
        return any(
            marker in text
            for marker in (
                "1011",
                "internal error",
                "connectionclosed",
                "connection closed",
                "transport ended",
                "timed out",
                "timeout",
                "temporarily unavailable",
                "service unavailable",
                "websocket",
            )
        )

    def stop_session(self) -> None:
        """Signal live session to shut down."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        else:
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
        self._pause_mic_until = time.monotonic() + 0.5
        
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
        """Task continuously reading mic PCM chunks and streaming directly to WebSocket."""
        try:
            while self.is_connected and self._session is not None:
                audio_data = await self.recorder.audio_queue.get()
                try:
                    # Check mic pause window (after text interruption dispatch).
                    if time.monotonic() < self._pause_mic_until:
                        continue

                    await self._send_audio_chunk(audio_data)
                finally:
                    self.recorder.audio_queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log(f"Error in send audio loop: {e}")
            raise

    async def _send_audio_chunk(self, audio_data: bytes) -> None:
        if audio_data and self._session:
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type=f"audio/pcm;rate={self.config.input_sample_rate}",
                )
            )

    async def _receive_responses_loop(self) -> None:
        """Task receiving real-time audio responses and processing incoming AI tool calls."""
        try:
            while self.is_connected and self._session is not None:
                async for response in self._session.receive():
                    resumption = response.session_resumption_update
                    if (
                        resumption is not None
                        and resumption.resumable
                        and resumption.new_handle
                    ):
                        self._session_resumption_handle = resumption.new_handle

                    if response.go_away is not None:
                        self.log(
                            "Gemini Live connection will rotate soon "
                            f"(time left: {response.go_away.time_left or 'unknown'})"
                        )

                    # Handle AI Tool Calls (Function Calling)
                    tool_call = response.tool_call
                    if tool_call is not None:
                        for fn_call in tool_call.function_calls:
                            # Single dispatch through the tool registry — the
                            # live model is allowed everything the policy grants.
                            # Tool executors may touch SQLite, hardware, or the
                            # local embedding model. Never run them on the event
                            # loop that is continuously sending microphone PCM.
                            result_dict = await asyncio.to_thread(
                                dispatch_tool_call,
                                fn_call.name or "",
                                fn_call.args or {},
                                ToolContext(
                                    embedding_service=self.embedding_service,
                                    on_react=self._dispatch_tool_reaction,
                                    wheels_service=self.wheels_service,
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

                    # 1. Handle native user speech transcription from Gemini Live
                    input_transcription = server_content.input_transcription
                    if input_transcription is not None:
                        if input_transcription.text:
                            self._user_text_buffer.append(input_transcription.text)
                            self.log(
                                f"🎤 [Gemini Live User STT Chunk]: '{input_transcription.text}' "
                                f"(finished={input_transcription.finished})"
                            )
                        if input_transcription.finished:
                            self._flush_user_text()

                    # 2. Handle native model output speech transcription from Gemini Live
                    output_transcription = server_content.output_transcription
                    if output_transcription is not None:
                        if output_transcription.text:
                            self._model_text_buffer.append(output_transcription.text)
                            self.log(
                                f"🤖 [Gemini Live Model STT Chunk]: '{output_transcription.text}' "
                                f"(finished={output_transcription.finished})"
                            )
                        if output_transcription.finished:
                            self._flush_model_text()

                    # 3. Handle model speech turn audio
                    model_turn = server_content.model_turn
                    if model_turn is not None:
                        # Flush any pending user text when model begins response
                        self._flush_user_text()
                        for part in model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                self.player.play_chunk(part.inline_data.data)
                                self._model_turn_active = True

                    # Flush accumulated text at the end of turn
                    if server_content.turn_complete:
                        self._flush_user_text()
                        self._flush_model_text()
                        self._model_turn_active = False

                    # Handle server-side barge-in / interruption signal
                    if server_content.interrupted:
                        self._flush_user_text()
                        self._flush_model_text()
                        self._model_turn_active = False
                        self.player.clear()
                        self.log("⚡ [Server Barge-in Detected] Gemini cut off generation.")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log(f"Error in receive responses loop: {e}")
            raise

    def _flush_user_text(self) -> None:
        """Emit accumulated user speech transcript line and reset the buffer."""
        text = "".join(self._user_text_buffer).strip()
        self._user_text_buffer.clear()
        if text:
            self.log(f"🎤 [Gemini Live User STT]: '{text}'")
            if self.on_transcript:
                self.on_transcript("User", text)

    def _flush_model_text(self) -> None:
        """Emit accumulated model text as one transcript line and reset the buffer."""
        text = "".join(self._model_text_buffer).strip()
        self._model_text_buffer.clear()
        if text:
            self.log(f"🤖 [Gemini Live Model STT]: '{text}'")
            if self.on_transcript:
                self.on_transcript("Model", text)

    def _dispatch_tool_reaction(self, reaction_type: str) -> None:
        """Dispatch a reaction through the controller's thread-safe bridge."""
        if self.on_tool_reaction:
            try:
                self.on_tool_reaction(reaction_type)
            except Exception:
                logger.exception("Live reaction callback failed")

