"""Tests for fail-closed PipeWire acoustic echo-cancellation routing."""

import json
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
                pw_dump_path="pw-dump",
            )

    def test_requires_pw_dump(self):
        with patch("src.audio.echo_cancel.shutil.which", return_value=None):
            with self.assertRaisesRegex(EchoCancellationUnavailable, "pw-dump"):
                prepare_pipewire_echo_cancellation(
                    "clean_source",
                    "reference_sink",
                    system_name="Linux",
                )

    def test_verifies_endpoints_and_sets_process_routing(self):
        environment = {"PULSE_PROP": "existing=value"}
        graph = [
            {"info": {"props": {
                "node.name": "clean_source",
                "media.class": "Audio/Source",
            }}},
            {"info": {"props": {
                "node.name": "reference_sink",
                "media.class": "Audio/Sink",
            }}},
        ]
        successful_query = subprocess.CompletedProcess(
            [], 0, json.dumps(graph), ""
        )

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
                pw_dump_path="/usr/bin/pw-dump",
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
            [["/usr/bin/pw-dump", "--no-colors"]],
        )

    def test_missing_endpoint_fails_before_changing_environment(self):
        environment = {}
        missing = subprocess.CompletedProcess([], 0, "[]", "")

        with patch("src.audio.echo_cancel.subprocess.run", return_value=missing):
            with self.assertRaisesRegex(
                EchoCancellationUnavailable, "clean_source.*unavailable"
            ):
                prepare_pipewire_echo_cancellation(
                    "clean_source",
                    "reference_sink",
                    environment=environment,
                    system_name="Linux",
                    pw_dump_path="/usr/bin/pw-dump",
                )

        self.assertNotIn("PULSE_SOURCE", environment)
        self.assertNotIn("PULSE_SINK", environment)


if __name__ == "__main__":
    unittest.main()
