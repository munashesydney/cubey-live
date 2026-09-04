"""Tests for the Gemini Live ``add_face`` tool."""

import unittest
from unittest.mock import MagicMock

from src.client.tools import (
    MODEL_TOOL_POLICY,
    TOOL_SCHEMAS,
    ToolContext,
    dispatch_tool_call,
    tool_names_for,
)
from src.client.tools.people.execute import execute_add_face_tool
from src.ai.prompts.gemini_live import SYSTEM_PROMPT


class TestPeopleTool(unittest.TestCase):
    def test_schema_and_live_policy(self):
        self.assertIn("add_face", TOOL_SCHEMAS)
        self.assertEqual(TOOL_SCHEMAS["add_face"]["parameters"]["required"], ["name"])
        self.assertIn("add_face", MODEL_TOOL_POLICY["live_model"])
        self.assertIn("add_face", tool_names_for("live_model"))
        self.assertNotIn("add_face", tool_names_for("local_model"))

    def test_dispatch_saves_pending_enrollment(self):
        face_service = MagicMock()
        face_service.save_pending_enrollment.return_value = (True, "Saved John.")

        result = dispatch_tool_call(
            "add_face",
            {"name": "John"},
            ToolContext(face_recognition_service=face_service),
        )

        self.assertEqual(result["status"], "face_saved")
        self.assertEqual(result["name"], "John")
        face_service.save_pending_enrollment.assert_called_once_with("John")

    def test_tool_reports_when_no_enrollment_is_ready(self):
        face_service = MagicMock()
        face_service.save_pending_enrollment.return_value = (
            False,
            "There is no face enrollment waiting for a name.",
        )

        result = execute_add_face_tool("Sarah", face_service)

        self.assertEqual(result["status"], "save_failed")
        self.assertIn("no face enrollment", result["message"])

    def test_tool_rejects_blank_name(self):
        result = execute_add_face_tool("   ", MagicMock())
        self.assertEqual(result["status"], "validation_error")

    def test_live_prompt_describes_face_enrollment_flow(self):
        self.assertIn("facial recognition is always active", SYSTEM_PROMPT)
        self.assertIn("add_face", SYSTEM_PROMPT)
        self.assertIn("Never guess a person's name", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
