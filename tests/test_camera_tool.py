"""
Unit tests for the 'camera' tool and policy restrictions.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from src.client.tools.camera.execute import (
    execute_camera_tool,
    stop_active_camera_timer,
)
from src.client.tools.registry import (
    MODEL_TOOL_POLICY,
    TOOL_SCHEMAS,
    ToolContext,
    build_gemini_tool,
    dispatch_tool_call,
    tool_names_for,
)


class TestCameraTool(unittest.TestCase):
    """Test camera tool declaration, policy access, execution, and auto-off timer."""

    def tearDown(self):
        stop_active_camera_timer()

    def test_camera_tool_schema_and_gemini_declaration(self):
        """Camera tool schema should exist and convert to Gemini Tool successfully."""
        self.assertIn("camera", TOOL_SCHEMAS)
        schema = TOOL_SCHEMAS["camera"]
        self.assertEqual(schema["name"], "camera")
        self.assertIn("parameters", schema)

        gemini_tool = build_gemini_tool("camera")
        self.assertIsNotNone(gemini_tool)
        fn_decl = gemini_tool.function_declarations[0]
        self.assertEqual(fn_decl.name, "camera")

    def test_model_tool_policy_restricts_camera_to_live_model_only(self):
        """Only live_model should have access to camera; local_model must not."""
        live_tools = tool_names_for("live_model")
        local_tools = tool_names_for("local_model")
        task_tools = tool_names_for("local_task_runner")

        self.assertIn("camera", live_tools)
        self.assertNotIn("camera", local_tools)
        self.assertNotIn("camera", task_tools)

    def test_execute_camera_turn_on(self):
        """turn_on should toggle camera active and start 30s auto-off timer."""
        mock_toggle = MagicMock(return_value=True)

        result = execute_camera_tool(
            action="turn_on",
            duration_seconds=30,
            on_toggle_camera=mock_toggle,
        )

        mock_toggle.assert_called_once_with(True)
        self.assertEqual(result["status"], "camera_activated")
        self.assertTrue(result["camera_active"])
        self.assertEqual(result["duration_seconds"], 30)
        self.assertIn("automatically turn off in 30 seconds", result["message"])

    def test_execute_camera_turn_off(self):
        """turn_off should deactivate camera and cancel timer."""
        mock_toggle = MagicMock(return_value=False)

        result = execute_camera_tool(
            action="turn_off",
            on_toggle_camera=mock_toggle,
        )

        mock_toggle.assert_called_once_with(False)
        self.assertEqual(result["status"], "camera_deactivated")
        self.assertFalse(result["camera_active"])

    def test_execute_camera_status(self):
        """status action should return current camera state."""
        mock_live_client = MagicMock()
        mock_live_client.is_camera_streaming = True

        result = execute_camera_tool(
            action="status",
            live_client=mock_live_client,
        )

        self.assertEqual(result["status"], "camera_status")
        self.assertTrue(result["camera_active"])

    def test_dispatch_tool_call_routes_camera(self):
        """dispatch_tool_call should properly route camera tool calls."""
        mock_toggle = MagicMock(return_value=True)
        context = ToolContext(on_toggle_camera=mock_toggle)

        result = dispatch_tool_call("camera", {"action": "turn_on"}, context)

        mock_toggle.assert_called_once_with(True)
        self.assertEqual(result["status"], "camera_activated")

    def test_auto_off_timer_expires_and_turns_off_camera(self):
        """Timer expiry should automatically invoke on_toggle_camera(False)."""
        mock_toggle = MagicMock(return_value=True)

        # Set a short test duration of 1 second
        execute_camera_tool(
            action="turn_on",
            duration_seconds=1,
            on_toggle_camera=mock_toggle,
        )
        self.assertEqual(mock_toggle.call_count, 1)
        self.assertEqual(mock_toggle.call_args[0][0], True)

        # Wait for 1-second auto-off timer to trigger
        time.sleep(1.2)

        # Should now have been called with False
        self.assertEqual(mock_toggle.call_count, 2)
        self.assertEqual(mock_toggle.call_args[0][0], False)


if __name__ == "__main__":
    unittest.main()
