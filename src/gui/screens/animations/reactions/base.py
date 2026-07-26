"""
Base reaction animation class.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class BaseReaction:
    """Base property container for a visual robot face reaction."""
    name: str = "normal"
    color: str = "#FFFFFF"       # Solid pure white eyes
    core_color: str = "#000000"  # Solid black pupil
    duration_ms: int = 1500      # Reaction active duration
    height_scale_mult: float = 1.0  # Vertical scale multiplier
    width_scale_mult: float = 1.0   # Horizontal scale multiplier
    slant_angle: float = 0.0     # Eye slant angle for emotion (degrees)
    shape_mode: str = "capsule"  # "capsule", "crescent_happy", "trapezoid_slant", "wide_oval"
    eyelid_top: float = 0.0      # Top eyelid coverage (0.0 to 1.0)
    eyelid_bottom: float = 0.0   # Bottom eyelid coverage (0.0 to 1.0)
    jitter: bool = False         # Rapid micro-shiver displacement
    squish_x: float = 1.0        # Horizontal stretch/squish multiplier
    squish_y: float = 1.0        # Vertical stretch/squish multiplier
