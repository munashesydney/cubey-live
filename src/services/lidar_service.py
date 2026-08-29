"""
LiDAR Service module for Waveshare / Slamtec RPLIDAR C1.

Manages high-speed serial communication (460,800 baud) with the RPLIDAR C1 sensor,
real-time 360-degree point cloud packet decoding, 4-sector obstacle proximity telemetry,
health monitoring, motor control, and simulated mock radar generation.
"""

import math
import logging
import platform
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None
    PYSERIAL_AVAILABLE = False
    logger.warning("pyserial is not installed. Running LiDAR service in mock mode.")


# ---------------------------------------------------------------------------
# Slamtec RPLIDAR Protocol Constants
# ---------------------------------------------------------------------------
SYNC_BYTE = 0xA5
SYNC_BYTE2 = 0x5A

CMD_STOP = 0x25
CMD_RESET = 0x40
CMD_SCAN = 0x20
CMD_FORCE_SCAN = 0x21
CMD_GET_INFO = 0x50
CMD_GET_HEALTH = 0x52
CMD_GET_SAMPLERATE = 0x59

RESP_DESCRIPTOR_LEN = 7
RESP_HEALTH_LEN = 3
RESP_INFO_LEN = 20


@dataclass
class LidarPoint:
    """A single range measurement in a 360-degree LiDAR sweep."""
    angle_deg: float      # 0.0 to 360.0 degrees (0 = Front / Heading)
    distance_mm: float    # Distance in millimeters
    quality: int          # Signal strength / reflection quality (0-63 or 0-255)
    x_m: float = 0.0      # Cartesian X in meters (Right = positive X)
    y_m: float = 0.0      # Cartesian Y in meters (Front = positive Y)

    def __post_init__(self):
        rad = math.radians(self.angle_deg)
        dist_m = self.distance_mm / 1000.0
        # Robot coordinates: 0 deg = North (+Y), 90 deg = East (+X)
        self.x_m = round(dist_m * math.sin(rad), 4)
        self.y_m = round(dist_m * math.cos(rad), 4)


@dataclass
class LidarScanData:
    """A full 360-degree sweep frame containing point cloud and spatial telemetry."""
    points: List[LidarPoint] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    scan_rate_hz: float = 0.0
    sample_rate_hz: float = 0.0
    point_count: int = 0
    # Minimum distances per cardinal sector (in mm)
    min_front_dist_mm: int = 99999    # Front arc (-30° to +30°)
    min_left_dist_mm: int = 99999     # Left arc (60° to 120°)
    min_right_dist_mm: int = 99999    # Right arc (240° to 300°)
    min_back_dist_mm: int = 99999     # Rear arc (150° to 210°)
    closest_point: Optional[LidarPoint] = None
    health_status: str = "OK"
    # Valid protocol samples with a zero distance.  The C1 emits these when no
    # obstacle return was received at that angle.  They are kept separately so
    # mapping can clear a short, conservative ray without presenting a fake
    # obstacle point to collision monitoring or the UI.
    clear_ray_angles_deg: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "point_count": self.point_count,
            "clear_ray_count": len(self.clear_ray_angles_deg),
            "scan_rate_hz": self.scan_rate_hz,
            "sample_rate_hz": self.sample_rate_hz,
            "min_front_dist_mm": self.min_front_dist_mm,
            "min_left_dist_mm": self.min_left_dist_mm,
            "min_right_dist_mm": self.min_right_dist_mm,
            "min_back_dist_mm": self.min_back_dist_mm,
            "closest_distance_mm": self.closest_point.distance_mm if self.closest_point else 0,
            "closest_angle_deg": self.closest_point.angle_deg if self.closest_point else 0,
            "health_status": self.health_status,
            "timestamp": self.timestamp,
        }


