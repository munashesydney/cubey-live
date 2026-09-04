"""
RPLIDAR C1 360-Degree Radar Visualizer & Diagnostics Page.

Provides real-time polar radar visualization, point cloud rendering with multiple
color palettes, 4-sector proximity collision warnings, zoom & range tuning,
health telemetry, and serial command logs for the Waveshare / Slamtec RPLIDAR C1.
"""

import math
import logging
import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk

from src.services.lidar_service import (
    LidarPoint,
    LidarScanData,
    LidarService,
    get_lidar_service,
)

logger = logging.getLogger(__name__)

# Colors matching Catppuccin Mocha theme used across the Dev Console
COLOR_BASE = "#11111B"
COLOR_MANTLE = "#181825"
COLOR_CRUST = "#1E1E2E"
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
COLOR_SKY = "#89DCEB"         # Sky
COLOR_SAPPHIRE = "#74C7EC"    # Sapphire
COLOR_GRID = "#252739"        # Radar grid rings
COLOR_GRID_TEXT = "#6C7086"   # Range labels


class LidarPage(ctk.CTkFrame):
    """Developer console page for 360° LiDAR radar visualization and testing."""

    def __init__(self, master, lidar_service: Optional[LidarService] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self.service = lidar_service or get_lidar_service()

        # Connect callbacks
        self.service.on_scan_data = self._on_scan_data_received
        self.service.on_log = self._on_log_received
        self.service.on_connection_change = self._on_connection_changed

        # Visualization settings
        self._max_range_m: float = 4.0      # Canvas visible range (meters)
        self._point_size: int = 2            # Point radius in pixels
        self._color_mode: str = "Heatmap"   # "Heatmap", "Safety", "Neon Sky", "Quality"
        self._safety_dist_mm: int = 300     # Threshold for red collision alerts
        self._persistence_mode: str = "Single"  # "Single" or "Trail (3x)"

        # State cache
        self._latest_scan: Optional[LidarScanData] = None
        self._trail_points: List[Tuple[float, List[LidarPoint]]] = []  # (timestamp, points)
        self._hover_info: Optional[str] = None

        # Log buffer
        self._pending_logs: List[str] = []
        self._log_flush_scheduled: bool = False

        # Canvas rendering throttle
        self._render_pending: bool = False
        self._is_active: bool = True

        self._create_layout()
        self._refresh_port_list()

    def _create_layout(self) -> None:
        """Build top header, radar canvas, and diagnostics control column."""
        # Top Header Bar
        header = ctk.CTkFrame(self, corner_radius=10, fg_color=COLOR_CRUST)
        header.pack(fill="x", padx=15, pady=(12, 6))

        # Title & Subtitle
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=14, pady=8)

        ctk.CTkLabel(
            title_box,
            text="📡 RPLIDAR C1 · 360° Radar & 2D Mapping",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Waveshare / Slamtec DToF Laser Scanner · 460,800 Baud · Real-Time Obstacle Detection",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        ).pack(anchor="w")

        # Connection Controls Bar
        conn_box = ctk.CTkFrame(header, fg_color="transparent")
        conn_box.pack(side="right", padx=14, pady=8)

        ctk.CTkLabel(
            conn_box, text="Port:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(0, 4))

        self.port_combo = ctk.CTkComboBox(
            conn_box,
            values=["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/serial0", "COM3", "MOCK_SIMULATOR"],
            width=140,
            height=30,
            font=ctk.CTkFont(size=11),
        )
        self.port_combo.set(self.service.port)
        self.port_combo.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            conn_box, text="Baud:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(4, 4))

        self.baud_combo = ctk.CTkComboBox(
            conn_box,
            values=["460800", "115200", "256000"],
            width=100,
            height=30,
            font=ctk.CTkFont(size=11),
        )
        self.baud_combo.set(str(self.service.baudrate))
        self.baud_combo.pack(side="left", padx=(0, 6))

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
            width=85,
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

        # Main Split Content Workspace
        main_content = ctk.CTkFrame(self, fg_color="transparent")
        main_content.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        main_content.columnconfigure(0, weight=1)  # Left: Radar Canvas & Zoom Toolbar (responsive)
        main_content.columnconfigure(1, weight=0, minsize=420)  # Right: Proximity, Telemetry & Logs (stable)
        main_content.rowconfigure(0, weight=1)

        # ---------------- Left Panel: Polar Radar Canvas ----------------
        left_panel = ctk.CTkFrame(main_content, fg_color=COLOR_CRUST, corner_radius=10)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)
        left_panel.rowconfigure(0, weight=1)
        left_panel.columnconfigure(0, weight=1)

        # Tkinter Canvas for high-speed custom 2D vector drawing
        self.radar_canvas = tk.Canvas(
            left_panel,
            bg=COLOR_BASE,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.radar_canvas.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._canvas_resize_timer = None
        self.radar_canvas.bind("<Configure>", self._on_canvas_configure)
        self.radar_canvas.bind("<Motion>", self._on_canvas_mouse_move)
        self.radar_canvas.bind("<Leave>", self._on_canvas_mouse_leave)

        # Canvas bottom floating toolbar for Range Presets & Hover Info
        canvas_toolbar = ctk.CTkFrame(left_panel, fg_color="transparent")
        canvas_toolbar.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 8))

        # Hover coordinate readout
        self.lbl_hover = ctk.CTkLabel(
            canvas_toolbar,
            text="Hover over point for distance & angle",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        )
        self.lbl_hover.pack(side="left", padx=4)

        # Range scale buttons
        preset_box = ctk.CTkFrame(canvas_toolbar, fg_color="transparent")
        preset_box.pack(side="right")

        ctk.CTkLabel(
            preset_box, text="Range:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(0, 4))

        for r_val, r_label in [(1.5, "1.5m"), (3.0, "3m"), (6.0, "6m"), (12.0, "12m")]:
            ctk.CTkButton(
                preset_box,
                text=r_label,
                width=42,
                height=24,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color=COLOR_SURFACE0 if self._max_range_m != r_val else COLOR_PRIMARY,
                hover_color=COLOR_SURFACE1,
                text_color="#11111B" if self._max_range_m == r_val else COLOR_TEXT,
                command=lambda v=r_val: self._set_range_preset(v),
            ).pack(side="left", padx=2)

        # ---------------- Right Panel: Proximity, Telemetry, Controls & Logs ----------------
        right_panel = ctk.CTkFrame(
            main_content, fg_color=COLOR_CRUST, corner_radius=10
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        self._build_proximity_section(right_panel)
        self._build_controls_section(right_panel)
        self._build_display_settings_section(right_panel)
        self._build_terminal_section(right_panel)

    # ------------------------------------------------------------------
    # UI Section Builders
    # ------------------------------------------------------------------

    def _build_proximity_section(self, parent: ctk.CTkFrame) -> None:
        """4-Sector proximity obstacle detection HUD (Front, Left, Right, Rear)."""
        ctk.CTkLabel(
            parent,
            text="🛡️ 4-Sector Obstacle Proximity",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(10, 6))

        prox_grid = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        prox_grid.pack(fill="x", padx=12, pady=(0, 8))
        prox_grid.columnconfigure(0, weight=1)
        prox_grid.columnconfigure(1, weight=1)

        # Sector cards: Front, Rear, Left, Right
        # Front (0°±30°)
        f_box = ctk.CTkFrame(prox_grid, fg_color="transparent")
        f_box.grid(row=0, column=0, padx=8, pady=6, sticky="nsew")
        ctk.CTkLabel(
            f_box, text="⬆️ Front (0°±30°):", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(anchor="w")
        self.lbl_dist_front = ctk.CTkLabel(
            f_box, text="— mm", font=ctk.CTkFont(size=15, weight="bold"), text_color=COLOR_PRIMARY
        )
        self.lbl_dist_front.pack(anchor="w")
        self.badge_front = ctk.CTkLabel(
            f_box, text="Safe", font=ctk.CTkFont(size=10, weight="bold"), text_color=COLOR_SUCCESS
        )
        self.badge_front.pack(anchor="w")

        # Rear (180°±30°)
        b_box = ctk.CTkFrame(prox_grid, fg_color="transparent")
        b_box.grid(row=0, column=1, padx=8, pady=6, sticky="nsew")
        ctk.CTkLabel(
            b_box, text="⬇️ Rear (180°±30°):", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(anchor="w")
        self.lbl_dist_back = ctk.CTkLabel(
            b_box, text="— mm", font=ctk.CTkFont(size=15, weight="bold"), text_color=COLOR_PRIMARY
        )
        self.lbl_dist_back.pack(anchor="w")
        self.badge_back = ctk.CTkLabel(
            b_box, text="Safe", font=ctk.CTkFont(size=10, weight="bold"), text_color=COLOR_SUCCESS
        )
        self.badge_back.pack(anchor="w")

        # Left (270°±30°)
        l_box = ctk.CTkFrame(prox_grid, fg_color="transparent")
        l_box.grid(row=1, column=0, padx=8, pady=6, sticky="nsew")
        ctk.CTkLabel(
            l_box, text="⬅️ Left (270°±30°):", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(anchor="w")
        self.lbl_dist_left = ctk.CTkLabel(
            l_box, text="— mm", font=ctk.CTkFont(size=15, weight="bold"), text_color=COLOR_PRIMARY
        )
        self.lbl_dist_left.pack(anchor="w")
        self.badge_left = ctk.CTkLabel(
            l_box, text="Safe", font=ctk.CTkFont(size=10, weight="bold"), text_color=COLOR_SUCCESS
        )
        self.badge_left.pack(anchor="w")

        # Right (90°±30°)
        r_box = ctk.CTkFrame(prox_grid, fg_color="transparent")
        r_box.grid(row=1, column=1, padx=8, pady=6, sticky="nsew")
        ctk.CTkLabel(
            r_box, text="➡️ Right (90°±30°):", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(anchor="w")
        self.lbl_dist_right = ctk.CTkLabel(
            r_box, text="— mm", font=ctk.CTkFont(size=15, weight="bold"), text_color=COLOR_PRIMARY
        )
        self.lbl_dist_right.pack(anchor="w")
        self.badge_right = ctk.CTkLabel(
            r_box, text="Safe", font=ctk.CTkFont(size=10, weight="bold"), text_color=COLOR_SUCCESS
        )
        self.badge_right.pack(anchor="w")

        # Closest Obstacle Banner
        self.lbl_closest_obstacle = ctk.CTkLabel(
            prox_grid,
            text="⚠️ Closest Obstacle: None detected",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_TEXT,
        )
        self.lbl_closest_obstacle.grid(row=2, column=0, columnspan=2, padx=10, pady=(2, 8), sticky="w")

    def _build_controls_section(self, parent: ctk.CTkFrame) -> None:
        """LiDAR scan controls, motor toggle, core reset, and telemetry readouts."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=(4, 8))

        ctk.CTkLabel(
            parent,
            text="⚡ Sensor Control & Telemetry",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        ctrl_box = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        ctrl_box.pack(fill="x", padx=12, pady=(0, 8))

        # Action Buttons Row
        btn_row = ctk.CTkFrame(ctrl_box, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=8)

        self.btn_scan = ctk.CTkButton(
            btn_row,
            text="Start Scan",
            width=90,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color="#B4BEFE",
            text_color="#11111B",
            command=self._toggle_scan,
        )
        self.btn_scan.pack(side="left", padx=(0, 6))

        self.btn_health = ctk.CTkButton(
            btn_row,
            text="Health Check",
            width=90,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self.service.get_health,
        )
        self.btn_health.pack(side="left", padx=(0, 6))

        self.btn_reset = ctk.CTkButton(
            btn_row,
            text="Reset Core",
            width=80,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLOR_SURFACE0,
            hover_color=COLOR_SURFACE1,
            command=self.service.reset_core,
        )
        self.btn_reset.pack(side="left")

        # Telemetry Stats Grid
        stats_frame = ctk.CTkFrame(ctrl_box, fg_color="transparent")
        stats_frame.pack(fill="x", padx=10, pady=(0, 8))
        stats_frame.columnconfigure(0, weight=1)
        stats_frame.columnconfigure(1, weight=1)

        self.lbl_fps = ctk.CTkLabel(
            stats_frame,
            text="Scan Rate: 0.0 Hz",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_ACCENT,
            anchor="w",
        )
        self.lbl_fps.grid(row=0, column=0, sticky="w", pady=2)

        self.lbl_pts_count = ctk.CTkLabel(
            stats_frame,
            text="Points / Sweep: 0",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.lbl_pts_count.grid(row=0, column=1, sticky="w", pady=2)

        self.lbl_sample_rate = ctk.CTkLabel(
            stats_frame,
            text="Sample Rate: 0 pts/s",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.lbl_sample_rate.grid(row=1, column=0, sticky="w", pady=2)

        self.lbl_hw_info = ctk.CTkLabel(
            stats_frame,
            text="Sensor: C1 DToF (460.8k)",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SKY,
            anchor="w",
        )
        self.lbl_hw_info.grid(row=1, column=1, sticky="w", pady=2)

    def _build_display_settings_section(self, parent: ctk.CTkFrame) -> None:
        """Palette selection, point size, and safety distance threshold slider."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=(4, 8))

        ctk.CTkLabel(
            parent,
            text="🎨 Radar Display & Filter Settings",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        disp_box = ctk.CTkFrame(parent, fg_color=COLOR_MANTLE, corner_radius=8)
        disp_box.pack(fill="x", padx=12, pady=(0, 8))

        # Color Mode Selector
        mode_row = ctk.CTkFrame(disp_box, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(8, 4))

        ctk.CTkLabel(
            mode_row, text="Color Palette:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(0, 8))

        self.palette_seg = ctk.CTkSegmentedButton(
            mode_row,
            values=["Heatmap", "Safety", "Neon Sky", "Quality"],
            font=ctk.CTkFont(size=10),
            fg_color=COLOR_SURFACE0,
            selected_color=COLOR_PRIMARY,
            selected_hover_color="#B4BEFE",
            command=self._on_palette_changed,
        )
        self.palette_seg.set("Heatmap")
        self.palette_seg.pack(side="left", fill="x", expand=True)

        # Safety Zone Distance Slider
        safety_row = ctk.CTkFrame(disp_box, fg_color="transparent")
        safety_row.pack(fill="x", padx=10, pady=(4, 4))

        self.lbl_safety_slider = ctk.CTkLabel(
            safety_row,
            text=f"Safety Warning Ring: {self._safety_dist_mm} mm",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_SUBTEXT,
        )
        self.lbl_safety_slider.pack(anchor="w")

        self.slider_safety = ctk.CTkSlider(
            safety_row,
            from_=150,
            to=1000,
            number_of_steps=17,
            command=self._on_safety_slider_changed,
        )
        self.slider_safety.set(self._safety_dist_mm)
        self.slider_safety.pack(fill="x", pady=(2, 4))

        # Point Persistence (Trail) Segment
        trail_row = ctk.CTkFrame(disp_box, fg_color="transparent")
        trail_row.pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(
            trail_row, text="Persistence:", font=ctk.CTkFont(size=11), text_color=COLOR_SUBTEXT
        ).pack(side="left", padx=(0, 8))

        self.trail_seg = ctk.CTkSegmentedButton(
            trail_row,
            values=["Single Sweep", "3x Trail Decay"],
            font=ctk.CTkFont(size=10),
            fg_color=COLOR_SURFACE0,
            selected_color=COLOR_PURPLE,
            selected_hover_color="#DDB6F2",
            command=self._on_trail_changed,
        )
        self.trail_seg.set("Single Sweep")
        self.trail_seg.pack(side="left", fill="x", expand=True)

    def _build_terminal_section(self, parent: ctk.CTkFrame) -> None:
        """Embedded terminal output and custom serial command dispatcher."""
        sep = ctk.CTkFrame(parent, height=2, fg_color=COLOR_SURFACE0)
        sep.pack(fill="x", padx=12, pady=(4, 8))

        term_header = ctk.CTkFrame(parent, fg_color="transparent")
        term_header.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(
            term_header,
            text="💻 LiDAR Console Logs",
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
            height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLOR_BASE,
            text_color="#A6ADC8",
            corner_radius=8,
        )
        self.terminal_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    # ------------------------------------------------------------------
    # Radar Canvas Drawing Engine
    # ------------------------------------------------------------------

    def _on_canvas_configure(self, event=None) -> None:
        """Debounce canvas redraw during rapid window resizing."""
        if getattr(self, "_is_destroyed", False):
            return
        if hasattr(self, "_canvas_resize_timer") and self._canvas_resize_timer:
            try:
                self.after_cancel(self._canvas_resize_timer)
            except Exception:
                pass
        self._canvas_resize_timer = self.after(35, self._redraw_canvas)

    def _request_canvas_redraw(self) -> None:
        """Schedule a redraw on the Tk main thread."""
        if not self._render_pending and self.winfo_exists():
            self._render_pending = True
            self.after_idle(self._redraw_canvas)

    def _redraw_canvas(self) -> None:
        """Render the complete polar radar view, grid lines, safety ring, and points."""
        self._render_pending = False
        if not self.winfo_exists():
            return

        canvas = self.radar_canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()

        if w <= 20 or h <= 20:
            return

        canvas.delete("all")

        cx = w / 2.0
        cy = h / 2.0
        max_r = min(cx, cy) - 24.0  # leave margin for cardinal labels

        if max_r <= 10:
            return

        scale_px_per_m = max_r / self._max_range_m

        # 1. Draw polar concentric distance circles (4 rings)
        num_rings = 4
        for i in range(1, num_rings + 1):
            r_dist_m = (self._max_range_m * i) / num_rings
            r_px = r_dist_m * scale_px_per_m

            # Ring outline
            canvas.create_oval(
                cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                outline=COLOR_GRID, width=1, dash=(3, 3) if i < num_rings else None
            )

            # Distance label
            label_text = f"{r_dist_m:.1f}m"
            canvas.create_text(
                cx + 4, cy - r_px + 8,
                text=label_text, fill=COLOR_GRID_TEXT,
                font=("Segoe UI", 8, "bold"), anchor="w"
            )

        # 2. Draw crosshair axes & 45-degree angle rays
        canvas.create_line(cx - max_r, cy, cx + max_r, cy, fill=COLOR_GRID, width=1)
        canvas.create_line(cx, cy - max_r, cx, cy + max_r, fill=COLOR_GRID, width=1)

        # 45° diagonals
        diag_offset = max_r * 0.7071
        canvas.create_line(cx - diag_offset, cy - diag_offset, cx + diag_offset, cy + diag_offset, fill="#1B1D2C", width=1)
        canvas.create_line(cx - diag_offset, cy + diag_offset, cx + diag_offset, cy - diag_offset, fill="#1B1D2C", width=1)

        # 3. Cardinal Direction Labels (Slamtec Frame: 0°=North/Fwd, 90°=East/Right, 180°=South/Rear, 270°=West/Left)
        canvas.create_text(cx, cy - max_r - 12, text="▲ 0° FRONT", fill=COLOR_PRIMARY, font=("Segoe UI", 9, "bold"))
        canvas.create_text(cx + max_r + 14, cy, text="90° R", fill=COLOR_SUBTEXT, font=("Segoe UI", 8, "bold"), anchor="w")
        canvas.create_text(cx, cy + max_r + 12, text="▼ 180° REAR", fill=COLOR_SUBTEXT, font=("Segoe UI", 8, "bold"))
        canvas.create_text(cx - max_r - 14, cy, text="270° L", fill=COLOR_SUBTEXT, font=("Segoe UI", 8, "bold"), anchor="e")

        # 4. Safety Collision Ring (Warning Zone)
        safety_r_px = (self._safety_dist_mm / 1000.0) * scale_px_per_m
        if safety_r_px <= max_r:
            # Danger check: any point within safety zone?
            has_danger = (
                self._latest_scan is not None
                and self._latest_scan.closest_point is not None
                and self._latest_scan.closest_point.distance_mm <= self._safety_dist_mm
            )
            ring_color = COLOR_DANGER if has_danger else "#EBA0AC"
            canvas.create_oval(
                cx - safety_r_px, cy - safety_r_px, cx + safety_r_px, cy + safety_r_px,
                outline=ring_color, width=1.5, dash=(4, 4)
            )

        # 5. Render Point Cloud
        points_to_draw: List[Tuple[LidarPoint, float]] = []  # (point, alpha_factor)

        if self._persistence_mode == "3x Trail Decay" and self._trail_points:
            for trail_idx, (t_stamp, pts) in enumerate(self._trail_points):
                alpha = 0.35 + 0.35 * (trail_idx / max(1, len(self._trail_points) - 1))
                for pt in pts:
                    points_to_draw.append((pt, alpha))
        elif self._latest_scan and self._latest_scan.points:
            for pt in self._latest_scan.points:
                points_to_draw.append((pt, 1.0))

        pt_size = self._point_size

        for pt, alpha in points_to_draw:
            dist_m = pt.distance_mm / 1000.0
            if dist_m <= 0.05 or dist_m > self._max_range_m * 1.05:
                continue

            # Polar -> Canvas coordinates
            # Angle 0° = straight UP (cy - r), 90° = RIGHT (cx + r)
            rad = math.radians(pt.angle_deg)
            px = cx + (dist_m * scale_px_per_m) * math.sin(rad)
            py = cy - (dist_m * scale_px_per_m) * math.cos(rad)

            color = self._get_point_color(pt, alpha)

            canvas.create_oval(
                px - pt_size, py - pt_size, px + pt_size, py + pt_size,
                fill=color, outline=""
            )

        # 6. Highlight Closest Point (pulsing red target circle)
        if self._latest_scan and self._latest_scan.closest_point:
            cp = self._latest_scan.closest_point
            cp_dist_m = cp.distance_mm / 1000.0
            if cp_dist_m <= self._max_range_m:
                cp_rad = math.radians(cp.angle_deg)
                cpx = cx + (cp_dist_m * scale_px_per_m) * math.sin(cp_rad)
                cpy = cy - (cp_dist_m * scale_px_per_m) * math.cos(cp_rad)
                canvas.create_oval(
                    cpx - 6, cpy - 6, cpx + 6, cpy + 6,
                    outline=COLOR_DANGER, width=2
                )

        # 7. Center Robot Footprint Icon
        robot_size = 14
        canvas.create_rectangle(
            cx - robot_size, cy - robot_size, cx + robot_size, cy + robot_size,
            fill=COLOR_SURFACE1, outline=COLOR_PRIMARY, width=1.5
        )
        # Heading Triangle
        canvas.create_polygon(
            cx, cy - robot_size - 6,
            cx - 5, cy - robot_size + 2,
            cx + 5, cy - robot_size + 2,
            fill=COLOR_PRIMARY, outline=""
        )
        canvas.create_text(
            cx, cy, text="🤖", font=("Segoe UI Emoji", 10)
        )

    def _get_point_color(self, pt: LidarPoint, alpha: float = 1.0) -> str:
        """Compute point color based on selected palette mode."""
        if self._color_mode == "Safety":
            if pt.distance_mm <= self._safety_dist_mm:
                return COLOR_DANGER
            elif pt.distance_mm <= self._safety_dist_mm * 2.0:
                return COLOR_WARNING
            else:
                return COLOR_SUCCESS

        elif self._color_mode == "Neon Sky":
            return COLOR_SKY

        elif self._color_mode == "Quality":
            if pt.quality >= 50:
                return COLOR_SUCCESS
            elif pt.quality >= 25:
                return COLOR_PRIMARY
            else:
                return COLOR_PURPLE

        else:
            # Heatmap mode (distance based: Close = Cyan, Mid = Blue, Far = Mauve/Peach)
            norm_dist = min(1.0, max(0.0, (pt.distance_mm / 1000.0) / self._max_range_m))
            if norm_dist < 0.25:
                return COLOR_SKY       # #89DCEB
            elif norm_dist < 0.50:
                return COLOR_PRIMARY   # #89B4FA
            elif norm_dist < 0.75:
                return COLOR_PURPLE    # #CBA6F7
            else:
                return COLOR_ACCENT    # #FAB387

    # ------------------------------------------------------------------
    # Mouse Hover Coordinate Tracker
    # ------------------------------------------------------------------

    def _on_canvas_mouse_move(self, event) -> None:
        """Inspect angle, distance, and coordinates when mouse hovers over radar."""
        canvas = self.radar_canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        cx = w / 2.0
        cy = h / 2.0
        max_r = min(cx, cy) - 24.0

        if max_r <= 0:
            return

        dx = event.x - cx
        dy = -(event.y - cy)  # Flip Y for Cartesian (Up = +Y)

        dist_px = math.hypot(dx, dy)
        dist_m = (dist_px / max_r) * self._max_range_m
        dist_mm = int(dist_m * 1000.0)

        # Angle (0° = North, 90° = East)
        angle_rad = math.atan2(dx, dy)
        angle_deg = math.degrees(angle_rad)
        if angle_deg < 0:
            angle_deg += 360.0

        self.lbl_hover.configure(
            text=f"🎯 Cursor: {angle_deg:.1f}° | {dist_mm:,} mm ({dist_m:.2f} m) | X: {dist_m*math.sin(angle_rad):.2f}m, Y: {dist_m*math.cos(angle_rad):.2f}m"
        )

    def _on_canvas_mouse_leave(self, event) -> None:
        self.lbl_hover.configure(text="Hover over point for distance & angle")

    # ------------------------------------------------------------------
    # UI Event Handlers & Callbacks
    # ------------------------------------------------------------------

    def _on_palette_changed(self, mode: str) -> None:
        self._color_mode = mode
        self._request_canvas_redraw()

    def _on_safety_slider_changed(self, value: float) -> None:
        self._safety_dist_mm = int(value)
        self.lbl_safety_slider.configure(
            text=f"Safety Warning Ring: {self._safety_dist_mm} mm"
        )
        self._request_canvas_redraw()

    def _on_trail_changed(self, mode: str) -> None:
        self._persistence_mode = mode
        if mode == "Single Sweep":
            self._trail_points.clear()
        self._request_canvas_redraw()

    def _set_range_preset(self, range_m: float) -> None:
        self._max_range_m = range_m
        self._request_canvas_redraw()

    def _toggle_scan(self) -> None:
        if self.service.is_scanning:
            self.service.stop_scan()
            self.btn_scan.configure(
                text="Start Scan", fg_color=COLOR_PRIMARY, text_color="#11111B"
            )
        else:
            self.service.start_scan()
            self.btn_scan.configure(
                text="Stop Scan", fg_color=COLOR_DANGER, text_color="#11111B"
            )

    # ------------------------------------------------------------------
    # Connection Management & Port Callbacks
    # ------------------------------------------------------------------

    def _refresh_port_list(self) -> None:
        ports = LidarService.list_available_ports()
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
            selected_baud = int(self.baud_combo.get().strip())
            self.service.connect(port=selected_port, baudrate=selected_baud)

    def _on_connection_changed(self, connected: bool, info: str) -> None:
        """Dispatched from LidarService when serial state changes."""
        def _update():
            if not self.winfo_exists():
                return
            if connected:
                self.btn_connect.configure(
                    text="Disconnect",
                    fg_color=COLOR_DANGER,
                    hover_color="#EBA0AC",
                )
                self.btn_scan.configure(
                    text="Stop Scan" if self.service.is_scanning else "Start Scan",
                    fg_color=COLOR_DANGER if self.service.is_scanning else COLOR_PRIMARY,
                )
                if self.service.is_mock:
                    self.status_badge.configure(
                        text="⚙️ Mock Mode", text_color=COLOR_ACCENT
                    )
                else:
                    self.status_badge.configure(
                        text="🟢 Connected", text_color=COLOR_SUCCESS
                    )
            else:
                self.btn_connect.configure(
                    text="Connect",
                    fg_color=COLOR_SUCCESS,
                    hover_color="#94E2D5",
                )
                self.btn_scan.configure(text="Start Scan", fg_color=COLOR_PRIMARY)
                self.status_badge.configure(
                    text="🔴 Disconnected", text_color=COLOR_DANGER
                )

        try:
            if not getattr(self, "_is_destroyed", False) and self.winfo_exists():
                self.after(0, _update)
        except Exception:
            pass

    def on_activate(self) -> None:
        """Called when this tab is selected in DeveloperWindow."""
        self._is_active = True
        if self._latest_scan is not None:
            self._on_scan_data_received(self._latest_scan)

    def on_deactivate(self) -> None:
        """Called when navigating away from this tab."""
        self._is_active = False

    def destroy(self) -> None:
        """Clean up callbacks and scheduled timers."""
        self._is_destroyed = True
        self._is_active = False
        self._log_flush_scheduled = False
        if hasattr(self, "_canvas_resize_timer") and self._canvas_resize_timer:
            try:
                self.after_cancel(self._canvas_resize_timer)
            except Exception:
                pass
            self._canvas_resize_timer = None
        if hasattr(self, "service") and self.service:
            if getattr(self.service, "on_scan_data", None) == self._on_scan_data_received:
                self.service.on_scan_data = None
            if getattr(self.service, "on_log", None) == self._on_log_received:
                self.service.on_log = None
            if getattr(self.service, "on_connection_change", None) == self._on_connection_changed:
                self.service.on_connection_change = None
        super().destroy()

    # ------------------------------------------------------------------
    # Telemetry & Scan Callbacks
    # ------------------------------------------------------------------

    def _on_scan_data_received(self, scan_data: LidarScanData) -> None:
        """Receive complete 360-degree point cloud sweep."""
        self._latest_scan = scan_data

        if self._persistence_mode == "3x Trail Decay":
            self._trail_points.append((scan_data.timestamp, scan_data.points))
            if len(self._trail_points) > 3:
                self._trail_points.pop(0)

        # Gate execution: do nothing if page is not active or destroyed
        if getattr(self, "_is_destroyed", False) or not self.winfo_exists() or not getattr(self, "_is_active", True):
            return

        if self._render_pending:
            return
        self._render_pending = True

        def _update_ui():
            self._render_pending = False
            if getattr(self, "_is_destroyed", False) or not self.winfo_exists() or not getattr(self, "_is_active", True):
                return

            # Update stats
            self.lbl_fps.configure(text=f"Scan Rate: {scan_data.scan_rate_hz:.1f} Hz")
            self.lbl_pts_count.configure(text=f"Points / Sweep: {scan_data.point_count}")
            self.lbl_sample_rate.configure(text=f"Sample Rate: {scan_data.sample_rate_hz:.0f} pts/s")

            # Update 4 sector cards
            def _format_sector(dist: int, lbl_val: ctk.CTkLabel, lbl_badge: ctk.CTkLabel):
                if dist <= 0 or dist > 15000:
                    lbl_val.configure(text="— mm", text_color=COLOR_PRIMARY)
                    lbl_badge.configure(text="Clear", text_color=COLOR_SUCCESS)
                elif dist <= self._safety_dist_mm:
                    lbl_val.configure(text=f"{dist:,} mm", text_color=COLOR_DANGER)
                    lbl_badge.configure(text="⚠️ DANGER (<300mm)", text_color=COLOR_DANGER)
                elif dist <= self._safety_dist_mm * 2:
                    lbl_val.configure(text=f"{dist:,} mm", text_color=COLOR_WARNING)
                    lbl_badge.configure(text="Caution", text_color=COLOR_WARNING)
                else:
                    lbl_val.configure(text=f"{dist:,} mm", text_color=COLOR_SUCCESS)
                    lbl_badge.configure(text="Safe", text_color=COLOR_SUCCESS)

            _format_sector(scan_data.min_front_dist_mm, self.lbl_dist_front, self.badge_front)
            _format_sector(scan_data.min_back_dist_mm, self.lbl_dist_back, self.badge_back)
            _format_sector(scan_data.min_left_dist_mm, self.lbl_dist_left, self.badge_left)
            _format_sector(scan_data.min_right_dist_mm, self.lbl_dist_right, self.badge_right)

            # Closest Obstacle banner
            if scan_data.closest_point and scan_data.closest_point.distance_mm > 0:
                cp = scan_data.closest_point
                color = COLOR_DANGER if cp.distance_mm <= self._safety_dist_mm else (
                    COLOR_WARNING if cp.distance_mm <= self._safety_dist_mm * 2 else COLOR_TEXT
                )
                self.lbl_closest_obstacle.configure(
                    text=f"⚠️ Closest: {int(cp.distance_mm):,} mm @ {cp.angle_deg:.1f}° (X: {cp.x_m}m, Y: {cp.y_m}m)",
                    text_color=color,
                )
            else:
                self.lbl_closest_obstacle.configure(
                    text="⚠️ Closest Obstacle: None detected", text_color=COLOR_TEXT
                )

            # Redraw canvas
            self._redraw_canvas()

        try:
            if not getattr(self, "_is_destroyed", False) and self.winfo_exists():
                self.after_idle(_update_ui)
            else:
                self._render_pending = False
        except Exception:
            self._render_pending = False

    # ------------------------------------------------------------------
    # Terminal Logging
    # ------------------------------------------------------------------

    def _on_log_received(self, text: str) -> None:
        if getattr(self, "_is_destroyed", False):
            return
        self._pending_logs.append(text)
        if not self._log_flush_scheduled:
            self._log_flush_scheduled = True
            try:
                if self.winfo_exists():
                    self.after(50, self._flush_pending_logs)
            except Exception:
                self._log_flush_scheduled = False

    def _flush_pending_logs(self) -> None:
        self._log_flush_scheduled = False
        if not self.winfo_exists() or not self._pending_logs:
            return

        chunk = "\n".join(self._pending_logs) + "\n"
        self._pending_logs.clear()

        try:
            self.terminal_box.insert("end", chunk)
            line_count = int(self.terminal_box.index("end-1c").split(".")[0])
            if line_count > 300:
                self.terminal_box.delete("1.0", f"{line_count - 200}.0")
            self.terminal_box.see("end")
        except Exception:
            pass

    def _clear_terminal(self) -> None:
        self._pending_logs.clear()
        self.terminal_box.delete("1.0", "end")
