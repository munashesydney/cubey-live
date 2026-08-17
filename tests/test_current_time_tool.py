"""Tests for Cubey's read-only current-time AI tool."""

from datetime import datetime
import unittest

from src.client.tools import ToolContext, build_llama_tools, dispatch_tool_call, tool_names_for
from src.client.tools.current_time import execute_current_time_tool


class CurrentTimeToolTests(unittest.TestCase):
    def test_returns_parseable_local_and_utc_times(self) -> None:
        result = execute_current_time_tool()

        self.assertEqual(result["status"], "current_time")
        self.assertTrue(result["timezone_name"])
        self.assertRegex(result["utc_offset"], r"^[+-]\d{4}$")
        self.assertIsNotNone(datetime.fromisoformat(result["local_time"]))
        self.assertIsNotNone(datetime.fromisoformat(result["utc_time"]))

    def test_is_available_to_both_ai_models(self) -> None:
        self.assertIn("current_time", tool_names_for("live_model"))
        self.assertIn("current_time", tool_names_for("local_model"))
        self.assertIn(
            "current_time",
            {tool["function"]["name"] for tool in build_llama_tools("local_model")},
        )

    def test_dispatches_without_model_specific_context(self) -> None:
        result = dispatch_tool_call("current_time", {}, ToolContext())

        self.assertEqual(result["status"], "current_time")
