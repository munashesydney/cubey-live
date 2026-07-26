"""
Base reaction animation class.
"""

from dataclasses import dataclass

@dataclass
class BaseReaction:
    """Base property container for a visual robot face reaction."""
    name: str = "normal"
    color: str = "#89CFF0"       # Default cyan
    core_color: str = "#E0F7FA"  # Default bright core
    duration_ms: int = 1500      # Reaction active duration
    height_scale_mult: float = 1.0  # Vertical squish or stretch multiplier
