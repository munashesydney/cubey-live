"""
Listening reaction animation handler.
Triggered when a wake-up command ("Hey Cubey", "Cubey", etc.) is detected.
Renders perked-up, attentive electric cyan eyes.
"""

from .base import BaseReaction


class ListeningReaction(BaseReaction):
    """Attentive listening reaction triggered by wake words."""

    def __init__(self):
        super().__init__(
            name="listening",
            color="#00F0FF",         # Electric vibrant cyan listening glow
            core_color="#000000",    # Solid black pupil
            duration_ms=3000,        # Initial reaction burst duration
            height_scale_mult=1.22,  # Attentive perked-up height
            width_scale_mult=1.10,   # Wide attentive width
            slant_angle=5.0,         # Inquisitive perked slant
            shape_mode="capsule",
            squish_x=1.05,
            squish_y=1.18,
        )
