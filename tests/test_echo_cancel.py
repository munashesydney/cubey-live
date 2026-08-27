"""Tests for fail-closed PipeWire acoustic echo-cancellation routing."""

import subprocess
import unittest
from unittest.mock import patch

from src.audio.echo_cancel import (
    EchoCancellationUnavailable,
    prepare_pipewire_echo_cancellation,
)


class EchoCancellationRoutingTests(unittest.TestCase):
    def test_rejects_non_linux_runtime(self):
        with self.assertRaisesRegex(EchoCancellationUnavailable, "Linux/Pi"):
            prepare_pipewire_echo_cancellation(
                "clean_source",
                "reference_sink",
                system_name="Windows",
                pactl_path="pactl",
            )

    def test_requires_pactl(self):
        with patch("src.audio.echo_cancel.shutil.which", return_value=None):
            with self.assertRaisesRegex(EchoCancellationUnavailable, "pactl"):
                prepare_pipewire_echo_cancellation(
                    "clean_source",
                    "reference_sink",
                    system_name="Linux",
                )

    def test_verifies_endpoints_and_sets_process_routing(self):
        environment = {"PULSE_PROP": "existing=value"}
        successful_query = subprocess.CompletedProcess([], 0, "ready", "")

        with patch(
            "src.audio.echo_cancel.subprocess.run",
            side_effect=[successful_query, successful_query],
        ) as run:
            routing = prepare_pipewire_echo_cancellation(
                "clean_source",
                "reference_sink",
                host_device="pulse",
                environment=environment,
                system_name="Linux",
                pactl_path="/usr/bin/pactl",
            )

        self.assertEqual(routing.source_name, "clean_source")
        self.assertEqual(routing.sink_name, "reference_sink")
        self.assertEqual(routing.host_device, "pulse")
        self.assertEqual(environment["PULSE_SOURCE"], "clean_source")
        self.assertEqual(environment["PULSE_SINK"], "reference_sink")
        self.assertIn("existing=value", environment["PULSE_PROP"])
        self.assertIn("application.name=Cubey", environment["PULSE_PROP"])
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["/usr/bin/pactl", "get-source-volume", "clean_source"],
                ["/usr/bin/pactl", "get-sink-volume", "reference_sink"],
            ],
        )

    def test_missing_endpoint_fails_before_changing_environment(self):
        environment = {}
        missing = subprocess.CompletedProcess([], 1, "", "No such entity")

        with patch("src.audio.echo_cancel.subprocess.run", return_value=missing):
            with self.assertRaisesRegex(
                EchoCancellationUnavailable, "clean_source.*unavailable"
            ):
                prepare_pipewire_echo_cancellation(
                    "clean_source",
                    "reference_sink",
                    environment=environment,
                    system_name="Linux",
                    pactl_path="/usr/bin/pactl",
                )

        self.assertNotIn("PULSE_SOURCE", environment)
        self.assertNotIn("PULSE_SINK", environment)


if __name__ == "__main__":
    unittest.main()
