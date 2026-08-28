"""
Wheels & Motor Control page — embedded in the DeveloperWindow shell.

Provides interactive mecanum D-pad, rotation, speed tuning, individual
motor testing/diagnostics, live cliff sensor telemetry, and serial terminal
for the Raspberry Pi 5 to ESP32-S3 UART connection.
"""

import logging
from typing import Optional

import customtkinter as ctk

from src.services.wheels_service import TelemetryData, WheelsService, get_wheels_service

logger = logging.getLogger(__name__)

# Colors matching Catppuccin Mocha theme used across the Dev Console
COLOR_BASE = "#11111B"
COLOR_MANTLE = "#181825"
COLOR_SURFACE0 = "#313244"
COLOR_SURFACE1 = "#45475A"
COLOR_SURFACE2 = "#585B70"
COLOR_TEXT = "#CDD6F4"
COLOR_SUBTEXT = "#BAC2DE"
COLOR_ACCENT = "#FAB387"      # Peach
COLOR_SUCCESS = "#A6E3A1"     # Green
COLOR_DANGER = "#F38BA8"      # Red
COLOR_WARNING = "#F9E2AF"     # Yellow
COLOR_PRIMARY = "#89B4FA"     # Blue
COLOR_PURPLE = "#CBA6F7"      # Mauve


