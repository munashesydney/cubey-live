"""
Gemini Live page — embedded in the DeveloperWindow shell.

Holds live session controls (mic meter, mute, start/stop), real-time camera
streaming controls & live video preview, physical event interruption panel,
and transcript / logs / conversations tabs.
"""

import datetime
import logging
import threading
import time
from typing import Any, Callable, Optional
import customtkinter as ctk
from PIL import Image, ImageTk

from src.camera.devices import list_camera_devices
from src.config import AppConfig
from src.db import (
    ConversationSource,
    ConversationStatus,
    get_conversation,
    list_conversations,
    list_messages,
)

logger = logging.getLogger(__name__)


class LivePage(ctk.CTkFrame):
    """Gemini Live session control page with real-time camera streaming & preview."""

    def __init__(
        self,
        master,
        config: AppConfig,
        on_start_session: Callable[[], None],
        on_stop_session: Callable[[], None],
        on_send_interruption: Callable[[str], None],
        on_toggle_mute: Callable[[bool], None],
        is_session_active: bool = False,
        camera_service: Optional[Any] = None,
        on_toggle_camera: Optional[Callable[[Optional[bool]], bool]] = None,
        on_set_camera_device: Optional[Callable[[int], None]] = None,
        on_send_snapshot: Optional[Callable[[Optional[str]], None]] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app_config = config
        self.on_start_session = on_start_session
        self.on_stop_session = on_stop_session
        self.on_send_interruption = on_send_interruption
        self.on_toggle_mute = on_toggle_mute
        self.is_session_active = is_session_active
        self.camera_service = camera_service
        self.on_toggle_camera = on_toggle_camera
        self.on_set_camera_device = on_set_camera_device
        self.on_send_snapshot = on_send_snapshot

        self.is_camera_active = (
            self.camera_service.is_running if self.camera_service else False
        )

        # Video preview state
        self._preview_lock = threading.Lock()
        self._latest_pil_frame: Optional[Image.Image] = None
        self._preview_tk_img: Optional[ImageTk.PhotoImage] = None
        self._preview_scheduled = False

        self._create_layout()

        # Connect camera preview callback if camera service is provided
        if self.camera_service:
            self.camera_service.add_preview_listener(self._on_camera_frame_captured)

        # Start preview render loop
        self._preview_after_id = self.after(50, self._render_preview_loop)

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
            text_color="#F5E0DC",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text=f"Model: {self.app_config.model} | Voice: {self.app_config.voice_name}",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
        ).pack(anchor="w")

        # Right Header Controls
        controls_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        controls_box.pack(side="right", padx=15, pady=8)

        # Mic Level Meter
        mic_box = ctk.CTkFrame(controls_box, fg_color="transparent")
        mic_box.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            mic_box, text="Mic Level:", font=ctk.CTkFont(size=11), text_color="#BAC2DE"
        ).pack(anchor="w")
        self.mic_meter = ctk.CTkProgressBar(
            mic_box, width=80, height=10, progress_color="#A6E3A1"
        )
        self.mic_meter.set(0.0)
        self.mic_meter.pack(pady=2)

        # Mute Switch
        self.mute_switch = ctk.CTkSwitch(
            controls_box,
            text="Mute Mic",
            command=self._handle_mute_toggle,
            font=ctk.CTkFont(size=12),
        )
        self.mute_switch.pack(side="left", padx=6)

        # Denoiser Noise Suppression Switch
        self.denoiser_switch = ctk.CTkSwitch(
            controls_box,
            text="🧠 Denoise",
            command=self._handle_denoiser_toggle,
            font=ctk.CTkFont(size=12),
            progress_color="#A6E3A1",
        )
        if getattr(self.app_config, "enable_noise_suppression", False):
            self.denoiser_switch.select()
        else:
            self.denoiser_switch.deselect()
        self.denoiser_switch.pack(side="left", padx=6)


        # Camera Vision Switch
        self.camera_switch = ctk.CTkSwitch(
            controls_box,
            text="📷 Camera",
            command=self._handle_camera_switch_toggle,
            font=ctk.CTkFont(size=12),
            progress_color="#89B4FA",
        )
        if self.is_camera_active:
            self.camera_switch.select()
        self.camera_switch.pack(side="left", padx=6)


        # Session Start/Stop Button
        self.session_button = ctk.CTkButton(
            controls_box,
            text="▶ Start Live Session",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            width=145,
            height=34,
            command=self._toggle_session,
        )
        self.session_button.pack(side="left", padx=5)
        self._update_session_button_state()

        # Main Split Grid
        self.main_grid = ctk.CTkFrame(self, fg_color="transparent")
        self.main_grid.pack(fill="both", expand=True, padx=15, pady=5)

        self.main_grid.columnconfigure(0, weight=4)
        self.main_grid.columnconfigure(1, weight=6)
        self.main_grid.rowconfigure(0, weight=1)

        # Left Panel: Vision & Interruption Controls
        self.left_panel = ctk.CTkScrollableFrame(
            self.main_grid, corner_radius=10, fg_color="#181825"
        )
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)

        # --- Section 1: Real-Time Camera & Vision Card ---
        vision_card = ctk.CTkFrame(self.left_panel, fg_color="#1E1E2E", corner_radius=8)
        vision_card.pack(fill="x", padx=6, pady=(6, 12))

        v_head = ctk.CTkFrame(vision_card, fg_color="transparent")
        v_head.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            v_head,
            text="📷 Camera & AI Vision",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#89B4FA",
        ).pack(side="left")

        self.vision_badge = ctk.CTkLabel(
            v_head,
            text="🔴 CAMERA OFF",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#F38BA8",
            fg_color="#11111B",
            corner_radius=6,
            padx=8,
            pady=2,
        )
        self.vision_badge.pack(side="right")

        # Camera Device Selector & Action Buttons
        v_ctrls = ctk.CTkFrame(vision_card, fg_color="transparent")
        v_ctrls.pack(fill="x", padx=12, pady=(4, 10))

        # Device selector
        devices = list_camera_devices()
        dev_names = (
            [f"Index {d['index']}: {d['name']}" for d in devices]
            if devices
            else ["Camera 0 (Default)"]
        )
        self.device_var = ctk.StringVar(value=dev_names[0])
        self.device_menu = ctk.CTkOptionMenu(
            v_ctrls,
            values=dev_names,
            variable=self.device_var,
            command=self._handle_device_select,
            font=ctk.CTkFont(size=11),
            height=28,
            fg_color="#313244",
            button_color="#45475A",
        )
        self.device_menu.pack(fill="x", pady=(0, 6))

        # Toggle Button and Snapshot Button row
        btn_row = ctk.CTkFrame(v_ctrls, fg_color="transparent")
        btn_row.pack(fill="x")
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.camera_btn = ctk.CTkButton(
            btn_row,
            text="📷 Start Camera",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            height=32,
            command=self._handle_camera_button_click,
        )
        self.camera_btn.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        self.snapshot_btn = ctk.CTkButton(
            btn_row,
            text="📸 Look Now",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#FAB387",
            hover_color="#F5C2E7",
            text_color="#11111B",
            height=32,
            command=self._handle_snapshot_click,
        )
        self.snapshot_btn.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        self._update_camera_ui_state()

        # --- Section 2: Physical Event Interruptions ---
        left_header = ctk.CTkLabel(
            self.left_panel,
            text="⚡ Physical Event Interruptions",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#FAB387",
        )
        left_header.pack(anchor="w", padx=10, pady=(6, 2))

        left_desc = ctk.CTkLabel(
            self.left_panel,
            text="Forces Gemini Live to react instantly to external physical events.",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
            justify="left",
        )
        left_desc.pack(anchor="w", padx=10, pady=(0, 8))

        # Event Preset Buttons Grid
        self.preset_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.preset_frame.pack(fill="x", padx=6, pady=2)

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
                height=38,
                command=lambda p=payload: self.on_send_interruption(p),
            )
            btn.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
            self.preset_frame.columnconfigure(col, weight=1)

        # Custom Event Input Box
        custom_box = ctk.CTkFrame(self.left_panel, fg_color="#1E1E2E", corner_radius=8)
        custom_box.pack(fill="x", padx=6, pady=(12, 10))

        ctk.CTkLabel(
            custom_box,
            text="Custom Event Text:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CDD6F4",
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.custom_entry = ctk.CTkEntry(
            custom_box,
            placeholder_text="e.g. [HUMAN POKED YOUR SENSOR]",
            font=ctk.CTkFont(size=12),
            height=32,
        )
        self.custom_entry.pack(fill="x", padx=10, pady=3)
        self.custom_entry.bind("<Return>", lambda event: self._send_custom_interruption())

        self.send_custom_btn = ctk.CTkButton(
            custom_box,
            text="⚡ Inject Custom Interruption",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            height=32,
            command=self._send_custom_interruption,
        )
        self.send_custom_btn.pack(fill="x", padx=10, pady=(3, 8))

        # Right Panel: Tabs for Video Preview, Transcript, Logs, Conversations
        self.right_panel = ctk.CTkTabview(
            self.main_grid, corner_radius=10, fg_color="#181825"
        )
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)

        # Tab 1: Live Video Feed
        self.tab_video = self.right_panel.add("👁️ Camera Vision")
        self._build_video_preview_tab()

        # Tab 2: Live Transcript
        self.tab_transcript = self.right_panel.add("💬 Live Transcript")
        self.transcript_box = ctk.CTkTextbox(
            self.tab_transcript,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            fg_color="#1E1E2E",
            text_color="#CDD6F4",
        )
        self.transcript_box.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab 3: System Logs
        self.tab_logs = self.right_panel.add("📜 System Logs")
        self.log_box = ctk.CTkTextbox(
            self.tab_logs,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            fg_color="#1E1E2E",
            text_color="#A6ADC8",
        )
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab 4: Saved Conversations
        self.tab_convos = self.right_panel.add("🗂️ Conversations")
        self._build_conversations_tab()

        # Bottom Status Bar
        self.status_bar = ctk.CTkFrame(self, height=26, fg_color="#11111B")
        self.status_bar.pack(fill="x", side="bottom")

        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="Status: Idle (Disconnected)",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
        )
        self.status_label.pack(side="left", padx=15, pady=2)

    def _build_video_preview_tab(self) -> None:
        """Create real-time video preview viewport in the right panel."""
        preview_container = ctk.CTkFrame(self.tab_video, fg_color="transparent")
        preview_container.pack(fill="both", expand=True, padx=8, pady=8)

        # Viewport Frame
        self.video_viewport = ctk.CTkFrame(
            preview_container, fg_color="#11111B", corner_radius=10
        )
        self.video_viewport.pack(fill="both", expand=True)

        # Video Label displaying Pillow / Tk PhotoImage
        self.video_label = ctk.CTkLabel(
            self.video_viewport,
            text="📷 Camera Feed Offline\n\nClick '📷 Start Camera' to activate real-time AI vision streaming.",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#6C7086",
        )
        self.video_label.pack(fill="both", expand=True, padx=10, pady=10)

        # Bottom Telemetry Bar in Viewport
        self.video_footer = ctk.CTkFrame(self.tab_video, height=28, fg_color="#1E1E2E", corner_radius=6)
        self.video_footer.pack(fill="x", padx=8, pady=(4, 0))

        preview_fps = getattr(self.app_config, "camera_preview_fps", 10)
        live_fps = getattr(self.app_config, "camera_live_fps", 1.0)
        self.video_info_label = ctk.CTkLabel(
            self.video_footer,
            text=f"⚡ Stream Rate: {live_fps:.1f} FPS to Gemini Live | Local Preview: ~{preview_fps} FPS",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
        )
        self.video_info_label.pack(side="left", padx=10, pady=2)

        self.video_snap_btn = ctk.CTkButton(
            self.video_footer,
            text="📸 Quick Snapshot",
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            height=22,
            width=110,
            command=self._handle_snapshot_click,
        )
        self.video_snap_btn.pack(side="right", padx=6, pady=2)

    # ------------------------------------------------------------------
    # Camera preview & event handlers
    # ------------------------------------------------------------------

    def _on_camera_frame_captured(self, pil_img: Image.Image) -> None:
        """Callback from CameraService worker thread delivering fresh frame."""
        with self._preview_lock:
            self._latest_pil_frame = pil_img

    def _render_preview_loop(self) -> None:
        """
        Periodic Tk mainloop task refreshing the video preview widget.
        Optimized with tab-visibility gating and fast OpenCV hardware-accelerated scaling.
        """
        preview_fps = getattr(self.app_config, "camera_preview_fps", 10)
        loop_interval_ms = max(40, int(1000.0 / max(1, preview_fps)))

        try:
            if self.winfo_exists():
                is_active = (
                    self.camera_service.is_running if self.camera_service else False
                )

                # Tab-visibility check: only render when Camera Vision tab is active
                current_tab = ""
                try:
                    current_tab = self.right_panel.get()
                except Exception:
                    pass

                tab_is_camera = (current_tab == "👁️ Camera Vision")

                if is_active:
                    if tab_is_camera:
                        # Determine viewport size dynamically
                        vp_w = max(160, self.video_viewport.winfo_width() - 20)
                        vp_h = max(120, self.video_viewport.winfo_height() - 20)

                        aspect = (
                            self.app_config.camera_width
                            / max(1, self.app_config.camera_height)
                        )
                        target_w = vp_w
                        target_h = int(target_w / aspect)
                        if target_h > vp_h:
                            target_h = vp_h
                            target_w = int(target_h * aspect)

                        # Fetch pre-scaled frame using fast OpenCV SIMD scaling
                        frame = None
                        if self.camera_service:
                            frame = self.camera_service.get_latest_frame_pil(
                                target_size=(max(10, target_w), max(10, target_h))
                            )
                        if frame is None:
                            with self._preview_lock:
                                frame = self._latest_pil_frame
                            if frame is not None and frame.size != (target_w, target_h):
                                frame = frame.resize(
                                    (max(10, target_w), max(10, target_h)),
                                    Image.Resampling.BILINEAR,
                                )

                        if frame is not None:
                            self._preview_ctk_img = ctk.CTkImage(
                                light_image=frame,
                                dark_image=frame,
                                size=(max(10, target_w), max(10, target_h)),
                            )
                            self.video_label.configure(
                                image=self._preview_ctk_img,
                                text="",
                            )
                else:
                    if self.video_label.cget("text") == "":
                        self.video_label.configure(
                            image="",
                            text="📷 Camera Feed Offline\n\nClick '📷 Start Camera' to activate real-time AI vision streaming.",
                        )
        except Exception as e:
            logger.debug("Preview render error: %s", e)
        finally:
            if not getattr(self, "_is_destroyed", False):
                try:
                    if self.winfo_exists():
                        self._preview_after_id = self.after(
                            loop_interval_ms, self._render_preview_loop
                        )
                except Exception:
                    pass

    def destroy(self) -> None:
        """Unregister preview listener and cancel timer on widget teardown."""
        self._is_destroyed = True
        if hasattr(self, "_preview_after_id") and self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self.camera_service:
            try:
                self.camera_service.remove_preview_listener(
                    self._on_camera_frame_captured
                )
            except Exception:
                pass
        super().destroy()

    def _handle_mute_toggle(self) -> None:
        """Handler for Mute Mic toggle switch."""
        is_muted = bool(self.mute_switch.get())
        if self.on_toggle_mute:
            self.on_toggle_mute(is_muted)

    def _handle_denoiser_toggle(self) -> None:
        """Handler for Noise Suppression toggle switch (persists for all new sessions)."""
        enabled = bool(self.denoiser_switch.get())
        self.app_config.enable_noise_suppression = enabled
        try:
            from src.services.audio_test_service import get_audio_test_service
            get_audio_test_service().set_denoiser_enabled(enabled)
        except Exception:
            pass
        logger.info("LivePage: Noise suppression for upcoming sessions set to %s", enabled)

    def _handle_camera_switch_toggle(self) -> None:

        """Handler for Camera toggle switch in top header bar."""
        desired_state = bool(self.camera_switch.get())
        if self.on_toggle_camera:
            actual_state = self.on_toggle_camera(desired_state)
            self.set_vision_state(actual_state)
        elif self.camera_service:
            if desired_state:
                self.camera_service.start()
            else:
                self.camera_service.stop()
            self.set_vision_state(self.camera_service.is_running)

    def _handle_camera_button_click(self) -> None:
        """Handler for Start/Stop Camera button in left panel card."""
        current_state = (
            self.camera_service.is_running if self.camera_service else False
        )
        desired_state = not current_state
        if self.on_toggle_camera:
            actual_state = self.on_toggle_camera(desired_state)
            self.set_vision_state(actual_state)
        elif self.camera_service:
            if desired_state:
                self.camera_service.start()
            else:
                self.camera_service.stop()
            self.set_vision_state(self.camera_service.is_running)

    def _handle_device_select(self, choice: str) -> None:
        """Handler for camera device selection dropdown."""
        try:
            # Parse index from string like 'Index 0: Camera 0 (640x480)' or 'Camera 0'
            idx = 0
            if "Index " in choice:
                idx = int(choice.split("Index ")[1].split(":")[0].strip())
            elif choice.startswith("Camera "):
                idx = int(choice.split("Camera ")[1].split()[0].strip())
            if self.on_set_camera_device:
                self.on_set_camera_device(idx)
            elif self.camera_service:
                self.camera_service.set_device(idx)
        except Exception as e:
            logger.warning("Error switching camera device: %s", e)

    def _handle_snapshot_click(self) -> None:
        """Handler for '📸 Look Now' snapshot inquiry button."""
        prompt = "[CAMERA SNAPSHOT]: What is in front of the camera right now? Look closely and describe."
        if self.on_send_snapshot:
            self.on_send_snapshot(prompt)
        elif self.camera_service:
            self.append_log("📸 Captured local snapshot.")

    def set_vision_state(self, is_active: bool) -> None:
        """Thread-safe update to vision UI controls and badge."""
        self.is_camera_active = is_active
        self._update_camera_ui_state()

    def _update_camera_ui_state(self) -> None:
        """Synchronize button labels, colors, and badge with camera state."""
        try:
            if hasattr(self, "camera_switch") and self.camera_switch.winfo_exists():
                if self.is_camera_active:
                    self.camera_switch.select()
                else:
                    self.camera_switch.deselect()

            if hasattr(self, "camera_btn") and self.camera_btn.winfo_exists():
                if self.is_camera_active:
                    self.camera_btn.configure(
                        text="⏹ Stop Camera",
                        fg_color="#F38BA8",
                        hover_color="#E78284",
                        text_color="#11111B",
                    )
                else:
                    self.camera_btn.configure(
                        text="📷 Start Camera",
                        fg_color="#89B4FA",
                        hover_color="#B4BEFE",
                        text_color="#11111B",
                    )

            if hasattr(self, "vision_badge") and self.vision_badge.winfo_exists():
                if self.is_camera_active:
                    if self.is_session_active:
                        self.vision_badge.configure(
                            text="🟢 STREAMING (1 FPS)",
                            text_color="#A6E3A1",
                        )
                    else:
                        self.vision_badge.configure(
                            text="⚪ PREVIEW ONLY",
                            text_color="#BAC2DE",
                        )
                else:
                    self.vision_badge.configure(
                        text="🔴 CAMERA OFF",
                        text_color="#F38BA8",
                    )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live updates (called from the controller / async side)
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
                self._update_camera_ui_state()
        except Exception:
            pass

    def update_mic_level(self, level: float) -> None:
        """Thread-safe update to mic volume meter."""
        try:
            if self.winfo_exists() and hasattr(self, "mic_meter") and self.mic_meter.winfo_exists():
                self.mic_meter.set(level)
        except Exception:
            pass

    def append_transcript(self, role: str, text: str) -> None:
        """Thread-safe append to transcript text box."""
        try:
            if self.winfo_exists() and hasattr(self, "transcript_box") and self.transcript_box.winfo_exists():
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                formatted = f"[{timestamp}] {role}: {text}\n"
                self.transcript_box.insert("end", formatted)
                self.transcript_box.see("end")
        except Exception:
            pass

    def append_log(self, message: str) -> None:
        """Thread-safe append to system log box."""
        self.append_logs([message])

    def append_logs(self, messages: list[str]) -> None:
        """Append and trim a log batch with one text-widget mutation."""
        if not messages:
            return
        try:
            if self.winfo_exists() and hasattr(self, "log_box") and self.log_box.winfo_exists():
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                chunk = "".join(f"[{timestamp}] {message}\n" for message in messages)
                self.log_box.insert("end", chunk)
                line_count = int(self.log_box.index("end-1c").split(".")[0])
                if line_count > 600:
                    self.log_box.delete("1.0", f"{line_count - 450}.0")
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
            text_color="#BAC2DE",
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
            command=self.refresh_conversations,
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
            text_color="#CDD6F4",
        )
        self.convo_detail_box.grid(row=0, column=1, sticky="nsew")

        self.refresh_conversations()

    def refresh_conversations(self) -> None:
        """Reload the conversation list from the database."""
        try:
            conversations = list_conversations(
                source=ConversationSource.GEMINI,
                limit=100,
            )
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
                command=lambda cid=conv.id: self._show_conversation(cid),
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
            + "=" * 60
            + "\n",
        )
        for msg in messages:
            self.convo_detail_box.insert(
                "end", f"[{msg.role.value}] {msg.content}\n\n"
            )
        self.convo_detail_box.see("1.0")

    # ------------------------------------------------------------------
    # Session controls
    # ------------------------------------------------------------------

    def _update_session_button_state(self) -> None:
        """Update button appearance based on session state."""
        if self.is_session_active:
            self.session_button.configure(
                text="⏹ Stop Session",
                fg_color="#F38BA8",
                hover_color="#E78284",
                text_color="#11111B",
            )
        else:
            self.session_button.configure(
                text="▶ Start Live Session",
                fg_color="#A6E3A1",
                hover_color="#94E2D5",
                text_color="#11111B",
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
