from datetime import datetime
from typing import Any


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def calculate_sleep_duration(
    shutdown_at: Any,
    booted_at: Any,
) -> float | None:
    """Return a measured completed sleep interval, or None when unknowable."""
    shutdown = _parse_datetime(shutdown_at)
    boot = _parse_datetime(booted_at)
    if shutdown is None or boot is None:
        return None

    try:
        seconds = (boot - shutdown).total_seconds()
    except TypeError:
        return None

    if seconds < 0:
        return None
    return seconds


def sleep_context_from_state(state: Any) -> dict[str, Any]:
    """Expose only the duration needed for conversation, not local timestamps."""
    if not isinstance(state, dict):
        return {"status": "unavailable"}

    seconds = state.get("last_sleep_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return {"status": "unavailable"}
    if seconds < 0:
        return {"status": "unavailable"}

    return {
        "status": "known",
        "duration_seconds": round(float(seconds), 1),
    }