class LidarService:
    """
    Coordinates serial communication with the Slamtec RPLIDAR C1,
    parses 360-degree point clouds, computes obstacle proximity metrics,
    and provides a simulated mock mode when physical hardware is not present.
    """

    DEFAULT_BAUDRATE = 460800  # RPLIDAR C1 standard baudrate

    def __init__(
        self,
        default_port: Optional[str] = None,
        default_baudrate: int = DEFAULT_BAUDRATE,
        min_valid_distance_mm: int = 30,
        mount_yaw_deg: float = 0.0,
        on_scan_data: Optional[Callable[[LidarScanData], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_connection_change: Optional[Callable[[bool, str], None]] = None,
    ):
        self.port: str = default_port or self._get_default_port_for_platform()
        self.baudrate: int = default_baudrate
        self.min_valid_distance_mm = max(1, int(min_valid_distance_mm))
        self.mount_yaw_deg = float(mount_yaw_deg) % 360.0

        self.on_scan_data = on_scan_data
        self.on_log = on_log
        self.on_connection_change = on_connection_change

        self._serial: Optional[Any] = None
        self._write_lock = threading.Lock()
        self._is_connected: bool = False
        self._is_scanning: bool = False
        self._is_mock: bool = False
        self._motor_enabled: bool = False

        self._worker_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Telemetry cache
        self.latest_scan = LidarScanData()
        self.device_info: Dict[str, Any] = {}
        self.health_info: Dict[str, Any] = {"status": "UNKNOWN", "error_code": 0}

    # ------------------------------------------------------------------
    # Port & Platform Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _get_default_port_for_platform() -> str:
        """Choose reasonable default port based on current OS."""
        system = platform.system().lower()
        if "linux" in system:
            return "/dev/ttyUSB0"
        elif "darwin" in system:
            return "/dev/cu.usbserial-0001"
        else:
            return "COM3"

    @classmethod
    def list_available_ports(cls) -> List[str]:
        """Scan system for available serial ports with standard fallbacks."""
        ports = []
        if PYSERIAL_AVAILABLE and serial is not None:
            try:
                for port_info in serial.tools.list_ports.comports():
                    ports.append(port_info.device)
            except Exception as e:
                logger.warning("Error scanning serial ports for LiDAR: %s", e)

        # Append common Linux & Windows USB serial devices if not already discovered
        system = platform.system().lower()
        if "linux" in system:
            for p in ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0", "/dev/ttyAMA0"]:
                if p not in ports:
                    ports.append(p)
        elif "windows" in system:
            for p in ["COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8"]:
                if p not in ports:
                    ports.append(p)

        if "MOCK_SIMULATOR" not in ports:
            ports.append("MOCK_SIMULATOR")

        return ports

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    @property
    def is_mock(self) -> bool:
        return self._is_mock

    def scan_health(
        self,
        *,
        max_age_s: float = 0.35,
        min_points: int = 30,
        min_scan_rate_hz: float = 3.0,
        now: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Return fail-closed scan-stream health and a diagnostic reason."""
        if not self._is_connected:
            return False, "lidar_disconnected"
        if not self._is_scanning:
            return False, "lidar_not_scanning"

        scan = self.latest_scan
        check_time = time.time() if now is None else now
        age_s = max(0.0, check_time - scan.timestamp)
        if age_s > max_age_s:
            return False, f"lidar_scan_stale:{age_s:.3f}s"
        if scan.point_count < min_points:
            return False, f"lidar_too_few_points:{scan.point_count}"
        if scan.scan_rate_hz < min_scan_rate_hz:
            return False, f"lidar_scan_rate_low:{scan.scan_rate_hz:.1f}Hz"
        if scan.health_status != "OK":
            return False, f"lidar_health:{scan.health_status}"
        return True, "ok"

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> bool:
        """
        Open serial connection to the RPLIDAR C1 at the designated baudrate (460800 bps).
        If port is 'MOCK_SIMULATOR', starts the simulated mock radar generator.
        """
        if port:
            self.port = port
        if baudrate:
            self.baudrate = baudrate

        self.disconnect()

        if self.port == "MOCK_SIMULATOR" or not PYSERIAL_AVAILABLE:
            self._is_mock = True
            self._is_connected = True
            self._is_scanning = True
            self._running = True
            self._motor_enabled = True
            self.device_info = {
                "model": "RPLIDAR C1 (Simulated)",
                "firmware": "1.00",
                "hardware": "1.0",
                "serial_number": "SIM-C1-2026-OK",
            }
            self.health_info = {"status": "OK", "error_code": 0}

            self._worker_thread = threading.Thread(
                target=self._mock_lidar_loop, daemon=True, name="LidarMockWorker"
            )
            self._worker_thread.start()
            self._emit_log(f"Connected to {self.port} (Mock Simulation Radar Mode)")
            self._emit_connection_change(True, f"Mock Mode ({self.port})")
            return True

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1.0,
                write_timeout=1.0,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

            # Set DTR to enable motor control if hardware adapter supports it
            try:
                self._serial.dtr = False
                time.sleep(0.05)
                self._serial.dtr = True
            except Exception:
                pass

            self._is_mock = False
            self._is_connected = True
            self._running = True
            self._motor_enabled = True

            self._emit_log(f"Connected to hardware LiDAR UART: {self.port} @ {self.baudrate} baud")
            self._emit_connection_change(True, f"Connected ({self.port})")

            # Query health & device info
            self.get_health()
            self.get_device_info()

            # Automatically start scan stream
            self.start_scan()
            return True

        except Exception as e:
            logger.error("Failed to connect to LiDAR serial port %s: %s", self.port, e)
            self._is_connected = False
            self._serial = None
            self._emit_log(f"LiDAR Connection failed to {self.port}: {e}")
            self._emit_connection_change(False, f"Connection Failed: {e}")
            return False

    def disconnect(self) -> None:
        """Safely stop scanning, disable motor, and close serial port."""
        self.stop_scan()
        self._running = False

        if self._serial:
            try:
                self._send_command(CMD_STOP)
                time.sleep(0.05)
                self._serial.close()
            except Exception as e:
                logger.warning("Error closing LiDAR serial port: %s", e)
            self._serial = None

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=0.8)
        self._worker_thread = None

        was_connected = self._is_connected
        self._is_connected = False
        self._is_scanning = False
        self._is_mock = False
        self._motor_enabled = False

        if was_connected:
            self._emit_log("Disconnected from LiDAR sensor.")
            self._emit_connection_change(False, "Disconnected")

    # ------------------------------------------------------------------
    # LiDAR Commands & Control
    # ------------------------------------------------------------------

    def _send_command(self, cmd_byte: int, payload: bytes = b"") -> bool:
        """Send raw command byte with 0xA5 sync prefix."""
        if not self._serial or not self._serial.is_open:
            return False
        with self._write_lock:
            try:
                pkt = bytearray([SYNC_BYTE, cmd_byte])
                if payload:
                    pkt.extend(payload)
                self._serial.write(pkt)
                self._serial.flush()
                return True
            except Exception as e:
                logger.error("LiDAR write command 0x%02X error: %s", cmd_byte, e)
                return False

    def _read_response_descriptor(self, timeout: float = 1.0) -> Optional[Tuple[int, int, int]]:
        """
        Read standard 7-byte Slamtec response descriptor:
        [0xA5, 0x5A, 30-bit length, 2-bit sub-type, data_type]
        Returns (length, sub_type, data_type) or None.
        """
        if not self._serial or not self._serial.is_open:
            return None
        start_t = time.time()
        buf = bytearray()
        while time.time() - start_t < timeout:
            b = self._serial.read(1)
            if not b:
                continue
            buf.extend(b)
            if len(buf) >= 2:
                if buf[0] == SYNC_BYTE and buf[1] == SYNC_BYTE2:
                    remaining = RESP_DESCRIPTOR_LEN - len(buf)
                    if remaining > 0:
                        rest = self._serial.read(remaining)
                        buf.extend(rest)
                    if len(buf) == RESP_DESCRIPTOR_LEN:
                        # Parse length & type
                        length_sub = struct.unpack("<I", buf[2:6])[0]
                        length = length_sub & 0x3FFFFFFF
                        sub_type = (length_sub >> 30) & 0x03
                        data_type = buf[6]
                        return (length, sub_type, data_type)
                else:
                    # Sync search
                    buf = buf[1:]
        return None

    def start_scan(self) -> bool:
        """Send START_SCAN command to initiate continuous 360-degree scan data stream."""
        if not self._is_connected:
            return False

        if self._is_mock:
            self._is_scanning = True
            self._emit_log("Started Mock LiDAR scanning stream.")
            return True

        self.stop_scan()
        time.sleep(0.05)

        with self._write_lock:
            if not self._serial or not self._serial.is_open:
                return False
            self._serial.reset_input_buffer()

        # Send CMD_SCAN (0x20)
        self._emit_log("Starting RPLIDAR C1 scan (0xA5 0x20)...")
        if not self._send_command(CMD_SCAN):
            return False

        # Read response descriptor
        desc = self._read_response_descriptor(timeout=1.5)
        if desc is None:
            self._emit_log("Warning: No scan response descriptor received, attempting stream read anyway...")
        else:
            length, sub_type, data_type = desc
            self._emit_log(f"Scan response descriptor: type=0x{data_type:02X}, len={length}")

        self._is_scanning = True
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._hardware_scan_loop, daemon=True, name="LidarScanWorker"
        )
        self._worker_thread.start()
        return True

    def stop_scan(self) -> bool:
        """Send STOP command to halt scanner rotation / laser emission."""
        self._is_scanning = False
        if self._is_mock:
            self._emit_log("Stopped Mock LiDAR scanning.")
            return True

        if self._is_connected and self._serial and self._serial.is_open:
            self._send_command(CMD_STOP)
            time.sleep(0.05)
            self._emit_log("Sent LiDAR STOP command.")
            return True
        return False

    def reset_core(self) -> bool:
        """Reboot the LiDAR internal DSP core."""
        if self._is_mock:
            self._emit_log("Mock LiDAR core reset.")
            return True

        if self._is_connected and self._serial and self._serial.is_open:
            self._emit_log("Sending LiDAR RESET command (0xA5 0x40)...")
            self._send_command(CMD_RESET)
            time.sleep(0.2)
            # Reconnect & query
            self.connect(self.port, self.baudrate)
            return True
        return False

    def get_health(self) -> Dict[str, Any]:
        """Query LiDAR sensor health status (OK, Warning, Error)."""
        if self._is_mock:
            return self.health_info

        if not self._is_connected or not self._serial:
            return {"status": "DISCONNECTED", "error_code": 0}

        try:
            self._send_command(CMD_GET_HEALTH)
            desc = self._read_response_descriptor(timeout=0.5)
            if desc and self._serial:
                payload = self._serial.read(RESP_HEALTH_LEN)
                if len(payload) == RESP_HEALTH_LEN:
                    status_byte, err_low, err_high = payload[0], payload[1], payload[2]
                    status_map = {0: "OK", 1: "WARNING", 2: "ERROR"}
                    status_str = status_map.get(status_byte, f"CODE_{status_byte}")
                    err_code = (err_high << 8) | err_low
                    self.health_info = {"status": status_str, "error_code": err_code}
                    self._emit_log(f"LiDAR Health: {status_str} (Error Code: 0x{err_code:04X})")
                    return self.health_info
        except Exception as e:
            logger.warning("Error querying LiDAR health: %s", e)

        return self.health_info

    def get_device_info(self) -> Dict[str, Any]:
        """Query LiDAR model ID, firmware, hardware revision, and serial number."""
        if self._is_mock:
            return self.device_info

        if not self._is_connected or not self._serial:
            return {}

        try:
            self._send_command(CMD_GET_INFO)
            desc = self._read_response_descriptor(timeout=0.5)
            if desc and self._serial:
                payload = self._serial.read(RESP_INFO_LEN)
                if len(payload) == RESP_INFO_LEN:
                    model = payload[0]
                    fw_minor = payload[1]
                    fw_major = payload[2]
                    hw = payload[3]
                    serial_num = payload[4:20].hex().upper()
                    self.device_info = {
                        "model": f"RPLIDAR (Model 0x{model:02X})",
                        "firmware": f"{fw_major}.{fw_minor:02d}",
                        "hardware": f"{hw}",
                        "serial_number": serial_num,
                    }
                    self._emit_log(
                        f"LiDAR Info: Model=0x{model:02X}, FW={fw_major}.{fw_minor:02d}, "
                        f"HW={hw}, S/N={serial_num}"
                    )
                    return self.device_info
        except Exception as e:
            logger.warning("Error querying LiDAR device info: %s", e)

        return self.device_info

    # ------------------------------------------------------------------
    # Spatial Analysis & Sector Distance Computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_scan_metrics(
        points: List[LidarPoint],
        scan_rate_hz: float,
        sample_rate_hz: float,
        min_valid_distance_mm: int = 30,
        clear_ray_angles_deg: Optional[List[float]] = None,
    ) -> LidarScanData:
        """
        Calculates cardinal 4-sector proximity distances and locates the closest obstacle.
        Sectors:
          - Front: 330° to 30° (-30° to +30°)
          - Right: 60° to 120°
          - Rear: 150° to 210°
          - Left: 240° to 300°
        """
        min_front = 99999
        min_left = 99999
        min_right = 99999
        min_back = 99999
        closest_pt: Optional[LidarPoint] = None
        min_overall = 99999

        for pt in points:
            dist = pt.distance_mm
            # Never use a broad near-field exclusion here. It made real walls
            # disappear once they were close enough to be most dangerous.
            # Platform self-reflections must be removed with a calibrated
            # footprint filter at the collision-monitor layer.
            if dist < min_valid_distance_mm or dist > 16000:
                continue

            angle = pt.angle_deg

            # Front: [330, 360) or [0, 30]
            if angle >= 330 or angle <= 30:
                if dist < min_front:
                    min_front = int(dist)

            # In standard Slamtec clockwise frame: 0=Fwd, 90=Right, 180=Back, 270=Left
            if 60 <= angle <= 120:
                if dist < min_right:
                    min_right = int(dist)
            elif 150 <= angle <= 210:
                if dist < min_back:
                    min_back = int(dist)
            elif 240 <= angle <= 300:
                if dist < min_left:
                    min_left = int(dist)

            if dist < min_overall:
                min_overall = dist
                closest_pt = pt

        clear_rays = list(clear_ray_angles_deg or [])
        return LidarScanData(
            points=points,
            clear_ray_angles_deg=clear_rays,
            timestamp=time.time(),
            scan_rate_hz=round(scan_rate_hz, 1),
            sample_rate_hz=round(sample_rate_hz, 0),
            # Stream health is based on valid samples, not only reflected
            # hits.  An open room can legitimately contain many no-return
            # samples and must not look like a dead LiDAR stream.
            point_count=len(points) + len(clear_rays),
            min_front_dist_mm=min_front if min_front < 99999 else 0,
            min_left_dist_mm=min_left if min_left < 99999 else 0,
            min_right_dist_mm=min_right if min_right < 99999 else 0,
            min_back_dist_mm=min_back if min_back < 99999 else 0,
            closest_point=closest_pt,
            health_status="OK",
        )

    # ------------------------------------------------------------------
    # Background Hardware Worker Loop
    # ------------------------------------------------------------------

    def _hardware_scan_loop(self) -> None:
        """
        Background loop parsing raw 5-byte sample nodes from RPLIDAR C1:
        Node Structure:
          byte 0: [S | !S | Quality (6-bit)]
          byte 1: [Angle_q6_low (7-bit) | CheckBit (must be 1)]
          byte 2: [Angle_q6_high (8-bit)]
          byte 3: [Dist_q2_low (8-bit)]
          byte 4: [Dist_q2_high (8-bit)]
        """
        accumulated_points: List[LidarPoint] = []
        accumulated_clear_ray_angles: List[float] = []
        last_scan_time = time.time()
        sample_count_window = 0
        window_start_time = time.time()
        scan_rate_hz = 10.0
        sample_rate_hz = 5000.0

        NODE_LEN = 5

        while self._running and self._is_scanning and self._serial and self._serial.is_open:
            try:
                # Read 5 bytes for one sample node
                raw = self._serial.read(NODE_LEN)
                if len(raw) < NODE_LEN:
                    continue

                b0, b1, b2, b3, b4 = raw[0], raw[1], raw[2], raw[3], raw[4]

                # Validate check bit (bit 0 of byte 1 must be 1) and sync bits
                sync_bit = b0 & 0x01
                inv_sync_bit = (b0 >> 1) & 0x01
                check_bit = b1 & 0x01

                if check_bit != 1 or (sync_bit == inv_sync_bit):
                    # Packet alignment slip — read 1 byte to resynchronize stream
                    self._serial.read(1)
                    continue

                quality = b0 >> 2
                angle_q6 = (b2 << 7) | (b1 >> 1)
                angle_deg = round(angle_q6 / 64.0, 2)
                if angle_deg >= 360.0:
                    angle_deg -= 360.0

                dist_q2 = (b4 << 8) | b3
                distance_mm = round(dist_q2 / 4.0, 1)

                sample_count_window += 1

                # Start of a new 360-degree rotation (sync_bit == 1)
                if sync_bit == 1 and (
                    len(accumulated_points) + len(accumulated_clear_ray_angles) > 15
                ):
                    now = time.time()
                    dt = now - last_scan_time
                    if dt > 0.01:
                        scan_rate_hz = 1.0 / dt
                    last_scan_time = now

                    # Measure sample rate over window
                    win_dt = now - window_start_time
                    if win_dt >= 1.0:
                        sample_rate_hz = sample_count_window / win_dt
                        sample_count_window = 0
                        window_start_time = now

                    scan_data = self._compute_scan_metrics(
                        accumulated_points,
                        scan_rate_hz,
                        sample_rate_hz,
                        self.min_valid_distance_mm,
                        accumulated_clear_ray_angles,
                    )
                    self.latest_scan = scan_data

                    if self.on_scan_data:
                        try:
                            self.on_scan_data(scan_data)
                        except Exception as e:
                            logger.warning("Error in on_scan_data callback: %s", e)

                    accumulated_points = []
                    accumulated_clear_ray_angles = []

                if distance_mm > 0:
                    accumulated_points.append(
                        LidarPoint(
                            angle_deg=(angle_deg + self.mount_yaw_deg) % 360.0,
                            distance_mm=distance_mm,
                            quality=quality,
                        )
                    )
                else:
                    accumulated_clear_ray_angles.append(
                        round((angle_deg + self.mount_yaw_deg) % 360.0, 2)
                    )

            except Exception as e:
                if self._running and self._is_scanning:
                    logger.error("LiDAR hardware read loop error: %s", e)
                    self._emit_log(f"LiDAR stream error: {e}")
                    time.sleep(0.1)
                break

    # ------------------------------------------------------------------
    # Realistic Mock Simulation Mode
    # ------------------------------------------------------------------

    def _mock_lidar_loop(self) -> None:
        """
        Generates simulated 360-degree point cloud radar data at ~10 Hz.
        Simulates:
          - A rectangular room (5.0m x 4.0m)
          - A moving dynamic obstacle oscillating back and forth
          - Realistic measurement jitter and signal quality
        """
        import random

        sim_time = 0.0
        points_per_sweep = 480  # ~480 points = 0.75° resolution

        # Room bounds (meters relative to robot at 0,0)
        wall_front = 2.2
        wall_back = -1.8
        wall_right = 2.0
        wall_left = -2.0

        while self._running and self._is_mock and self._is_scanning:
            frame_start = time.time()
            sim_time += 0.1

            # Moving obstacle position (oscillates in front-left quadrant)
            obs_x = -0.8 + 0.5 * math.sin(sim_time * 1.5)
            obs_y = 1.2 + 0.4 * math.cos(sim_time * 1.2)
            obs_radius = 0.22

            sweep_points: List[LidarPoint] = []

            for i in range(points_per_sweep):
                angle_deg = (i * 360.0) / points_per_sweep
                rad = math.radians(angle_deg)
                sin_a = math.sin(rad)
                cos_a = math.cos(rad)

                # Ray-cast against 4 rectangular room walls
                dists = []

                # Front wall (+Y)
                if cos_a > 0.001:
                    d = wall_front / cos_a
                    if wall_left <= d * sin_a <= wall_right:
                        dists.append(d)

                # Back wall (-Y)
                if cos_a < -0.001:
                    d = wall_back / cos_a
                    if wall_left <= d * sin_a <= wall_right:
                        dists.append(d)

                # Right wall (+X)
                if sin_a > 0.001:
                    d = wall_right / sin_a
                    if wall_back <= d * cos_a <= wall_front:
                        dists.append(d)

                # Left wall (-X)
                if sin_a < -0.001:
                    d = wall_left / sin_a
                    if wall_back <= d * cos_a <= wall_front:
                        dists.append(d)

                # Ray-cast against circular obstacle
                dist_to_obs = math.hypot(obs_x, obs_y)
                obs_angle_deg = math.degrees(math.atan2(obs_x, obs_y))
                if obs_angle_deg < 0:
                    obs_angle_deg += 360.0

                diff_angle = abs(angle_deg - obs_angle_deg)
                if diff_angle > 180:
                    diff_angle = 360 - diff_angle

                if diff_angle < 15.0 and dist_to_obs > obs_radius:
                    dists.append(dist_to_obs - obs_radius)

                final_dist_m = min(dists) if dists else 3.0

                # Add small measurement noise (+- 8mm)
                final_dist_m += random.gauss(0, 0.008)
                final_dist_mm = max(50.0, final_dist_m * 1000.0)
                quality = random.randint(45, 60)

                sweep_points.append(
                    LidarPoint(
                        angle_deg=round(angle_deg, 1),
                        distance_mm=round(final_dist_mm, 1),
                        quality=quality,
                    )
                )

            # Compute telemetry & dispatch
            scan_data = self._compute_scan_metrics(
                sweep_points, scan_rate_hz=10.0, sample_rate_hz=4800.0
            )
            self.latest_scan = scan_data

            if self.on_scan_data:
                try:
                    self.on_scan_data(scan_data)
                except Exception as e:
                    logger.warning("Error in on_scan_data mock callback: %s", e)

            # Maintain ~10 Hz (100ms per frame)
            elapsed = time.time() - frame_start
            sleep_time = max(0.01, 0.10 - elapsed)
            time.sleep(sleep_time)

    # ------------------------------------------------------------------
    # Notification & Logging Helpers
    # ------------------------------------------------------------------

    def _emit_log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
            except Exception as e:
                logger.warning("Error in on_log callback: %s", e)

    def _emit_connection_change(self, connected: bool, info: str) -> None:
        if self.on_connection_change:
            try:
                self.on_connection_change(connected, info)
            except Exception as e:
                logger.warning("Error in on_connection_change callback: %s", e)


# ---------------------------------------------------------------------------
# Global Singleton Accessor
# ---------------------------------------------------------------------------
_SHARED_LIDAR_SERVICE: Optional[LidarService] = None


def get_lidar_service() -> LidarService:
    """Get or instantiate the global shared LidarService singleton."""
    global _SHARED_LIDAR_SERVICE
    if _SHARED_LIDAR_SERVICE is None:
        from src.config import config

        _SHARED_LIDAR_SERVICE = LidarService(
            default_port=config.lidar_port or None,
            default_baudrate=config.lidar_baudrate,
            min_valid_distance_mm=config.lidar_min_valid_distance_mm,
            mount_yaw_deg=config.lidar_mount_yaw_deg,
        )
        if config.lidar_auto_connect:
            try:
                _SHARED_LIDAR_SERVICE.connect()
            except Exception as e:
                logger.warning("Auto-connection for LidarService deferred: %s", e)
    return _SHARED_LIDAR_SERVICE
