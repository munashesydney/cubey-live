"""
Unit tests for ListeningReaction, EyeAnimationEngine registration, and side wave visualizer.
"""

import math
import sys
import time
import unittest
from unittest.mock import MagicMock

if "customtkinter" not in sys.modules:
    try:
        import customtkinter
    except ImportError:
        ctk_mock = MagicMock()
        ctk_mock.CTkFrame = object
        sys.modules["customtkinter"] = ctk_mock

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    sys.modules["PIL"] = MagicMock()
    sys.modules["PIL.Image"] = MagicMock()
    sys.modules["PIL.ImageDraw"] = MagicMock()
    sys.modules["PIL.ImageTk"] = MagicMock()

from src.gui.pages.animations.eye_animations import EyeAnimationEngine
from src.gui.pages.animations.reactions import ListeningReaction
from src.gui.pages.robot_face_page import RobotFacePage


class TestListeningAnimation(unittest.TestCase):

    def setUp(self):
        self.redraw_mock = MagicMock()
        self.engine = EyeAnimationEngine(redraw_callback=self.redraw_mock)

    def test_listening_reaction_registered(self):
        """Verify listening reaction exists in engine registry."""
        self.assertIn("listening", self.engine.reactions)
        self.assertIn("wake_up", self.engine.reactions)
        reaction = self.engine.reactions["listening"]
        self.assertIsInstance(reaction, ListeningReaction)
        self.assertEqual(reaction.name, "listening")
        self.assertEqual(reaction.color, "#00F0FF")
        self.assertGreater(reaction.height_scale_mult, 1.0)
        self.assertGreater(reaction.slant_angle, 0.0)

    def test_trigger_listening_reaction(self):
        """Triggering listening reaction sets color and eye parameters."""
        schedule_after_mock = MagicMock()
        self.engine.trigger_reaction("listening", schedule_after_mock)

        self.assertEqual(self.engine.emotion_name, "listening")
        self.assertEqual(self.engine.current_color, "#00F0FF")
        self.assertEqual(self.engine.height_scale_mult, 1.22)
        self.assertEqual(self.engine.target_slant_l, 5.0)
        schedule_after_mock.assert_called_once()

    def test_draw_listening_waves_renders_cleanly(self):
        """Verify _draw_listening_waves renders onto PIL canvas without error."""
        page = RobotFacePage.__new__(RobotFacePage)
        page.listening_intensity = 1.0
        page.current_mic_level = 0.5
        now = time.time()

        if HAS_PIL:
            img = Image.new("RGB", (1104, 631), (10, 10, 15))
            draw = ImageDraw.Draw(img)
            page._draw_listening_waves(draw, 1104, 631, base_scale=1.0, now=now)
            pixels = list(img.getdata())
            has_non_bg = any(p != (10, 10, 15) for p in pixels)
            self.assertTrue(has_non_bg, "Listening waves should draw colored pixels onto canvas")
        else:
            draw_mock = MagicMock()
            page._draw_listening_waves(draw_mock, 1104, 631, base_scale=1.0, now=now)
            self.assertGreater(draw_mock.line.call_count, 0)


if __name__ == "__main__":
    unittest.main()
