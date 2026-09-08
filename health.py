"""
Tracks whether the checker is actually working — what the dashboard's
"Health status" section reads from, and what triggers a "PRTracker is
broken" alert email if checks start failing repeatedly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HEALTH_FILE = Path(__file__).parent / "health.json"

# After this many failed checks in a row, send one alert email (not one per failure).
FAILURE_ALERT_THRESHOLD = 3


def _load() -> dict:
    if HEALTH_FILE.exists():
        return json.loads(HEALTH_FILE.read_text())
    return {
        "last_check_at": None,
        "last_success_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "already_alerted": False,
    }


def _save(state: dict) -> None:
    HEALTH_FILE.write_text(json.dumps(state, indent=2))


def get_health() -> dict:
    return _load()


def record_check(ok: bool, error: str = None) -> dict:
    """Call after every check attempt. Returns the updated health state, plus
    "should_alert": True exactly once when failures just crossed the threshold."""
    state = _load()
    now = datetime.now(timezone.utc).isoformat()
    state["last_check_at"] = now

    should_alert = False

    if ok:
        state["last_success_at"] = now
        state["last_error"] = None
        state["consecutive_failures"] = 0
        state["already_alerted"] = False
    else:
        state["last_error"] = error
        state["consecutive_failures"] += 1
        if state["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD and not state["already_alerted"]:
            should_alert = True
            state["already_alerted"] = True

    _save(state)
    return {**state, "should_alert": should_alert}
