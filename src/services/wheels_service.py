"""
Wheels Service module for Cubey robot.

Manages hardware UART / Serial communication between Raspberry Pi 5 and
ESP32-S3 (cubey_wheels), telemetry parsing (cliff sensors, motion state, speed),
and pulse/continuous motion dispatch.
"""

import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Try importing pyserial; provide graceful fallback if not installed
try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None
    PYSERIAL_AVAILABLE = False
    logger.warning("pyserial is not installed. Running in mock/simulation mode.")


@dataclass
class TelemetryData:
    """Parsed real-time telemetry from cubey_wheels firmware."""
    front_distance_mm: int = 0
    back_distance_mm: int = 0
    front_cliff: bool = False
    back_cliff: bool = False
    motion: str = "STOPPED"
    speed: int = 180
    battery_voltage: float = 0.0
    battery_pct: int = 0
    is_charging: bool = False
    emergency_stopped: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "front_distance_mm": self.front_distance_mm,
            "back_distance_mm": self.back_distance_mm,
            "front_cliff": self.front_cliff,
            "back_cliff": self.back_cliff,
            "motion": self.motion,
            "speed": self.speed,
            "battery_voltage": self.battery_voltage,
            "battery_pct": self.battery_pct,
            "is_charging": self.is_charging,
            "emergency_stopped": self.emergency_stopped,
            "timestamp": self.timestamp,
        }


