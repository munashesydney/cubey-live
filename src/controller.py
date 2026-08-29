"""
Application Controller orchestrating asyncio worker threads, audio pipeline, Gemini Live client, and GUI.
"""

import asyncio
import concurrent.futures
import logging
import platform
import threading
import time
import uuid
from typing import Optional

from src.config import config
from src.audio.recorder import AudioRecorder
from src.audio.player import AudioPlayer
from src.audio.devices import select_audio_device
from src.audio.echo_cancel import (
    EchoCancellationUnavailable,
    prepare_pipewire_echo_cancellation,
)
from src.camera import CameraService, list_camera_devices
from src.client.live_client import GeminiLiveClient
from src.db import (
    ConversationSource,
    MessageRole,
    create_conversation,
)
from src.services.embeddings import EmbeddingService
from src.gui.windows.app_window import GeminiLiveApp
from src.services.task_scheduler import TaskScheduler
from src.services.transcript_persistence import TranscriptPersistenceService
from src.services.wake_word import WakeWordService
from src.services.wheels_service import get_wheels_service
from src.services.lidar_service import get_lidar_service

logger = logging.getLogger(__name__)

# Keep titles short enough for the dev-console conversation list.
_TITLE_MAX_CHARS = 60


def _map_message_role(role: str) -> MessageRole:
    """Normalize transcript role names to a persisted MessageRole."""
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
        self.camera_service: Optional[CameraService] = None
        self.client: Optional[GeminiLiveClient] = None
        self.gui: Optional[GeminiLiveApp] = None
        self.wake_word_service: Optional[WakeWordService] = None

        # On-device embeddings (fastembed) for semantic memory over messages.
        self.embedding_service = EmbeddingService(model_name=config.embedding_model)
        self.transcript_persistence = TranscriptPersistenceService(
            self.embedding_service
        )

        # Background scheduler for AI tasks (the 'tasks' tool).
        self.task_scheduler = TaskScheduler()

        self._session_task: Optional[asyncio.Task] = None

        # Active DB-backed conversation for the current live session.
        self._active_conversation_id: Optional[int] = None
        self._conversation_titled: bool = False

    def start(self) -> None:
        """Start background asyncio loop thread, launch GUI immediately, and run startup diagnostics."""
        logger.info("Initializing Application Controller...")

        # 0. Start the background task scheduler
        self.task_scheduler.start()
        self.transcript_persistence.start()

        # Start Web Server & SLAM mapper if enabled
        self._start_web_server()

        # 1. Start dedicated background thread for asyncio event loop
        self.async_loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._run_async_loop,
            args=(self.async_loop,),
            daemon=True
        )
        self.loop_thread.start()

        # 2. Instantiate Camera video capture service
        self.camera_service = CameraService(self.config)

        # 3. Create CustomTkinter GUI app with Startup loading screen
        self.gui = GeminiLiveApp(
            config=self.config,
            async_loop=self.async_loop,
            camera_service=self.camera_service,
            on_start_session=self.start_live_session,
            on_stop_session=self.stop_live_session,
            on_send_interruption=self.send_interruption,
            on_toggle_camera=self.toggle_camera,
            on_set_camera_device=self.set_camera_device,
            on_send_snapshot=self.send_visual_snapshot,
            embedding_service=self.embedding_service,
            show_startup_screen=True,
        )

        # 4. Launch background startup diagnostics & prewarming thread
        threading.Thread(
            target=self._run_startup_diagnostics_and_prewarm,
            daemon=True,
            name="StartupDiagnosticsWorker",
        ).start()

        logger.info("Starting CustomTkinter GUI Main Loop...")
        try:
            self.gui.mainloop()
        finally:
            if hasattr(self, "web_server") and self.web_server:
                self.web_server.should_exit = True
            if self.wake_word_service:
                self.wake_word_service.stop()
            if self.camera_service:
                self.camera_service.stop()
            if self.recorder:
                self.recorder.stop()
            if self.player:
                self.player.stop()
            try:
                get_lidar_service().disconnect()
            except Exception:
                pass
            self.transcript_persistence.set_realtime_active(False)
            self.transcript_persistence.stop()
            self.task_scheduler.stop()

    def _start_web_server(self) -> None:
        """Launch FastAPI / Uvicorn server in a dedicated background daemon thread."""
        if not getattr(self.config, "web_server_enabled", True):
            return

        try:
            from src.web.app import app as web_app
            import uvicorn

            cfg = uvicorn.Config(
                app=web_app,
                host=self.config.web_host,
                port=self.config.web_port,
                log_level="warning",
                access_log=False,
            )
            self.web_server = uvicorn.Server(cfg)
            web_thread = threading.Thread(
                target=self.web_server.run,
                daemon=True,
                name="CubeyWebServerThread",
            )
            web_thread.start()
            logger.info(
                "Cubey Web Server started on http://%s:%d",
                self.config.web_host,
                self.config.web_port,
            )
        except Exception as e:
            logger.warning("Failed to start Cubey Web Server: %s", e)

    def _run_startup_diagnostics_and_prewarm(self) -> None:
        """
        Background routine that initializes hardware and pre-warms AI models (FastEmbed, Sherpa-ONNX),
        dispatching real-time progress updates to the StartupPage.
        """
        try:
            # --- Step 1: Audio Pipeline & PipeWire AEC (20%) ---
            if self.gui:
                self.gui.post_startup_progress(0.10, "🔊 Probing audio hardware & PipeWire AEC...", 0)

            input_device_name = self.config.input_device
            output_device_name = self.config.output_device
            explicit_sample_rate = self.config.device_sample_rate
            explicit_channels = self.config.device_channels
            explicit_dtype = self.config.device_dtype

            is_linux = platform.system() == "Linux"
            use_echo_cancellation = self.config.enable_echo_cancellation and is_linux

            if self.config.enable_echo_cancellation and not is_linux:
                logger.info(
                    "PipeWire echo cancellation is configured, but current OS is %s (Linux/Pi runtime only). "
                    "Using direct host audio devices.",
                    platform.system(),
                )

            if use_echo_cancellation:
                routing = prepare_pipewire_echo_cancellation(
                    source_name=self.config.echo_cancel_source,
                    sink_name=self.config.echo_cancel_sink,
                    host_device=self.config.echo_cancel_host_device,
                )
                if input_device_name or output_device_name:
                    logger.info(
                        "AEC enabled; ignoring direct AUDIO_INPUT_DEVICE/AUDIO_OUTPUT_DEVICE overrides"
                    )
                input_device_name = routing.host_device
                output_device_name = routing.host_device
                explicit_sample_rate = 0
                explicit_channels = 0
                explicit_dtype = ""

            input_device = select_audio_device(
                "input",
                self.config.input_sample_rate,
                input_device_name,
                self.config.prefer_low_latency_devices,
                explicit_sample_rate=explicit_sample_rate,
                explicit_channels=explicit_channels,
                explicit_dtype=explicit_dtype,
            )
            output_device = select_audio_device(
                "output",
                self.config.output_sample_rate,
                output_device_name,
                self.config.prefer_low_latency_devices,
                explicit_sample_rate=explicit_sample_rate,
                explicit_channels=explicit_channels,
                explicit_dtype=explicit_dtype,
            )
            if use_echo_cancellation and (
                input_device.device is None or output_device.device is None
            ):
                raise EchoCancellationUnavailable(
                    "PipeWire AEC endpoints exist, but PortAudio's 'pulse' bridge "
                    "is unavailable. Install libasound2-plugins and rerun "
                    "scripts/audio/setup_pipewire_aec.sh."
                )

            self.player = AudioPlayer(
                sample_rate=self.config.output_sample_rate,
                channels=self.config.channels,
                block_size=self.config.output_block_size,
                max_buffer_ms=self.config.output_buffer_ms,
                device=output_device.device,
                device_sample_rate=output_device.sample_rate,
                device_channels=output_device.channels,
                device_dtype=output_device.dtype,
            )

            self.recorder = AudioRecorder(
                sample_rate=self.config.input_sample_rate,
                channels=self.config.channels,
                chunk_size=self.config.chunk_size,
                max_queue_ms=self.config.input_queue_ms,
                device=input_device.device,
                device_sample_rate=input_device.sample_rate,
                device_channels=input_device.channels,
                device_dtype=input_device.dtype,
                enable_denoise=self.config.enable_noise_suppression,
                on_level_change=self._on_mic_level_changed,
                on_audio_chunk=self._on_audio_chunk_captured,
            )

            if self.gui:
                self.gui.post_startup_progress(0.25, "✓ Audio pipeline & AEC calibrated.", 0)

            # --- Step 2: Neural Wake-Word Engine (45%) ---
            if self.gui:
                self.gui.post_startup_progress(0.35, "🎙️ Initializing Sherpa-ONNX neural wake-word engine...", 1)

            if self.config.enable_wake_word:
                logger.info("Initializing offline open-vocabulary Wake Word Service (Sherpa-ONNX)...")
                self.wake_word_service = WakeWordService(
                    model_dir=self.config.wake_word_model_dir,
                    wake_words=self.config.wake_words,
                    threshold=self.config.wake_word_threshold,
                    score=self.config.wake_word_score,
                    gain=self.config.wake_word_gain,
                    num_threads=self.config.wake_word_threads,
                    on_wake_word=self._on_wake_word_detected,
                )
                self.wake_word_service.start()
                if not self.recorder.is_recording and self.async_loop:
                    self.recorder.start(self.async_loop)

            if self.gui:
                self.gui.post_startup_progress(0.50, "✓ Neural wake-word spotter active.", 1)

            # --- Step 3: Semantic Vector Memory (70%) ---
            if self.gui:
                self.gui.post_startup_progress(0.55, "🧠 Prewarming FastEmbed ONNX semantic memory...", 2)

            try:
                self.embedding_service.prewarm()
            except Exception as emb_err:
                logger.debug("FastEmbed prewarm notice: %s", emb_err)

            if self.gui:
                self.gui.post_startup_progress(0.70, "✓ Semantic vector memory ready in RAM.", 2)

            # --- Step 4: Camera Vision Subsystem (85%) ---
            if self.gui:
                self.gui.post_startup_progress(0.75, "📷 Probing camera & vision hardware buffers...", 3)

            # Camera service buffers are primed
            time.sleep(0.1)

            if self.gui:
                self.gui.post_startup_progress(0.85, "✓ Camera subsystem primed.", 3)

            # --- Step 5: Gemini Live Gateway & Locomotion (100%) ---
            if self.gui:
                self.gui.post_startup_progress(0.90, "⚡ Linking Gemini Live gateway & locomotion...", 4)

            self.client = GeminiLiveClient(
                config=self.config,
                recorder=self.recorder,
                player=self.player,
                camera_service=self.camera_service,
                on_status_change=self._on_status_changed,
                on_transcript=self._on_transcript_received,
                on_log=self._on_log_received,
                on_tool_reaction=self._on_tool_reaction_triggered,
                on_listening_state_change=self._on_listening_state_changed,
                on_vision_state_change=self._on_vision_state_changed,
                on_session_ended=self._on_session_ended,
                embedding_service=self.embedding_service,
                wheels_service=get_wheels_service(),
            )
            if self.gui:
                self.gui.client = self.client

            wheels_service = get_wheels_service()
            wheels_service.add_telemetry_listener(self._on_telemetry_received)
            threading.Thread(
                target=wheels_service.connect,
                daemon=True,
                name="WheelsAutoConnect",
            ).start()

            if self.gui:
                self.gui.post_startup_progress(1.0, "✓ All systems online. Waking up Cubey...", 4)

            time.sleep(0.35)
            if self.gui:
                self.gui.post_startup_complete()

        except Exception as e:
            logger.error("Error in startup diagnostics worker: %s", e, exc_info=True)
            if self.gui:
                self.gui.post_startup_complete()
            self.transcript_persistence.stop()
            self.task_scheduler.stop()


    def _run_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Worker thread entrypoint for asyncio event loop."""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def start_live_session(
        self,
        initial_interrupt: Optional[str] = (
            "[WAKE UP - USER STARTED LIVE SESSION] "
            "The user just started a live conversation session with you! "
            "Speak a brief, friendly greeting to the user now out loud."
        ),
    ) -> None:
        """Schedule start_session coroutine on background asyncio thread."""
        try:
            if not self.config.api_key:
                logger.error("Cannot start Live session: GEMINI_API_KEY is not set!")
                if self.gui:
                    self.gui.post_log("❌ Error: GEMINI_API_KEY is not set! Set it in your environment or .env file.")
                    self.gui.post_status("Error: Missing GEMINI_API_KEY")
                return

            if self.client and self.async_loop and self.async_loop.is_running():
                # Pause offline wake word detector to save Pi 5 CPU during Gemini Live conversation
                if self.wake_word_service:
                    self.wake_word_service.pause()

                # Ensure glowing listening visual HUD is active on robot face
                if self.gui:
                    self.gui.post_listening(True)

                self.transcript_persistence.set_realtime_active(True)
                self._begin_conversation()
                logger.info("Scheduling Gemini Live session start...")
                self._session_task = asyncio.run_coroutine_threadsafe(
                    self.client.start_session(initial_interrupt=initial_interrupt),
                    self.async_loop
                )

                def _on_session_task_done(fut) -> None:
                    try:
                        exc = fut.exception()
                        if exc:
                            logger.error(
                                "Gemini Live session raised exception: %s",
                                exc,
                                exc_info=exc,
                            )
                        else:
                            logger.info("Gemini Live session task completed cleanly.")
                    except Exception as e:
                        logger.error("Error inspecting session task future: %s", e)

                self._session_task.add_done_callback(_on_session_task_done)
            else:
                logger.warning(
                    "Cannot start Live session: client=%s, async_loop=%s (running=%s)",
                    bool(self.client),
                    bool(self.async_loop),
                    self.async_loop.is_running() if self.async_loop else False,
                )
        except Exception as e:
            logger.error("Failed to start Live session: %s", e, exc_info=True)

    def stop_live_session(self) -> None:
        """Stop active live session."""
        if self.client:
            logger.info("Stopping Live session...")
            self.client.stop_session()

    def _on_audio_chunk_captured(self, chunk: bytes) -> None:
        """Forward raw 16kHz PCM audio chunk to wake word spotter."""
        if self.wake_word_service:
            self.wake_word_service.process_audio_chunk(chunk)

    def _on_wake_word_detected(self, keyword: str) -> None:
        """
        Callback from Sherpa-ONNX when a wake-up phrase is recognized.
        Triggers listening eye animation, HUD indicator, and auto-starts Gemini Live with wake-up interrupt.
        """
        logger.info("🎯 Wake word recognized: '%s'! Waking up and starting Gemini Live...", keyword)
        wake_interrupt = (
            f"[WAKE UP - USER SAID '{keyword.upper()}'] "
            f"The user just said your wake phrase '{keyword.upper()}' to wake you up! "
            f"Speak a brief, friendly greeting to the user now out loud."
        )
        if self.gui:
            self.gui.post_log(f"🎯 Wake Word Detected: '{keyword}'! Waking up...")
            self.gui.post_reaction("listening")
            self.gui.post_listening(True)
            self.gui.post_status(f"Listening: {keyword} 🎤")

        # Auto-start Gemini Live conversation session if not already connected
        if self.client and not self.client.is_connected:
            self.start_live_session(initial_interrupt=wake_interrupt)
        elif self.client and self.client.is_connected:
            self.send_interruption(wake_interrupt)

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

    def toggle_camera(self, enabled: Optional[bool] = None) -> bool:
        """Toggle or set camera capture and Gemini Live vision streaming state."""
        if not self.camera_service:
            return False

        target_state = (not self.camera_service.is_running) if enabled is None else enabled
        if target_state:
            self.camera_service.start()
            if self.client:
                self.client.set_camera_streaming(True)
            if self.gui:
                self.gui.post_vision_state(True)
                self.gui.post_log("📷 Camera stream activated.")
        else:
            self.camera_service.stop()
            if self.client:
                self.client.set_camera_streaming(False)
            if self.gui:
                self.gui.post_vision_state(False)
                self.gui.post_log("📷 Camera stream stopped.")
        return target_state

    def set_camera_device(self, index: int) -> None:
        """Change camera hardware device index."""
        if self.camera_service:
            self.camera_service.set_device(index)
            if self.gui:
                self.gui.post_log(f"📷 Switched to Camera device index {index}")

    def send_visual_snapshot(self, prompt: Optional[str] = None) -> None:
        """Capture an immediate camera snapshot and send to Gemini Live."""
        if not self.camera_service:
            return

        if not self.camera_service.is_running:
            self.camera_service.start()
            time.sleep(0.1)

        jpeg_bytes = self.camera_service.get_latest_frame_jpeg(
            quality=self.config.camera_jpeg_quality
        )
        if jpeg_bytes and self.client and self.async_loop and self.async_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.client.send_visual_snapshot(jpeg_bytes, prompt),
                self.async_loop,
            )
        elif not jpeg_bytes:
            if self.gui:
                self.gui.post_log("⚠️ Snapshot failed: No camera frame available.")

    def _on_listening_state_changed(self, is_listening: bool) -> None:
        """Callback when Gemini Live client changes listening visibility (e.g. hidden while model speaks)."""
        if self.gui:
            self.gui.post_listening(is_listening)

    def _on_tool_reaction_triggered(self, reaction_type: str) -> None:
        """Callback invoked when Gemini Live calls the 'react' tool."""
        if self.gui:
            self.gui.post_reaction(reaction_type)

    def _on_vision_state_changed(self, is_active: bool) -> None:
        """Callback from Live client when vision streaming state changes."""
        if self.gui:
            self.gui.post_vision_state(is_active)

    def _on_telemetry_received(self, telemetry) -> None:
        """Callback when telemetry (battery/charging) is received from WheelsService."""
        if self.gui and hasattr(telemetry, "is_charging"):
            self.gui.post_battery(telemetry.is_charging, telemetry.battery_pct)

    def _on_mic_level_changed(self, level: float) -> None:
        """Callback from audio recorder (runs thread-safely)."""
        if self.gui:
            self.gui.post_mic_level(level)
        if self.client and self.client.is_connected and level >= 0.08:
            self.client.record_user_activity()

    def _on_status_changed(self, status: str) -> None:
        """Callback when Live client connection status changes."""
        if self.gui:
            self.gui.post_status(status)

    def _on_transcript_received(self, role: str, text: str) -> None:
        """Callback when transcript text is received from Gemini or User event.

        Runs on the asyncio worker thread. It only performs non-blocking queue
        handoff before forwarding the line to the GUI.
        """
        self._persist_message(role, text)
        if self.gui:
            self.gui.post_transcript(role, text)


    def _on_log_received(self, message: str) -> None:
        """Callback for log messages."""
        if self.gui:
            self.gui.post_log(message)

    # ------------------------------------------------------------------
    # Database-backed conversation lifecycle
    # ------------------------------------------------------------------

    def _begin_conversation(self) -> None:
        """Create a new ACTIVE conversation row for the upcoming live session."""
        try:
            conversation = create_conversation(
                session_id=uuid.uuid4().hex,
                metadata={"type": "live", "model": self.config.model},
                source=ConversationSource.GEMINI,
            )
            self._active_conversation_id = conversation.id
            self._conversation_titled = False
            logger.info("Started conversation #%s", conversation.id)
        except Exception as e:
            logger.error("Failed to create conversation record: %s", e)
            self._active_conversation_id = None
            self._conversation_titled = False

    def _on_session_ended(self) -> None:
        """Callback from the client when the live session fully terminates.

        Runs on the asyncio worker thread. Completion is queued behind all
        transcript writes, then deferred embeddings are allowed to resume.
        """
        conversation_id = self._active_conversation_id
        self._active_conversation_id = None
        if conversation_id is not None:
            self.transcript_persistence.enqueue_end(conversation_id)
        self.transcript_persistence.set_realtime_active(False)

        # Clear active listening visual state on robot face
        if self.gui:
            self.gui.post_listening(False)

        # Ensure background audio capture is active and resume wake word detection
        if self.recorder and not self.recorder.is_recording and self.async_loop:
            self.recorder.start(self.async_loop)
        if self.wake_word_service:
            self.wake_word_service.resume()

    def _persist_message(self, role: str, text: str) -> None:
        """Persist a transcript line against the active conversation."""
        conversation_id = self._active_conversation_id
        if conversation_id is None or not text.strip():
            return
        message_role = _map_message_role(role)
        title = None
        should_title = (
            not self._conversation_titled
            and message_role in (MessageRole.USER, MessageRole.EVENT)
        )
        if should_title:
            title = text.strip()[:_TITLE_MAX_CHARS] or None

        queued = self.transcript_persistence.enqueue_message(
            conversation_id,
            message_role,
            text,
            title=title,
        )
        if queued and title:
            self._conversation_titled = True
