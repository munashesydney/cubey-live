"""Frame-rate independence tests for the optimized robot face."""

import unittest

from src.gui.pages.animations.eye_animations import EyeAnimationEngine


class EyeAnimationTimingTests(unittest.TestCase):
    @staticmethod
    def _engine() -> EyeAnimationEngine:
        engine = EyeAnimationEngine(redraw_callback=lambda: None)
        # Keep random idle actions out of this deterministic physics test.
        engine._next_action_interval = 10_000
        engine._next_saccade_interval = 10_000
        engine.target_slant_l = 24.0
        engine.target_slant_r = -24.0
        engine.start_gaze_x = 0.0
        engine.target_gaze_x = 40.0
        engine.saccade_t = 0.0
        return engine

    def test_30_fps_physics_matches_60_fps_elapsed_time(self) -> None:
        sixty_fps = self._engine()
        thirty_fps = self._engine()

        for _ in range(10):
            sixty_fps.update_animation_frame(frame_scale=1.0)
        for _ in range(5):
            thirty_fps.update_animation_frame(frame_scale=2.0)

        self.assertAlmostEqual(sixty_fps.slant_left, thirty_fps.slant_left)
        self.assertAlmostEqual(sixty_fps.slant_right, thirty_fps.slant_right)
        self.assertAlmostEqual(sixty_fps.saccade_t, thirty_fps.saccade_t)
        self.assertAlmostEqual(sixty_fps.gaze_x, thirty_fps.gaze_x)


if __name__ == "__main__":
    unittest.main()
