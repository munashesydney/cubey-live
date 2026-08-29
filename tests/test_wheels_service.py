"""
Unit tests for WheelsService: UART protocol formatting, telemetry parsing,
and mock simulation mode.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from src.services.wheels_service import TelemetryData, WheelsService


class WheelsServiceProtocolTests(unittest.TestCase):
    """Test UART command formatting and telemetry parsing."""

    def setUp(self):
        self.service = WheelsService()
        # Connect in mock mode for unit tests
        self.service.connect(port="MOCK_SIMULATOR")

    def tearDown(self):
        self.service.disconnect()

    def test_mock_connection_lifecycle(self):
        self.assertTrue(self.service.is_connected)
        self.assertTrue(self.service.is_mock)
        healthy, reason = self.service.telemetry_health()
        self.assertTrue(healthy, reason)
        self.service.disconnect()
        self.assertFalse(self.service.is_connected)

    def test_move_command_formatting(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        # Test movement directions
        self.service.move("forward")
        self.assertIn("[TX-MOCK] CMD:forward", sent_lines)

        self.service.move("strafeLeft")
        self.assertIn("[TX-MOCK] CMD:strafeLeft", sent_lines)

        self.service.move("rotateRight")
        self.assertIn("[TX-MOCK] CMD:rotateRight", sent_lines)

    def test_stop_command(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.service.stop()
        self.assertIn("[TX-MOCK] CMD:stop", sent_lines)

    def test_emergency_stop_is_latched_until_explicit_reset(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.assertTrue(self.service.emergency_stop("test_collision"))
        self.assertTrue(self.service.is_emergency_stopped)
        self.assertFalse(self.service.move("forward"))
        self.assertFalse(self.service.test_motor("fl", 1))
        self.assertNotIn("[TX-MOCK] CMD:forward", sent_lines)

        self.assertTrue(self.service.clear_emergency_stop())
        self.assertFalse(self.service.is_emergency_stopped)
        self.assertTrue(self.service.move("forward"))
        self.assertIn("[TX-MOCK] CMD:forward", sent_lines)

    def test_old_pulse_cannot_stop_a_newer_command(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.assertTrue(self.service.pulse("forward", duration_ms=50))
        time.sleep(0.02)
        self.assertTrue(self.service.move("rotateLeft"))
        time.sleep(0.08)

        stop_index = next(
            (i for i, line in enumerate(sent_lines) if line == "[TX-MOCK] CMD:stop"),
            None,
        )
        rotate_index = sent_lines.index("[TX-MOCK] CMD:rotateLeft")
        self.assertTrue(stop_index is None or stop_index < rotate_index)

    def test_speed_command_constrains(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        # In-range speed
        self.service.set_speed(200)
        self.assertIn("[TX-MOCK] SPEED:200", sent_lines)

        # Below min (70)
        self.service.set_speed(30)
        self.assertIn("[TX-MOCK] SPEED:70", sent_lines)

        # Above max (255)
        self.service.set_speed(300)
        self.assertIn("[TX-MOCK] SPEED:255", sent_lines)

    def test_individual_motor_testing(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.service.test_motor("fl", 1)
        self.assertIn("[TX-MOCK] MOTOR:fl,1", sent_lines)

        self.service.test_motor("br", -1, speed=210)
        self.assertIn("[TX-MOCK] MOTOR:br,-1,210", sent_lines)

    def test_telemetry_parsing(self):
        telemetry_events = []
        self.service.on_telemetry = lambda telem: telemetry_events.append(telem)

        raw_line = "TELEMETRY:front_dist=62,back_dist=65,front_cliff=0,back_cliff=1,motion=FORWARD,speed=210,batt_v=7.85,batt_pct=72"
        self.service._parse_incoming_line(raw_line)

        self.assertEqual(len(telemetry_events), 1)
        t = telemetry_events[0]
        self.assertEqual(t.front_distance_mm, 62)
        self.assertEqual(t.back_distance_mm, 65)
        self.assertFalse(t.front_cliff)
        self.assertTrue(t.back_cliff)
        self.assertEqual(t.motion, "FORWARD")
        self.assertEqual(t.speed, 210)
        self.assertEqual(t.battery_voltage, 7.85)
        self.assertEqual(t.battery_pct, 72)

    def test_ping_and_status(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.service.send_ping()
        self.assertIn("[TX-MOCK] PING", sent_lines)

        self.service.request_status()
        self.assertIn("[TX-MOCK] STATUS", sent_lines)

    def test_pulse_movement(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.service.pulse("forward", duration_ms=50)
        time.sleep(0.12)
        self.assertIn("[TX-MOCK] CMD:forward", sent_lines)
        self.assertIn("[TX-MOCK] CMD:stop", sent_lines)

    def test_continuous_movement_and_stop(self):
        sent_lines = []
        self.service._emit_log = lambda text: sent_lines.append(text)

        self.service.start_continuous("rotateLeft", interval_ms=60)
        time.sleep(0.15)
        self.service.stop_continuous()

        rotate_cmds = [line for line in sent_lines if line == "[TX-MOCK] CMD:rotateLeft"]
        self.assertGreaterEqual(len(rotate_cmds), 2)
        self.assertIn("[TX-MOCK] CMD:stop", sent_lines)

    def test_malformed_telemetry_resilience(self):
        # Should not crash on invalid/malformed telemetry lines
        self.service._parse_incoming_line("TELEMETRY:corrupted_data_without_equals")
        self.service._parse_incoming_line("TELEMETRY:front_dist=not_a_number")
        self.service._parse_incoming_line("")
        self.service._parse_incoming_line("   ")


if __name__ == "__main__":
    unittest.main()
