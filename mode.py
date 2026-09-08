"""
Comfort mode / Sport mode.

  - Comfort mode (default): check every 10 minutes.
  - Sport mode: check every 2 minutes. Automatically reverts back to
    Comfort mode after 30 minutes, or immediately if turned off manually.

State is stored in mode_state.json so the scheduler (running continuously)
and any manual toggle (this file's CLI, or later the dashboard) agree on
the current mode.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(__file__).parent / "mode_state.json"

COMFORT_INTERVAL_SECONDS = 10 * 60
SPORT_INTERVAL_SECONDS = 2 * 60
SPORT_MAX_DURATION = timedelta(minutes=30)


def _load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"mode": "comfort", "sport_expires_at": None}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_status() -> dict:
    """Returns the current mode, auto-reverting Sport -> Comfort if the 30-minute window passed."""
    state = _load()

    if state["mode"] == "sport" and state.get("sport_expires_at"):
        expires_at = datetime.fromisoformat(state["sport_expires_at"])
        if datetime.now(timezone.utc) >= expires_at:
            state = {"mode": "comfort", "sport_expires_at": None}
            _save(state)

    interval = SPORT_INTERVAL_SECONDS if state["mode"] == "sport" else COMFORT_INTERVAL_SECONDS
    return {**state, "interval_seconds": interval}


def interval_for(tracked_page: dict) -> int:
    """A tracked page's actual check interval: pinned to Sport-mode speed if
    the page has "force_sport" set (see tracked_pages.py), otherwise
    following the global Comfort/Sport toggle."""
    if tracked_page.get("force_sport"):
        return SPORT_INTERVAL_SECONDS
    return get_status()["interval_seconds"]


def enable_sport_mode() -> dict:
    expires_at = datetime.now(timezone.utc) + SPORT_MAX_DURATION
    state = {"mode": "sport", "sport_expires_at": expires_at.isoformat()}
    _save(state)
    return state


def enable_comfort_mode() -> dict:
    state = {"mode": "comfort", "sport_expires_at": None}
    _save(state)
    return state


if __name__ == "__main__":
    # Stand-in for the dashboard toggle until that's built:
    #   python mode.py sport    -> turn Sport mode on (30-min auto-revert)
    #   python mode.py comfort  -> turn Comfort mode on now
    #   python mode.py          -> show current status
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if arg == "sport":
        state = enable_sport_mode()
        print(f"Sport mode ON — checking every {SPORT_INTERVAL_SECONDS // 60} min, "
              f"auto-reverts at {state['sport_expires_at']}")
    elif arg == "comfort":
        enable_comfort_mode()
        print(f"Comfort mode ON — checking every {COMFORT_INTERVAL_SECONDS // 60} min")
    else:
        status = get_status()
        print(f"Current mode: {status['mode']} (checking every {status['interval_seconds'] // 60} min)")
        if status.get("sport_expires_at"):
            print(f"Sport mode auto-reverts at: {status['sport_expires_at']}")
