"""Thread-safe, non-blocking handoff into Tk's main thread.

Tk widgets, including ``after()``, must only be touched by the thread running
the Tk main loop. Producers from asyncio, PortAudio, serial, and tool workers
post plain Python data here; the GUI periodically drains it on its own thread.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuiEvent:
    kind: str
    payload: tuple[Any, ...]


class GuiEventBridge:
    """Bounded FIFO plus latest-value slots for high-rate GUI signals."""

    def __init__(self, max_pending: int = 2048) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be >= 1")
        self.max_pending = max_pending
        self._pending: deque[GuiEvent] = deque()
        self._latest: dict[str, GuiEvent] = {}
        self._lock = threading.Lock()
        self._dropped = 0

    def post(self, kind: str, *payload: Any, latest: bool = False) -> None:
        """Post without waiting for Tk or running user code."""
        event = GuiEvent(kind=kind, payload=payload)
        dropped = 0
        with self._lock:
            if latest:
                self._latest[kind] = event
                return

            if len(self._pending) >= self.max_pending:
                self._pending.popleft()
                self._dropped += 1
                dropped = self._dropped
            self._pending.append(event)

        if dropped > 0 and (dropped == 1 or dropped % 100 == 0):
            logger.warning("GUI event bridge dropped %d stale event(s)", dropped)

    def drain(self, max_events: int = 256) -> list[GuiEvent]:
        """Return a bounded batch. Call only from Tk's main thread."""
        if max_events < 1:
            raise ValueError("max_events must be >= 1")

        with self._lock:
            # Latest-value signals (status and meter) cannot be starved behind
            # verbose logs. They are emitted once with their freshest value.
            events = list(self._latest.values())
            self._latest.clear()
            remaining = max(0, max_events - len(events))
            for _ in range(min(remaining, len(self._pending))):
                events.append(self._pending.popleft())
        return events

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + len(self._latest)