class WheelsService:
    """
    Coordinates serial communication with the ESP32 mecanum wheel controller.
    """

    # Supported motion command names matching firmware
    COMMANDS = [
        "forward",
        "backward",
        "strafeLeft",
        "strafeRight",
        "rotateLeft",
        "rotateRight",
        "forwardLeft",
        "forwardRight",
        "backwardLeft",
        "backwardRight",
        "stop",
    ]

    def __init__(
        self,
        default_port: Optional[str] = None,
        default_baudrate: int = 115200,
        on_telemetry: Optional[Callable[[TelemetryData], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_connection_change: Optional[Callable[[bool, str], None]] = None,
    ):
        self.port: str = default_port or self._get_default_port_for_platform()
        self.baudrate: int = default_baudrate

        self.on_telemetry = on_telemetry
        self.on_log = on_log
        self.on_connection_change = on_connection_change

        self._serial: Optional[Any] = None
        self._write_lock = threading.Lock()
        self._is_connected: bool = False
        self._is_mock: bool = False

        self._reader_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Continuous movement repeat timer
        self._continuous_timer: Optional[threading.Timer] = None
        self._continuous_command: Optional[str] = None
        self._continuous_lock = threading.Lock()

        # All motion producers share one inhibit and generation counter.  This
        # prevents an old pulse worker from stopping a newer command and gives
        # emergency stop true latched semantics across reconnects.
        # Wheel callbacks can synchronously inspect motion state while a command
        # is being issued.  A re-entrant lock prevents that callback path from
        # deadlocking the command thread.
        self._motion_lock = threading.RLock()
        self._motion_inhibited: bool = False
        self._motion_inhibit_reason: str = ""
        self._pulse_generation: int = 0

        # Telemetry state cache
        self.telemetry = TelemetryData()
        self._telemetry_received = False

        # Battery charging trend detection
        self._voltage_samples: List[tuple] = []
        self._is_charging: bool = False
        self._charging_sim_override: Optional[bool] = None

    def set_charging_simulation(self, charging: Optional[bool]) -> None:
        """Override charging detection state for UI/animation simulation and testing."""
        self._charging_sim_override = charging
        if charging is not None:
            self._is_charging = charging
            self.telemetry.is_charging = charging
            if self.on_telemetry:
                self.on_telemetry(self.telemetry)

    @staticmethod
    def _get_default_port_for_platform() -> str:
        """Choose reasonable default port based on current operating system."""
        system = platform.system().lower()
        if "linux" in system:
            return "/dev/ttyAMA0"
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
                logger.warning("Error scanning serial ports: %s", e)

        # Ensure platform-specific defaults are present in list
        system = platform.system().lower()
        if "linux" in system:
            for p in ["/dev/ttyAMA0", "/dev/serial0", "/dev/ttyUSB0", "/dev/ttyACM0"]:
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
    def is_mock(self) -> bool:
        return self._is_mock

    @property
    def is_emergency_stopped(self) -> bool:
        with self._motion_lock:
            return self._motion_inhibited

    @property
    def emergency_stop_reason(self) -> str:
        with self._motion_lock:
            return self._motion_inhibit_reason

    def telemetry_health(
        self, *, max_age_s: float = 1.0, now: Optional[float] = None
    ) -> tuple[bool, str]:
        if not self._is_connected:
            return False, "wheels_disconnected"
        if not self._telemetry_received:
            return False, "wheel_telemetry_missing"
        check_time = time.time() if now is None else now
        age_s = max(0.0, check_time - self.telemetry.timestamp)
        if age_s > max_age_s:
            return False, f"wheel_telemetry_stale:{age_s:.3f}s"
        return True, "ok"

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> bool:
        """
        Open serial connection to ESP32 on specified port and baudrate.
        If port fails or is not explicitly set, probes available candidate ports.
        """
        if port:
            self.port = port
        if baudrate:
            self.baudrate = baudrate

        self.disconnect()
        self._telemetry_received = False

        if self.port == "MOCK_SIMULATOR" or not PYSERIAL_AVAILABLE:
            self._is_mock = True
            self._is_connected = True
            self._running = True
            self._reader_thread = threading.Thread(
                target=self._mock_reader_loop, daemon=True, name="WheelsMockReader"
            )
            self._reader_thread.start()
            self._handle_mock_command("STATUS")
            self._emit_log(f"Connected to {self.port} (Mock/Simulated Mode)")
            self._emit_connection_change(True, f"Mock Mode ({self.port})")
            logger.info("WheelsService connected in MOCK_SIMULATOR mode.")
            return True

        # Build candidate ports list to try
        candidate_ports = [self.port]
        for p in self.list_available_ports():
            if p not in candidate_ports and p != "MOCK_SIMULATOR":
                candidate_ports.append(p)

        for candidate in candidate_ports:
            try:
                logger.info("Attempting to connect WheelsService to UART on %s @ %d baud...", candidate, self.baudrate)
                self._serial = serial.Serial(
                    port=candidate,
                    baudrate=self.baudrate,
                    timeout=1.0,
                    write_timeout=1.0,
                )
                self.port = candidate
                self._is_mock = False
                self._is_connected = True
                self._running = True

                self._reader_thread = threading.Thread(
                    target=self._serial_reader_loop, daemon=True, name="WheelsSerialReader"
                )
                self._reader_thread.start()

                self._emit_log(f"Connected to hardware UART: {self.port} @ {self.baudrate} baud")
                self._emit_connection_change(True, f"Connected ({self.port})")
                logger.info("WheelsService successfully connected to ESP32 on %s @ %d baud.", self.port, self.baudrate)

                # Request initial status
                self.request_status()
                return True
            except Exception as e:
                logger.debug("Could not connect WheelsService to port %s: %s", candidate, e)
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                self._serial = None

        logger.warning("WheelsService could not find physical ESP32 UART on candidates %s", candidate_ports)
        self._is_connected = False
        self._serial = None
        self._emit_log(f"Connection failed across candidates: {candidate_ports}")
        self._emit_connection_change(False, "Connection Failed")
        return False

    def disconnect(self) -> None:
        """Safely close serial port and terminate background worker threads."""
        self._stop_continuous_repeat()
        self._running = False

        if self._serial:
            try:
                self._serial.close()
            except Exception as e:
                logger.warning("Error closing serial port: %s", e)
            self._serial = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)
        self._reader_thread = None

        was_connected = self._is_connected
        self._is_connected = False
        self._is_mock = False

        if was_connected:
            self._emit_log("Disconnected from serial port.")
            self._emit_connection_change(False, "Disconnected")

    # ------------------------------------------------------------------
    # Command Dispatchers
    # ------------------------------------------------------------------

    def send_raw(self, line: str) -> bool:
        """Write raw ASCII line over serial port."""
        if not self._is_connected:
            self._emit_log(f"Cannot send '{line}': not connected")
            return False

        line = line.strip()
        data = (line + "\n").encode("utf-8")

        if self._is_mock:
            self._emit_log(f"[TX-MOCK] {line}")
            self._handle_mock_command(line)
            return True

        with self._write_lock:
            if not self._serial or not self._serial.is_open:
                self._emit_log(f"Serial port not open for '{line}'")
                return False
            try:
                self._serial.write(data)
                self._serial.flush()
                self._emit_log(f"[TX] {line}")
                return True
            except Exception as e:
                logger.error("Serial write failed: %s", e)
                self._emit_log(f"[TX ERROR] {e}")
                return False

    def move(self, direction: str) -> bool:
        """Send a single motion command (e.g. 'forward', 'strafeLeft')."""
        if direction not in self.COMMANDS or direction == "stop":
            if direction == "stop":
                return self.stop()
            self._emit_log(f"Rejected unsupported motion command '{direction}'")
            return False
        with self._motion_lock:
            inhibited = self._motion_inhibited
            inhibit_reason = self._motion_inhibit_reason
            if not inhibited:
                self._pulse_generation += 1
        if inhibited:
            self._emit_log(
                f"Motion inhibited ({inhibit_reason}); rejected '{direction}'"
            )
            return False
        return self.send_raw(f"CMD:{direction}")

    def stop(self) -> bool:
        """Normal stop; does not clear or create a latched emergency stop."""
        self._stop_continuous_repeat()
        with self._motion_lock:
            self._pulse_generation += 1
        return self.send_raw("CMD:stop")

    def emergency_stop(self, reason: str = "operator_request") -> bool:
        """Latch motion inhibition locally and in the ESP32 controller."""
        self._stop_continuous_repeat()
        with self._motion_lock:
            self._motion_inhibited = True
            self._motion_inhibit_reason = reason or "unspecified"
            self._pulse_generation += 1
        sent = self.send_raw("ESTOP")
        # Older firmware may not know ESTOP, so always follow with a normal stop.
        stopped = self.send_raw("CMD:stop")
        return sent and stopped

    def clear_emergency_stop(self) -> bool:
        """Explicitly re-arm motion after the caller has verified safe conditions."""
        if not self.send_raw("RESET_ESTOP"):
            return False
        with self._motion_lock:
            self._motion_inhibited = False
            self._motion_inhibit_reason = ""
            self._pulse_generation += 1
        return True

    def set_speed(self, speed: int) -> bool:
        """Update robot motor speed (70-255)."""
        speed = max(70, min(255, int(speed)))
        return self.send_raw(f"SPEED:{speed}")

    def test_motor(self, motor_name: str, direction: int, speed: int = 0) -> bool:
        """
        Diagnostic command to spin an individual motor.
        motor_name: 'fl', 'fr', 'bl', 'br'
        direction: 1 (fwd), -1 (rev), 0 (stop)
        """
        with self._motion_lock:
            inhibited = self._motion_inhibited and direction != 0
            inhibit_reason = self._motion_inhibit_reason
        if inhibited:
            self._emit_log(
                f"Motion inhibited ({inhibit_reason}); rejected motor test"
            )
            return False
        if speed > 0:
            return self.send_raw(f"MOTOR:{motor_name},{direction},{speed}")
        return self.send_raw(f"MOTOR:{motor_name},{direction}")

    def pulse(self, direction: str, duration_ms: int = 250) -> bool:
        """
        Move in a direction for a short duration, then automatically stop.
        Useful for precise step testing.
        """
        if direction not in self.COMMANDS or direction == "stop":
            return False
        with self._motion_lock:
            inhibited = self._motion_inhibited
            inhibit_reason = self._motion_inhibit_reason
            if not inhibited:
                self._pulse_generation += 1
                generation = self._pulse_generation
        if inhibited:
            self._emit_log(
                f"Motion inhibited ({inhibit_reason}); rejected pulse '{direction}'"
            )
            return False

        if not self.send_raw(f"CMD:{direction}"):
            return False

        def _pulse_worker():
            time.sleep(duration_ms / 1000.0)
            with self._motion_lock:
                if generation != self._pulse_generation:
                    return
                self._pulse_generation += 1
                self.send_raw("CMD:stop")

        threading.Thread(target=_pulse_worker, daemon=True, name="WheelsPulseWorker").start()
        return True

    def start_continuous(self, direction: str, interval_ms: int = 200) -> bool:
        """
        Begin continuous movement stream for hold-to-move controls.
        Periodically repeats command to prevent ESP32 700ms timeout.
        """
        with self._continuous_lock:
            self._continuous_command = direction
            if not self.move(direction):
                self._continuous_command = None
                return False

            def _repeat_step():
                with self._continuous_lock:
                    if self._continuous_command == direction and self._is_connected:
                        self.move(direction)
                        self._continuous_timer = threading.Timer(
                            interval_ms / 1000.0, _repeat_step
                        )
                        self._continuous_timer.daemon = True
                        self._continuous_timer.start()

            if self._continuous_timer:
                self._continuous_timer.cancel()
            self._continuous_timer = threading.Timer(interval_ms / 1000.0, _repeat_step)
            self._continuous_timer.daemon = True
            self._continuous_timer.start()
        return True

    def stop_continuous(self) -> None:
        """Stop hold-to-move repeat and stop wheels."""
        self._stop_continuous_repeat()
        self.stop()

    def _stop_continuous_repeat(self) -> None:
        with self._continuous_lock:
            self._continuous_command = None
            if self._continuous_timer:
                self._continuous_timer.cancel()
                self._continuous_timer = None

    def send_ping(self) -> bool:
        """Send PING heartbeat."""
        return self.send_raw("PING")

    def request_status(self) -> bool:
        """Request telemetry snapshot."""
        return self.send_raw("STATUS")

    # ------------------------------------------------------------------
    # Telemetry & Line Parsing
    # ------------------------------------------------------------------

    def _parse_incoming_line(self, line: str) -> None:
        """Process incoming line from ESP32."""
        line = line.strip()
        if not line:
            return

        self._emit_log(f"[RX] {line}")

        if line.startswith("TELEMETRY:"):
            self._parse_telemetry(line[len("TELEMETRY:"):])
        elif line.startswith("ACK:"):
            pass
        elif line == "PONG":
            pass

    def _parse_telemetry(self, payload: str) -> None:
        """
        Parse key-value pairs formatted as:
        front_dist=55,back_dist=58,front_cliff=0,back_cliff=0,motion=STOPPED,speed=180
        """
        try:
            parts = payload.split(",")
            kv = {}
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    kv[k.strip()] = v.strip()

            batt_v = float(kv.get("batt_v", self.telemetry.battery_voltage))
            batt_pct = int(kv.get("batt_pct", self.telemetry.battery_pct))
            motion_state = kv.get("motion", self.telemetry.motion)
            now = time.time()

            # Charging detection
            if "charging" in kv:
                self._is_charging = kv.get("charging") in ("1", "true", "True")
            elif self._charging_sim_override is not None:
                self._is_charging = self._charging_sim_override
            elif batt_v > 0.5:
                # Add sample and discard samples older than 30 seconds
                self._voltage_samples.append((now, batt_v))
                self._voltage_samples = [s for s in self._voltage_samples if now - s[0] <= 30.0]

                if batt_v >= 8.35:
                    self._is_charging = True
                elif len(self._voltage_samples) >= 4 and motion_state == "STOPPED":
                    oldest_t, oldest_v = self._voltage_samples[0]
                    delta_v = batt_v - oldest_v
                    delta_t = now - oldest_t
                    if delta_t >= 3.0:
                        if delta_v >= 0.07:  # +70mV rise or step
                            self._is_charging = True
                        elif delta_v < -0.05 and batt_v < 8.20:
                            self._is_charging = False

            self.telemetry = TelemetryData(
                front_distance_mm=int(kv.get("front_dist", self.telemetry.front_distance_mm)),
                back_distance_mm=int(kv.get("back_dist", self.telemetry.back_distance_mm)),
                front_cliff=kv.get("front_cliff", "0") in ("1", "true", "True"),
                back_cliff=kv.get("back_cliff", "0") in ("1", "true", "True"),
                motion=motion_state,
                speed=int(kv.get("speed", self.telemetry.speed)),
                battery_voltage=batt_v,
                battery_pct=batt_pct,
                is_charging=self._is_charging,
                emergency_stopped=kv.get("estop", "0") in ("1", "true", "True"),
                timestamp=now,
            )
            self._telemetry_received = True

            if self.telemetry.emergency_stopped:
                with self._motion_lock:
                    self._motion_inhibited = True
                    if not self._motion_inhibit_reason:
                        self._motion_inhibit_reason = "controller_reported_estop"

            if self.on_telemetry:
                self.on_telemetry(self.telemetry)
        except Exception as e:
            logger.warning("Failed to parse telemetry '%s': %s", payload, e)

    # ------------------------------------------------------------------
    # Background Worker Loops
    # ------------------------------------------------------------------

    def _serial_reader_loop(self) -> None:
        """Background thread reading lines from physical hardware UART."""
        while self._running and self._serial and self._serial.is_open:
            try:
                line_bytes = self._serial.readline()
                if line_bytes:
                    line = line_bytes.decode("utf-8", errors="replace")
                    self._parse_incoming_line(line)
            except Exception as e:
                if self._running:
                    logger.error("Serial read error: %s", e)
                    self._emit_log(f"[RX ERROR] {e}")
                    time.sleep(0.1)
                break

    def _mock_reader_loop(self) -> None:
        """Background thread simulating periodic telemetry for development."""
        counter = 0
        while self._running and self._is_mock:
            time.sleep(0.25)
            counter += 1
            if counter % 4 == 0:
                # Emit simulated telemetry
                simulated_line = (
                    f"TELEMETRY:front_dist=52,back_dist=55,"
                    f"front_cliff=0,back_cliff=0,"
                    f"motion={self.telemetry.motion},speed={self.telemetry.speed}"
                    f",estop={1 if self._motion_inhibited else 0}"
                )
                self._parse_incoming_line(simulated_line)

    def _handle_mock_command(self, line: str) -> None:
        """Simulate responses for mock mode."""
        if line.startswith("CMD:"):
            cmd = line[4:]
            self.telemetry.motion = cmd.upper()
            self._parse_incoming_line(f"ACK:CMD:{cmd}")
        elif line.startswith("SPEED:"):
            spd = int(line[6:])
            self.telemetry.speed = spd
            self._parse_incoming_line(f"ACK:SPEED:{spd}")
        elif line.startswith("MOTOR:"):
            self._parse_incoming_line(f"ACK:{line}")
        elif line == "PING":
            self._parse_incoming_line("PONG")
        elif line == "STATUS":
            simulated = (
                f"TELEMETRY:front_dist=52,back_dist=55,"
                f"front_cliff=0,back_cliff=0,"
                f"motion={self.telemetry.motion},speed={self.telemetry.speed}"
                f",estop={1 if self._motion_inhibited else 0}"
            )
            self._parse_incoming_line(simulated)
        elif line == "ESTOP":
            self.telemetry.motion = "STOPPED"
            self._parse_incoming_line("ACK:ESTOP")
        elif line == "RESET_ESTOP":
            self._parse_incoming_line("ACK:RESET_ESTOP")

    # ------------------------------------------------------------------
    # Notification Helpers
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


_SHARED_WHEELS_SERVICE: Optional[WheelsService] = None


def get_wheels_service() -> WheelsService:
    """Get or create the global shared WheelsService singleton."""
    global _SHARED_WHEELS_SERVICE
    if _SHARED_WHEELS_SERVICE is None:
        from src.config import config

        _SHARED_WHEELS_SERVICE = WheelsService(
            default_port=config.wheels_port or None,
            default_baudrate=config.wheels_baudrate,
        )
        try:
            _SHARED_WHEELS_SERVICE.connect()
        except Exception as e:
            logger.warning("Failed to auto-connect shared WheelsService: %s", e)
    return _SHARED_WHEELS_SERVICE
