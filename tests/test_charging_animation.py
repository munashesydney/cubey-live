import unittest
from unittest.mock import MagicMock
import time

from src.gui.pages.animations.eye_animations import EyeAnimationEngine
from src.gui.pages.animations.reactions import ChargingReaction, SleepingReaction
from src.services.wheels_service import WheelsService, TelemetryData


class TestChargingAnimation(unittest.TestCase):

    def setUp(self):
        self.redraw_mock = MagicMock()
        self.engine = EyeAnimationEngine(redraw_callback=self.redraw_mock)

    def test_reactions_registered(self):
        """Verify charging and sleeping reactions exist in registry."""
        self.assertIn("charging", self.engine.reactions)
        self.assertIn("sleeping", self.engine.reactions)
        self.assertIsInstance(self.engine.reactions["charging"], ChargingReaction)
        self.assertIsInstance(self.engine.reactions["sleeping"], SleepingReaction)

    def test_sleeping_transition(self):
        """Entering sleeping mode transitions shape to crescent_sleep with breathing."""
        self.engine.set_sleeping(True)
        self.assertTrue(self.engine.is_sleeping)
        self.assertEqual(self.engine.emotion_name, "sleeping")
        self.assertEqual(self.engine.pending_shape_mode, "crescent_sleep")

        # Simulate animation frames
        self.engine.update_animation_frame(frame_scale=1.0)
        self.assertGreater(self.engine.breathing_phase, 0.0)

        # Wake up
        self.engine.set_sleeping(False)
        self.assertFalse(self.engine.is_sleeping)
        self.assertEqual(self.engine.emotion_name, "normal")

    def test_charging_triggers_sleep_and_tracks_pct(self):
        """Setting charging sets is_charging and auto-enters sleeping mode."""
        self.engine.set_charging(True, battery_pct=78)
        self.assertTrue(self.engine.is_charging)
        self.assertEqual(self.engine.battery_pct, 78)
        self.assertTrue(self.engine.is_sleeping)

        # Unplugging wakes up
        self.engine.set_charging(False)
        self.assertFalse(self.engine.is_charging)
        self.assertFalse(self.engine.is_sleeping)

    def test_wheels_service_charging_simulation(self):
        """WheelsService charging simulation toggle propagates to telemetry."""
        service = WheelsService()
        telemetry_received = []
        service.on_telemetry = lambda t: telemetry_received.append(t)

        service.set_charging_simulation(True)
        self.assertTrue(service.telemetry.is_charging)
        self.assertTrue(telemetry_received[-1].is_charging)

        service.set_charging_simulation(False)
        self.assertFalse(service.telemetry.is_charging)
        self.assertFalse(telemetry_received[-1].is_charging)

    def test_wheels_service_voltage_jump_detection(self):
        """Telemetry detects sudden voltage increase as charging state."""
        service = WheelsService()
        
        # Initial discharge sample at resting 7.4V
        service._parse_telemetry("front_dist=50,back_dist=50,motion=STOPPED,speed=180,batt_v=7.40,batt_pct=50")
        time.sleep(0.05)
        # Sensed jump to 7.80V (+400mV step when plugged in)
        service._parse_telemetry("front_dist=50,back_dist=50,motion=STOPPED,speed=180,batt_v=7.80,batt_pct=75")
        
        # Saturated voltage >= 8.35V
        service._parse_telemetry("front_dist=50,back_dist=50,motion=STOPPED,speed=180,batt_v=8.38,batt_pct=98")
        self.assertTrue(service.telemetry.is_charging)


if __name__ == "__main__":
    unittest.main()
