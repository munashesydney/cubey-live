"""Execution logic for the read-only current-time tool."""

from datetime import datetime, timezone
from typing import Any


def execute_current_time_tool() -> dict[str, Any]:
    """Return the machine's current local time plus an unambiguous UTC value."""
    local_now = datetime.now().astimezone()
    utc_now = local_now.astimezone(timezone.utc)
    return {
        "status": "current_time",
        "local_time": local_now.isoformat(timespec="seconds"),
        "timezone_name": local_now.tzname() or str(local_now.tzinfo),
        "utc_offset": local_now.strftime("%z"),
        "utc_time": utc_now.isoformat(timespec="seconds"),
        "unix_timestamp": int(utc_now.timestamp()),
    }
