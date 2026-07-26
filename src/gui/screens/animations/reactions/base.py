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
    slant_angle: float = 0.0     # Eye slant angle for emotion
    jitter: bool = False         # Rapid micro-shiver displacement
