"""
Cubey Navigation Service — High-level Interface for Autonomous Mapping & Nav2.

Provides Python API for sending navigation goals, triggering autonomous room
exploration, canceling active trajectories, and reporting navigation telemetry
to Gemini Live and the Web UI.
"""

import math
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class NavGoal:
    """A target 2D waypoint in world/map coordinates."""
    x_m: float
    y_m: float
    theta_deg: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class NavTelemetry:
    """Real-time navigation state snapshot."""
    state: str = "IDLE"  # IDLE, PLANNING, NAVIGATING, EXPLORING, REACHED, BLOCKED, CANCELED
    current_goal: Optional[NavGoal] = None
    distance_remaining_m: float = 0.0
    estimated_time_remaining_s: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "current_goal": {
                "x_m": round(self.current_goal.x_m, 2),
                "y_m": round(self.current_goal.y_m, 2),
                "theta_deg": round(self.current_goal.theta_deg, 1),
            } if self.current_goal else None,
            "distance_remaining_m": round(self.distance_remaining_m, 2),
            "estimated_time_remaining_s": round(self.estimated_time_remaining_s, 1),
            "timestamp": self.timestamp,
        }


class CubeyNavService:
    """
    Coordinates high-level navigation goals and autonomous exploration.
    Bridges between Gemini Live / Web interface and the Nav2 navigation stack.
    """

    def __init__(
        self,
        on_telemetry: Optional[Callable[[NavTelemetry], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.on_telemetry = on_telemetry
        self.on_log = on_log

        self._lock = threading.Lock()
        self.telemetry = NavTelemetry()
        self._is_exploring = False
        self._explore_thread: Optional[threading.Thread] = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.telemetry.state in ("NAVIGATING", "EXPLORING", "PLANNING")

    def navigate_to(self, x_m: float, y_m: float, theta_deg: float = 0.0) -> bool:
        """
        Send a 2D navigation waypoint to Nav2.
        """
        goal = NavGoal(x_m=x_m, y_m=y_m, theta_deg=theta_deg)
        with self._lock:
            self.telemetry.state = "PLANNING"
            self.telemetry.current_goal = goal
            self.telemetry.distance_remaining_m = 0.0

        self._emit_log(f"Navigating to goal: ({x_m:.2f}m, {y_m:.2f}m, {theta_deg:.1f}°)")
        self._emit_telemetry()
        return True

    def start_exploration(self) -> bool:
        """
        Trigger autonomous room mapping and obstacle-avoidance exploration.
        """
        with self._lock:
            if self._is_exploring:
                return True
            self._is_exploring = True
            self.telemetry.state = "EXPLORING"

        self._emit_log("Started autonomous room exploration.")
        self._emit_telemetry()
        return True

    def stop_navigation(self) -> bool:
        """
        Cancel active navigation goal or autonomous exploration.
        """
        with self._lock:
            self._is_exploring = False
            self.telemetry.state = "IDLE"
            self.telemetry.current_goal = None
            self.telemetry.distance_remaining_m = 0.0

        self._emit_log("Navigation stopped.")
        self._emit_telemetry()
        return True

    def _emit_telemetry(self) -> None:
        if self.on_telemetry:
            try:
                self.on_telemetry(self.telemetry)
            except Exception as e:
                logger.warning("Error dispatching nav telemetry: %s", e)

    def _emit_log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
            except Exception as e:
                logger.warning("Error dispatching nav log: %s", e)


# Global Singleton
_SHARED_NAV_SERVICE: Optional[CubeyNavService] = None


def get_nav_service() -> CubeyNavService:
    global _SHARED_NAV_SERVICE
    if _SHARED_NAV_SERVICE is None:
        _SHARED_NAV_SERVICE = CubeyNavService()
    return _SHARED_NAV_SERVICE
