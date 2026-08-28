"""
Unit tests for ListeningReaction, EyeAnimationEngine registration, and top HUD listening visualizer.
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

    def test_draw_listening_hud_renders_cleanly(self):
        """Verify _draw_listening_hud renders onto PIL canvas without error."""
        page = RobotFacePage.__new__(RobotFacePage)
        page.listening_intensity = 1.0
        page.current_mic_level = 0.5
        page.is_charging = False
        page.animation_engine = self.engine
        now = time.time()

        if HAS_PIL:
            img = Image.new("RGB", (1104, 631), (10, 10, 15))
            draw = ImageDraw.Draw(img)
            page._draw_listening_hud(draw, 1104, 631, base_scale=1.0, now=now)
            pixels = list(img.getdata()) if hasattr(img, "getdata") else []
            has_non_bg = any(p != (10, 10, 15) for p in pixels)
            self.assertTrue(has_non_bg, "Listening HUD should draw colored pixels onto canvas")
        else:
            draw_mock = MagicMock()
            page._draw_listening_hud(draw_mock, 1104, 631, base_scale=1.0, now=now)
            self.assertGreater(draw_mock.rounded_rectangle.call_count, 0)

    def test_draw_mic_glyph_renders_cleanly(self):
        """Verify vector _draw_mic_glyph renders without error."""
        page = RobotFacePage.__new__(RobotFacePage)
        now = time.time()

        if HAS_PIL:
            img = Image.new("RGB", (200, 200), (10, 10, 15))
            draw = ImageDraw.Draw(img)
            page._draw_mic_glyph(draw, 100, 100, 24.0, (0, 240, 255), now)
            pixels = list(img.getdata()) if hasattr(img, "getdata") else []
            has_non_bg = any(p != (10, 10, 15) for p in pixels)
            self.assertTrue(has_non_bg, "Microphone glyph should draw colored pixels")
        else:
            draw_mock = MagicMock()
            page._draw_mic_glyph(draw_mock, 100, 100, 24.0, (0, 240, 255), now)
            self.assertGreater(draw_mock.rounded_rectangle.call_count, 0)

    def test_draw_listening_waves_alias(self):
        """Verify _draw_listening_waves backwards-compatible alias forwards to _draw_listening_hud."""
        page = RobotFacePage.__new__(RobotFacePage)
        page.listening_intensity = 1.0
        page.current_mic_level = 0.5
        page._draw_listening_hud = MagicMock()
        draw_mock = MagicMock()
        now = time.time()

        page._draw_listening_waves(draw_mock, 1104, 631, 1.0, now)
        page._draw_listening_hud.assert_called_once_with(draw_mock, 1104, 631, 1.0, now)


if __name__ == "__main__":
    unittest.main()

