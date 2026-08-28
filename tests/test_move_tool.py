import unittest
from unittest.mock import MagicMock

from src.client.tools.move.executor import execute_move_tool
from src.client.tools.registry import MODEL_TOOL_POLICY, TOOL_SCHEMAS, ToolContext, dispatch_tool_call


class TestMoveTool(unittest.TestCase):
    """Test suite for Cubey's Move tool."""

    def setUp(self):
        self.mock_wheels = MagicMock()
        self.mock_wheels.is_connected = True
        self.mock_wheels.telemetry.speed = 180
        self.mock_wheels.telemetry.battery_pct = 85

    def test_move_tool_schema_and_policy(self):
        """Move tool is registered and only allowed for live_model."""
        self.assertIn("move", TOOL_SCHEMAS)
        schema = TOOL_SCHEMAS["move"]
        self.assertEqual(schema["name"], "move")
        self.assertIn("action", schema["parameters"]["properties"])
        
        actions = schema["parameters"]["properties"]["action"]["enum"]
        self.assertIn("forward", actions)
        self.assertIn("backward", actions)
        self.assertIn("rotate_left", actions)
        self.assertIn("rotate_right", actions)
        self.assertIn("forward_left", actions)
        self.assertIn("forward_right", actions)
        self.assertIn("backward_left", actions)
        self.assertIn("backward_right", actions)
        self.assertIn("stop", actions)

        # Strafe actions must NOT be in the schema enum
        self.assertNotIn("strafe_left", actions)
        self.assertNotIn("strafe_right", actions)
        self.assertNotIn("strafeLeft", actions)
        self.assertNotIn("strafeRight", actions)

        # Policy check: ONLY live_model gets move tool
        self.assertIn("move", MODEL_TOOL_POLICY["live_model"])
        self.assertNotIn("move", MODEL_TOOL_POLICY["local_model"])
        self.assertNotIn("move", MODEL_TOOL_POLICY["local_task_runner"])

    def test_execute_forward(self):
        """Forward pulse movement."""
        res = execute_move_tool(
            action="forward",
            duration_seconds=1.5,
            speed=200,
            wheels_service=self.mock_wheels,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "forward")
        self.mock_wheels.set_speed.assert_called_with(200)
        self.mock_wheels.pulse.assert_called_with("forward", duration_ms=1500)

    def test_execute_backward(self):
        """Backward movement with action normalization."""
        for act in ("backward", "back", "reverse"):
            self.mock_wheels.reset_mock()
            res = execute_move_tool(action=act, wheels_service=self.mock_wheels)
            self.assertEqual(res["status"], "success")
            self.mock_wheels.pulse.assert_called_with("backward", duration_ms=1000)

    def test_execute_rotations(self):
        """Rotation synonyms map to rotateLeft and rotateRight."""
        left_synonyms = ("rotate_left", "rotateleft", "turn_left", "turnleft", "spin_left", "left")
        for syn in left_synonyms:
            self.mock_wheels.reset_mock()
            res = execute_move_tool(action=syn, duration_seconds=0.8, wheels_service=self.mock_wheels)
            self.assertEqual(res["status"], "success")
            self.mock_wheels.pulse.assert_called_with("rotateLeft", duration_ms=800)

        right_synonyms = ("rotate_right", "rotateright", "turn_right", "turnright", "spin_right", "right")
        for syn in right_synonyms:
            self.mock_wheels.reset_mock()
            res = execute_move_tool(action=syn, duration_seconds=0.5, wheels_service=self.mock_wheels)
            self.assertEqual(res["status"], "success")
            self.mock_wheels.pulse.assert_called_with("rotateRight", duration_ms=500)

    def test_execute_diagonals(self):
        """Diagonal motions map to firmware commands."""
        diagonals = {
            "forward_left": "forwardLeft",
            "forward_right": "forwardRight",
            "backward_left": "backwardLeft",
            "backward_right": "backwardRight",
        }
        for act, cmd in diagonals.items():
            self.mock_wheels.reset_mock()
            res = execute_move_tool(action=act, duration_seconds=1.2, wheels_service=self.mock_wheels)
            self.assertEqual(res["status"], "success")
            self.mock_wheels.pulse.assert_called_with(cmd, duration_ms=1200)

    def test_execute_stop(self):
        """Stop action calls wheels_service.stop()."""
        res = execute_move_tool(action="stop", wheels_service=self.mock_wheels)
        self.assertEqual(res["status"], "success")
        self.mock_wheels.stop.assert_called_once()

    def test_strafing_explicitly_disabled(self):
        """Strafing left/right is rejected with an informative error."""
        disabled_actions = (
            "strafe_left",
            "strafeleft",
            "strafe_right",
            "straferight",
            "sideways_left",
            "sideways_right",
            "straddle_left",
            "straddle_right",
        )
        for act in disabled_actions:
            self.mock_wheels.reset_mock()
            res = execute_move_tool(action=act, wheels_service=self.mock_wheels)
            self.assertEqual(res["status"], "disabled_action")
            self.assertIn("disabled on Cubey", res["message"])
            self.mock_wheels.pulse.assert_not_called()

    def test_invalid_action(self):
        """Unknown action returns invalid_action error."""
        res = execute_move_tool(action="teleport", wheels_service=self.mock_wheels)
        self.assertEqual(res["status"], "invalid_action")
        self.mock_wheels.pulse.assert_not_called()

    def test_dispatch_via_registry(self):
        """Registry dispatch correctly routes move tool call."""
        context = ToolContext(wheels_service=self.mock_wheels)
        res = dispatch_tool_call(
            "move",
            {"action": "rotate_right", "duration_seconds": 0.6, "speed": 160},
            context,
        )
        self.assertEqual(res["status"], "success")
        self.mock_wheels.set_speed.assert_called_with(160)
        self.mock_wheels.pulse.assert_called_with("rotateRight", duration_ms=600)


if __name__ == "__main__":
    unittest.main()
