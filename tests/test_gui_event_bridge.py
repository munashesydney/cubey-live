"""Tests for the non-blocking worker-to-Tk event handoff."""

import threading
import time
import unittest

from src.gui.event_bridge import GuiEventBridge


class GuiEventBridgeTests(unittest.TestCase):
    def test_latest_value_events_are_coalesced(self) -> None:
        bridge = GuiEventBridge()

        for value in range(100):
            bridge.post("mic_level", value, latest=True)
        bridge.post("status", "Connecting", latest=True)
        bridge.post("status", "Connected", latest=True)

        events = bridge.drain()
        by_kind = {event.kind: event.payload for event in events}

        self.assertEqual(by_kind["mic_level"], (99,))
        self.assertEqual(by_kind["status"], ("Connected",))
        self.assertEqual(len(events), 2)

    def test_fifo_events_preserve_order(self) -> None:
        bridge = GuiEventBridge()
        bridge.post("log", "one")
        bridge.post("transcript", "User", "two")
        bridge.post("reaction", "happy")

        events = bridge.drain()

        self.assertEqual(
            [(event.kind, event.payload) for event in events],
            [
                ("log", ("one",)),
                ("transcript", ("User", "two")),
                ("reaction", ("happy",)),
            ],
        )

    def test_background_producer_never_waits_for_gui_drain(self) -> None:
        bridge = GuiEventBridge(max_pending=32)
        completed = threading.Event()

        def producer() -> None:
            for index in range(10_000):
                bridge.post("log", str(index))
                bridge.post("mic_level", index, latest=True)
            completed.set()

        started = time.monotonic()
        thread = threading.Thread(target=producer)
        thread.start()
        thread.join(timeout=1)

        self.assertTrue(completed.is_set())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertLessEqual(bridge.pending_count, 33)

        events = bridge.drain(max_events=64)
        self.assertEqual(events[0].kind, "mic_level")
        self.assertEqual(events[0].payload, (9999,))
        self.assertEqual(events[-1].payload, ("9999",))


if __name__ == "__main__":
    unittest.main()