class WheelsPage(ctk.CTkFrame):
    """Developer console tab for Mecanum wheel control and telemetry."""

    def __init__(self, master, wheels_service: Optional[WheelsService] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.service = wheels_service or get_wheels_service()

        # Ensure service callbacks point here even if injected
        self.service.on_telemetry = self._on_telemetry_received
        self.service.on_log = self._on_log_received
        self.service.on_connection_change = self._on_connection_changed

        self._active_button: Optional[ctk.CTkButton] = None
        self._current_speed: int = 180
        self._pulse_duration_ms: int = 250
        self._control_mode: str = "hold"  # "hold" or "pulse"
        self._pending_logs: List[str] = []
        self._log_flush_scheduled: bool = False

        self._create_layout()
        self._bind_keyboard_events()
        self._refresh_port_list()

    def _create_layout(self) -> None:
        """Create header, connection bar, and two-column main workspace."""
        # Top Header & Connection Bar
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        header.pack(fill="x", padx=15, pady=(12, 6))

        # Title Section
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=14, pady=8)

        ctk.CTkLabel(
            title_box,
            text="🛞 Wheels & Motor Control",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="ESP32-S3 UART · Mecanum 4-Wheel Kinematics & Cliff Safety",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        ).pack(anchor="w")

        # Connection Controls Section
        conn_box = ctk.CTkFrame(header, fg_color="transparent")
        conn_box.pack(side="right", padx=14, pady=8)

        # Port Picker
        ctk.CTkLabel(
            conn_box, text="Port:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(0, 4))

        self.port_combo = ctk.CTkComboBox(
            conn_box,
            values=["/dev/serial0", "/dev/ttyAMA0", "COM3", "MOCK_SIMULATOR"],
            width=150,
            height=30,
            font=ctk.CTkFont(size=11),
        )
        self.port_combo.set(self.service.port)
        self.port_combo.pack(side="left", padx=(0, 6))

        # Refresh Ports Button
        ctk.CTkButton(
            conn_box,
            text="🔄",
            width=32,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self._refresh_port_list,
        ).pack(side="left", padx=(0, 6))

        # Connect / Disconnect Toggle Button
        self.btn_connect = ctk.CTkButton(
            conn_box,
            text="Connect",
            width=90,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLOR_SUCCESS,
            hover_color="#94E2D5",
            text_color="#11111B",
            command=self._toggle_connection,
        )
        self.btn_connect.pack(side="left", padx=(0, 8))

        # Status Indicator Badge
        self.status_badge = ctk.CTkLabel(
            conn_box,
            text="🔴 Disconnected",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_DANGER,
        )
        self.status_badge.pack(side="left")

        # Main Split Content Area
        main_content = ctk.CTkFrame(self, fg_color="transparent")
        main_content.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        main_content.columnconfigure(0, weight=5)  # Left column (D-pad & Speed)
        main_content.columnconfigure(1, weight=6)  # Right column (Motors, Telemetry & Logs)
        main_content.rowconfigure(0, weight=1)

        # ---------------- Left Panel: D-Pad, Modes, Speed ----------------
        left_panel = ctk.CTkScrollableFrame(
            main_content, fg_color="#1E1E2E", corner_radius=10
        )
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)

        self._build_dpad_section(left_panel)
        self._build_speed_and_mode_section(left_panel)

        # ---------------- Right Panel: Diagnostics, Telemetry, Terminal ----------------
        right_panel = ctk.CTkScrollableFrame(
            main_content, fg_color="#1E1E2E", corner_radius=10
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        self._build_telemetry_section(right_panel)
        self._build_diagnostics_section(right_panel)
        self._build_terminal_section(right_panel)

    # ------------------------------------------------------------------
    # UI Section Builders
    # ------------------------------------------------------------------

    def _build_dpad_section(self, parent: ctk.CTkFrame) -> None:
        """Omnidirectional 8-way Mecanum D-pad and rotation buttons."""
        section_label = ctk.CTkLabel(
            parent,
            text="🎮 Movement Controls",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        )
        section_label.pack(anchor="w", padx=12, pady=(10, 8))

        # D-pad grid container
        dpad_frame = ctk.CTkFrame(parent, fg_color="transparent")
        dpad_frame.pack(padx=12, pady=4)

        for col in range(3):
            dpad_frame.columnconfigure(col, weight=1, minsize=75)
        for row in range(3):
            dpad_frame.rowconfigure(row, weight=1, minsize=60)

        # Button specs: (row, col, label, command, color, hover)
        buttons = [
            (0, 0, "↖", "forwardLeft", COLOR_SURFACE0, COLOR_SURFACE1),
            (0, 1, "↑", "forward", COLOR_PRIMARY, "#B4BEFE"),
            (0, 2, "↗", "forwardRight", COLOR_SURFACE0, COLOR_SURFACE1),
            (1, 0, "←", "strafeLeft", COLOR_PRIMARY, "#B4BEFE"),
            (1, 1, "STOP", "stop", COLOR_DANGER, "#EBA0AC"),
            (1, 2, "→", "strafeRight", COLOR_PRIMARY, "#B4BEFE"),
            (2, 0, "↙", "backwardLeft", COLOR_SURFACE0, COLOR_SURFACE1),
            (2, 1, "↓", "backward", COLOR_PRIMARY, "#B4BEFE"),
            (2, 2, "↘", "backwardRight", COLOR_SURFACE0, COLOR_SURFACE1),
        ]

        for r, c, label, cmd, fg, hover in buttons:
            txt_color = "#11111B" if fg in (COLOR_PRIMARY, COLOR_DANGER) else COLOR_TEXT
            btn = ctk.CTkButton(
                dpad_frame,
                text=label,
                font=ctk.CTkFont(size=18, weight="bold"),
                width=75,
                height=56,
                corner_radius=10,
                fg_color=fg,
                hover_color=hover,
                text_color=txt_color,
            )
            btn.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")

            if cmd == "stop":
                btn.configure(command=self._on_stop_clicked)
            else:
                self._attach_movement_events(btn, cmd)

        # Rotation Row
        rotate_frame = ctk.CTkFrame(parent, fg_color="transparent")
        rotate_frame.pack(fill="x", padx=12, pady=(8, 4))
        rotate_frame.columnconfigure(0, weight=1)
        rotate_frame.columnconfigure(1, weight=1)

        btn_rot_left = ctk.CTkButton(
            rotate_frame,
            text="↺ Rotate Left (Q)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=42,
            corner_radius=8,
            fg_color=COLOR_PURPLE,
            hover_color="#DDB6F2",
            text_color="#11111B",
        )
        btn_rot_left.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self._attach_movement_events(btn_rot_left, "rotateLeft")

        btn_rot_right = ctk.CTkButton(
            rotate_frame,
            text="Rotate Right (E) ↻",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=42,
            corner_radius=8,
            fg_color=COLOR_PURPLE,
            hover_color="#DDB6F2",
            text_color="#11111B",
        )
        btn_rot_right.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        self._attach_movement_events(btn_rot_right, "rotateRight")

    def _build_speed_and_mode_section(self, parent: ctk.CTkFrame) -> None:
        """Control mode toggle, pulse duration, and speed tuning sliders."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            parent,
            text="⚙️ Drive Settings",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        # Mode Selection
        mode_frame = ctk.CTkFrame(parent, fg_color="transparent")
        mode_frame.pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            mode_frame,
            text="Mode:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_SUBTEXT,
        ).pack(side="left", padx=(0, 8))

        self.mode_segment = ctk.CTkSegmentedButton(
            mode_frame,
            values=["Hold to Move", "Step Pulse"],
            font=ctk.CTkFont(size=11),
            fg_color=COLOR_SURFACE0,
            selected_color=COLOR_PRIMARY,
            selected_hover_color="#B4BEFE",
            command=self._on_mode_changed,
        )
        self.mode_segment.set("Hold to Move")
        self.mode_segment.pack(side="left", fill="x", expand=True)

        # Pulse Duration Slider (visible when Step Pulse selected)
        self.pulse_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.pulse_frame.pack(fill="x", padx=12, pady=(0, 8))

        self.pulse_label = ctk.CTkLabel(
            self.pulse_frame,
            text="Step Duration: 250 ms",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        )
        self.pulse_label.pack(anchor="w")

        self.pulse_slider = ctk.CTkSlider(
            self.pulse_frame,
            from_=100,
            to=1000,
            number_of_steps=18,
            command=self._on_pulse_slider_changed,
        )
        self.pulse_slider.set(250)
        self.pulse_slider.pack(fill="x", pady=(2, 0))

        # Speed Slider
        speed_box = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        speed_box.pack(fill="x", padx=12, pady=(4, 8))

        speed_header = ctk.CTkFrame(speed_box, fg_color="transparent")
        speed_header.pack(fill="x", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            speed_header,
            text="Motor Speed (PWM):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left")

        self.speed_value_label = ctk.CTkLabel(
            speed_header,
            text=f"{self._current_speed} / 255",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_ACCENT,
        )
        self.speed_value_label.pack(side="right")

        self.speed_slider = ctk.CTkSlider(
            speed_box,
            from_=70,
            to=255,
            number_of_steps=37,
            command=self._on_speed_slider_changed,
        )
        self.speed_slider.set(self._current_speed)
        self.speed_slider.pack(fill="x", padx=10, pady=(2, 6))

        # Speed Preset Buttons
        preset_frame = ctk.CTkFrame(speed_box, fg_color="transparent")
        preset_frame.pack(fill="x", padx=8, pady=(0, 8))
        presets = [("Slow (100)", 100), ("Normal (180)", 180), ("Fast (220)", 220), ("Max (255)", 255)]
        for label, val in presets:
            ctk.CTkButton(
                preset_frame,
                text=label,
                font=ctk.CTkFont(size=10),
                height=26,
                fg_color=COLOR_SURFACE0,
                hover_color=COLOR_SURFACE1,
                command=lambda v=val: self._set_speed_preset(v),
            ).pack(side="left", fill="x", expand=True, padx=2)

        # Keyboard Navigation Help Hint
        help_box = ctk.CTkFrame(parent, fg_color="transparent")
        help_box.pack(fill="x", padx=12, pady=(4, 10))
        ctk.CTkLabel(
            help_box,
            text="💡 Hotkeys: W/A/S/D = Move  ·  Q/E = Rotate  ·  Space = Stop",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        ).pack(anchor="w")

    def _build_telemetry_section(self, parent: ctk.CTkFrame) -> None:
        """Cliff sensor distances, cliff alert badges, and motion status."""
        ctk.CTkLabel(
            parent,
            text="📡 Live Telemetry & Cliff Sensors",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(10, 6))

        telem_grid = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        telem_grid.pack(fill="x", padx=12, pady=(0, 10))
        telem_grid.columnconfigure(0, weight=1)
        telem_grid.columnconfigure(1, weight=1)

        # Front Sensor Box
        front_box = ctk.CTkFrame(telem_grid, fg_color="transparent")
        front_box.grid(row=0, column=0, padx=10, pady=8, sticky="nsew")

        ctk.CTkLabel(
            front_box,
            text="Front Sensor (VL53L0X):",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        ).pack(anchor="w")

        self.front_dist_label = ctk.CTkLabel(
            front_box,
            text="— mm",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_PRIMARY,
        )
        self.front_dist_label.pack(anchor="w", pady=(2, 2))

        self.front_cliff_badge = ctk.CTkLabel(
            front_box,
            text="Safe",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_SUCCESS,
        )
        self.front_cliff_badge.pack(anchor="w")

        # Back Sensor Box
        back_box = ctk.CTkFrame(telem_grid, fg_color="transparent")
        back_box.grid(row=0, column=1, padx=10, pady=8, sticky="nsew")

        ctk.CTkLabel(
            back_box,
            text="Back Sensor (VL53L0X):",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        ).pack(anchor="w")

        self.back_dist_label = ctk.CTkLabel(
            back_box,
            text="— mm",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_PRIMARY,
        )
        self.back_dist_label.pack(anchor="w", pady=(2, 2))

        self.back_cliff_badge = ctk.CTkLabel(
            back_box,
            text="Safe",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_SUCCESS,
        )
        self.back_cliff_badge.pack(anchor="w")

        # Motion & Speed status footer
        footer = ctk.CTkFrame(telem_grid, fg_color="transparent")
        footer.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")

        self.motion_label = ctk.CTkLabel(
            footer,
            text="State: STOPPED",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_WARNING,
        )
        self.motion_label.pack(side="left")

        self.battery_label = ctk.CTkLabel(
            footer,
            text="🔋 Battery: —",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_PRIMARY,
        )
        self.battery_label.pack(side="left", padx=(16, 0))

        ctk.CTkButton(
            footer,
            text="⚡ Sim Charge",
            width=85,
            height=24,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#A6E3A1",
            text_color="#11111B",
            hover_color="#94E2D5",
            command=self._toggle_sim_charge,
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            footer,
            text="Ping",
            width=50,
            height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self.service.send_ping,
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            footer,
            text="Status",
            width=50,
            height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self.service.request_status,
        ).pack(side="right")

    def _build_diagnostics_section(self, parent: ctk.CTkFrame) -> None:
        """Individual 4-motor testing matrix for wiring & calibration verification."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkLabel(
            parent,
            text="🔧 Individual Motor Diagnostics",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        diag_box = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        diag_box.pack(fill="x", padx=12, pady=(0, 10))

        motors = [
            ("Front-Left (FL)", "fl"),
            ("Front-Right (FR)", "fr"),
            ("Back-Left (BL)", "bl"),
            ("Back-Right (BR)", "br"),
        ]

        for idx, (name, code) in enumerate(motors):
            row_frame = ctk.CTkFrame(diag_box, fg_color="transparent")
            row_frame.pack(fill="x", padx=10, pady=3)

            ctk.CTkLabel(
                row_frame,
                text=name,
                width=130,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLOR_TEXT,
                anchor="w",
            ).pack(side="left")

            # Forward Pulse
            ctk.CTkButton(
                row_frame,
                text="▲ Fwd",
                width=65,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color=COLOR_SURFACE0,
                hover_color=COLOR_SURFACE1,
                command=lambda m=code: self._test_single_motor(m, 1),
            ).pack(side="left", padx=4)

            # Reverse Pulse
            ctk.CTkButton(
                row_frame,
                text="▼ Rev",
                width=65,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color=COLOR_SURFACE0,
                hover_color=COLOR_SURFACE1,
                command=lambda m=code: self._test_single_motor(m, -1),
            ).pack(side="left", padx=4)

            # Stop Motor
            ctk.CTkButton(
                row_frame,
                text="Stop",
                width=50,
                height=26,
                font=ctk.CTkFont(size=10),
                fg_color=COLOR_SURFACE1,
                hover_color=COLOR_SURFACE2,
                command=lambda m=code: self._test_single_motor(m, 0),
            ).pack(side="left", padx=4)

    def _build_terminal_section(self, parent: ctk.CTkFrame) -> None:
        """Serial terminal output viewer and manual command input."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=(0, 10))

        term_header = ctk.CTkFrame(parent, fg_color="transparent")
        term_header.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(
            term_header,
            text="💻 Serial Terminal & Logs",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(side="left")

        ctk.CTkButton(
            term_header,
            text="Clear",
            width=50,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self._clear_terminal,
        ).pack(side="right")

        self.terminal_box = ctk.CTkTextbox(
            parent,
            height=130,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLOR_BASE,
            text_color="#A6ADC8",
            corner_radius=8,
        )
        self.terminal_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # Command entry
        cmd_frame = ctk.CTkFrame(parent, fg_color="transparent")
        cmd_frame.pack(fill="x", padx=12, pady=(0, 10))

        self.cmd_entry = ctk.CTkEntry(
            cmd_frame,
            placeholder_text="Enter raw serial command (e.g. CMD:forward, SPEED:200, PING)...",
            font=ctk.CTkFont(family="Consolas", size=11),
            height=30,
        )
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.cmd_entry.bind("<Return>", lambda e: self._send_custom_command())

        ctk.CTkButton(
            cmd_frame,
            text="Send",
            width=60,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color="#B4BEFE",
            text_color="#11111B",
            command=self._send_custom_command,
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Movement Event Handlers & Binding
    # ------------------------------------------------------------------

    def _attach_movement_events(self, button: ctk.CTkButton, command: str) -> None:
        """Attach pointer down / up / click handlers for continuous vs pulse modes."""
        def _restore_style():
            if command in ("forward", "backward", "strafeLeft", "strafeRight"):
                button.configure(fg_color=COLOR_PRIMARY, text_color="#11111B")
            elif command in ("rotateLeft", "rotateRight"):
                button.configure(fg_color=COLOR_PURPLE, text_color="#11111B")
            else:
                button.configure(fg_color=COLOR_SURFACE0, text_color=COLOR_TEXT)

        def _on_press(event=None):
            if self._control_mode == "hold":
                self._active_button = button
                button.configure(fg_color=COLOR_ACCENT, text_color="#11111B")
                self.service.start_continuous(command)
            else:
                self.service.pulse(command, self._pulse_duration_ms)

        def _on_release(event=None):
            if self._control_mode == "hold":
                if self._active_button == button:
                    self._active_button = None
                self.service.stop_continuous()
                _restore_style()

        def _on_leave(event=None):
            if self._control_mode == "hold" and self._active_button == button:
                self._active_button = None
                self.service.stop_continuous()
                _restore_style()

        button.bind("<ButtonPress-1>", _on_press)
        button.bind("<ButtonRelease-1>", _on_release)
        button.bind("<Leave>", _on_leave)

    def _on_stop_clicked(self) -> None:
        """Stop button clicked."""
        self.service.stop()
        self.motion_label.configure(text="State: STOPPED", text_color=COLOR_WARNING)

    def _on_mode_changed(self, mode: str) -> None:
        """Switch between Hold to Move and Step Pulse modes."""
        if mode == "Hold to Move":
            self._control_mode = "hold"
        else:
            self._control_mode = "pulse"

    def _on_pulse_slider_changed(self, value: float) -> None:
        self._pulse_duration_ms = int(value)
        self.pulse_label.configure(text=f"Step Duration: {self._pulse_duration_ms} ms")

    def _on_speed_slider_changed(self, value: float) -> None:
        self._current_speed = int(value)
        self.speed_value_label.configure(text=f"{self._current_speed} / 255")
        self.service.set_speed(self._current_speed)

    def _set_speed_preset(self, speed: int) -> None:
        self._current_speed = speed
        self.speed_slider.set(speed)
        self.speed_value_label.configure(text=f"{speed} / 255")
        self.service.set_speed(speed)

    def _test_single_motor(self, motor: str, direction: int) -> None:
        """Send diagnostic motor command with short pulse if direction != 0."""
        if direction == 0:
            self.service.test_motor(motor, 0)
        else:
            self.service.test_motor(motor, direction, self._current_speed)
            # Pulse for 350ms
            self.after(350, lambda: self.service.test_motor(motor, 0))

    def _send_custom_command(self) -> None:
        text = self.cmd_entry.get().strip()
        if text:
            self.service.send_raw(text)
            self.cmd_entry.delete(0, "end")

    # ------------------------------------------------------------------
    # Keyboard Bindings
    # ------------------------------------------------------------------

    def _bind_keyboard_events(self) -> None:
        """Schedule safe keyboard bindings on the toplevel window."""
        self.after(200, self._attach_window_bindings)

    def _attach_window_bindings(self) -> None:
        try:
            top = self.winfo_toplevel()
            if not top:
                return

            bindings = {
                "<w>": "forward",
                "<W>": "forward",
                "<s>": "backward",
                "<S>": "backward",
                "<a>": "strafeLeft",
                "<A>": "strafeLeft",
                "<d>": "strafeRight",
                "<D>": "strafeRight",
                "<q>": "rotateLeft",
                "<Q>": "rotateLeft",
                "<e>": "rotateRight",
                "<E>": "rotateRight",
            }

            for key, cmd in bindings.items():
                top.bind(key, lambda e, c=cmd: self._handle_key_press(c), add="+")

            releases = [
                ("<KeyRelease-w>", "forward"),
                ("<KeyRelease-W>", "forward"),
                ("<KeyRelease-s>", "backward"),
                ("<KeyRelease-S>", "backward"),
                ("<KeyRelease-a>", "strafeLeft"),
                ("<KeyRelease-A>", "strafeLeft"),
                ("<KeyRelease-d>", "strafeRight"),
                ("<KeyRelease-D>", "strafeRight"),
                ("<KeyRelease-q>", "rotateLeft"),
                ("<KeyRelease-Q>", "rotateLeft"),
                ("<KeyRelease-e>", "rotateRight"),
                ("<KeyRelease-E>", "rotateRight"),
            ]
            for key, cmd in releases:
                top.bind(key, lambda e, c=cmd: self._handle_key_release(c), add="+")

            top.bind("<space>", lambda e: self._on_stop_clicked(), add="+")
        except Exception as e:
            logger.warning("Could not bind keyboard shortcuts: %s", e)

    def _handle_key_press(self, command: str) -> None:
        # Ignore if user is currently typing inside an Entry/Textbox widget
        focus_widget = self.focus_get()
        if isinstance(focus_widget, (ctk.CTkEntry, ctk.CTkTextbox)):
            return

        if self._control_mode == "hold":
            self.service.start_continuous(command)
        else:
            self.service.pulse(command, self._pulse_duration_ms)

    def _handle_key_release(self, command: str) -> None:
        focus_widget = self.focus_get()
        if isinstance(focus_widget, (ctk.CTkEntry, ctk.CTkTextbox)):
            return

        if self._control_mode == "hold":
            self.service.stop_continuous()

    # ------------------------------------------------------------------
    # Connection & Port Management
    # ------------------------------------------------------------------

    def _refresh_port_list(self) -> None:
        ports = WheelsService.list_available_ports()
        self.port_combo.configure(values=ports)
        if self.service.port in ports:
            self.port_combo.set(self.service.port)
        elif ports:
            self.port_combo.set(ports[0])

    def _toggle_connection(self) -> None:
        if self.service.is_connected:
            self.service.disconnect()
        else:
            selected_port = self.port_combo.get().strip()
            self.service.connect(port=selected_port)

    def _on_connection_changed(self, connected: bool, info: str) -> None:
        """Callback invoked when connection state changes (thread-safe dispatch)."""
        def _update():
            if not self.winfo_exists():
                return
            if connected:
                self.btn_connect.configure(
                    text="Disconnect",
                    fg_color=COLOR_DANGER,
                    hover_color="#EBA0AC",
                )
                if self.service.is_mock:
                    self.status_badge.configure(
                        text="⚙️ Mock Mode", text_color=COLOR_ACCENT
                    )
                else:
                    self.status_badge.configure(
                        text=f"🟢 Connected", text_color=COLOR_SUCCESS
                    )
            else:
                self.btn_connect.configure(
                    text="Connect",
                    fg_color=COLOR_SUCCESS,
                    hover_color="#94E2D5",
                )
                self.status_badge.configure(
                    text="🔴 Disconnected", text_color=COLOR_DANGER
                )

        self.after(0, _update)

    # ------------------------------------------------------------------
    # Telemetry & Log Callbacks
    # ------------------------------------------------------------------

    def _on_telemetry_received(self, data: TelemetryData) -> None:
        """Update live UI telemetry badges (dispatched on main idle)."""
        def _update():
            if not self.winfo_exists():
                return
            try:
                self.front_dist_label.configure(text=f"{data.front_distance_mm} mm")
                self.back_dist_label.configure(text=f"{data.back_distance_mm} mm")

                if data.front_cliff:
                    self.front_cliff_badge.configure(
                        text="⚠️ CLIFF DETECTED", text_color=COLOR_DANGER
                    )
                else:
                    self.front_cliff_badge.configure(text="Safe", text_color=COLOR_SUCCESS)

                if data.back_cliff:
                    self.back_cliff_badge.configure(
                        text="⚠️ CLIFF DETECTED", text_color=COLOR_DANGER
                    )
                else:
                    self.back_cliff_badge.configure(text="Safe", text_color=COLOR_SUCCESS)

                motion_color = COLOR_SUCCESS if data.motion != "STOPPED" else COLOR_WARNING
                self.motion_label.configure(
                    text=f"State: {data.motion}", text_color=motion_color
                )

                if data.battery_voltage > 0:
                    if data.is_charging:
                        self.battery_label.configure(
                            text=f"⚡ Charging: {data.battery_pct}% ({data.battery_voltage:.2f}V)",
                            text_color="#A6E3A1"
                        )
                    else:
                        batt_color = COLOR_SUCCESS if data.battery_pct >= 50 else (COLOR_WARNING if data.battery_pct >= 20 else COLOR_DANGER)
                        self.battery_label.configure(
                            text=f"🔋 Battery: {data.battery_pct}% ({data.battery_voltage:.2f}V)",
                            text_color=batt_color
                        )
            except Exception:
                pass

        self.after_idle(_update)

    def _toggle_sim_charge(self) -> None:
        """Toggle simulated charging state on WheelsService for testing."""
        curr = getattr(self.service, "_is_charging", False)
        new_state = not curr
        self.service.set_charging_simulation(new_state)
        self.service._emit_log(f"⚡ [Sim] Charging state set to: {new_state}")

    def _on_log_received(self, text: str) -> None:
        """Buffer incoming log line and schedule batched UI flush to prevent GUI freezing."""
        self._pending_logs.append(text)
        if not self._log_flush_scheduled:
            self._log_flush_scheduled = True
            self.after(50, self._flush_pending_logs)

    def _flush_pending_logs(self) -> None:
        """Batch-insert pending log lines into the embedded terminal."""
        self._log_flush_scheduled = False
        if not self.winfo_exists() or not self._pending_logs:
            return

        chunk = "\n".join(self._pending_logs) + "\n"
        self._pending_logs.clear()

        try:
            self.terminal_box.insert("end", chunk)
            # Limit scrollback to ~300 lines to prevent Tkinter canvas rendering lag
            line_count = int(self.terminal_box.index("end-1c").split(".")[0])
            if line_count > 350:
                self.terminal_box.delete("1.0", f"{line_count - 250}.0")
            self.terminal_box.see("end")
        except Exception:
            pass

    def _clear_terminal(self) -> None:
        self._pending_logs.clear()
        self.terminal_box.delete("1.0", "end")
