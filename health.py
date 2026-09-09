"""
Tracks whether the checker is actually working — what the dashboard's
"Health status" section reads from, and what triggers a "PRTracker is
broken" alert email if checks start failing repeatedly.

Keyed per tracked page (by its url), not globally. A shared/global counter
would let one broken page hide behind another healthy one — e.g. WAM's
scraper failing repeatedly would keep getting silently reset back to zero
every time MBZ's next check happened to succeed, so the dashboard would
keep showing "Healthy" and the failure-alert email might never fire even
though WAM tracking had been dead for days.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HEALTH_FILE = Path(__file__).parent / "health.json"

# After this many failed checks in a row (for a given page), send one alert
# email for that page (not one per failure).
FAILURE_ALERT_THRESHOLD = 3


def _default_page_state() -> dict:
    return {
        "last_check_at": None,
        "last_success_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "already_alerted": False,
    }


def _load() -> dict:
    if HEALTH_FILE.exists():
        return json.loads(HEALTH_FILE.read_text())
    return {}


def _save(state: dict) -> None:
    HEALTH_FILE.write_text(json.dumps(state, indent=2))


def get_health(page_url: str) -> dict:
    """This one page's health state."""
    return _load().get(page_url, _default_page_state())


def get_all_health() -> dict:
    """Every tracked page's health state, keyed by page url."""
    return _load()


def record_check(page_url: str, ok: bool, error: str = None) -> dict:
    """Call after every check attempt for a page. Returns that page's updated
    health state, plus:
      - "should_alert": True exactly once when its failures just crossed
        the threshold.
      - "should_alert_resolved": True exactly once when a check succeeds
        right after a failure alert had been sent for this page, so a
        follow-up "back to normal" email can go out.
    """
    all_state = _load()
    state = all_state.get(page_url, _default_page_state())
    now = datetime.now(timezone.utc).isoformat()
    state["last_check_at"] = now

    should_alert = False
    should_alert_resolved = False

    if ok:
        should_alert_resolved = state.get("already_alerted", False)
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

    all_state[page_url] = state
    _save(all_state)
    return {**state, "should_alert": should_alert, "should_alert_resolved": should_alert_resolved}
