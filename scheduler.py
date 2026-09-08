"""
Runs the checker continuously — each tracked page on its own timer.

By default a page follows the dashboard's global Comfort mode (10 min) /
Sport mode (2 min, auto-reverts after 30 min) toggle — see mode.py. A page
can instead be pinned to always check at the Sport-mode interval regardless
of the global toggle, via "force_sport": true in tracked_pages.json (set
from the Settings page isn't exposed yet — ask Claude to set it directly).

Sleeps in short ticks (rather than one long blocking sleep per page) so that
switching modes on the dashboard takes effect within a few seconds, not up
to a full interval later if it happened to switch mid-sleep.

This is the process that's meant to run 24/7 once deployed. Stop it with
Ctrl+C during local testing.
"""

import time
from datetime import datetime, timezone

import mode
import tracked_pages as tracked_pages_store
from checker import check_one_page

TICK_SECONDS = 5


def _interval_for(tracked_page: dict) -> int:
    if tracked_page.get("force_sport"):
        return mode.SPORT_INTERVAL_SECONDS
    return mode.get_status()["interval_seconds"]


def main():
    print("PRTracker scheduler starting.")
    last_checked_at: dict[str, float] = {}  # keyed by page url

    while True:
        now = time.monotonic()

        for tracked_page in tracked_pages_store.load_pages():
            url = tracked_page["url"]
            interval = _interval_for(tracked_page)
            last = last_checked_at.get(url)
            due = last is None or (now - last) >= interval

            if due:
                pace = "forced Sport" if tracked_page.get("force_sport") else mode.get_status()["mode"]
                print(f"[{datetime.now(timezone.utc).isoformat()}] {tracked_page['label']} "
                      f"is due (pace: {pace}, every {interval // 60} min)")
                try:
                    check_one_page(tracked_page)
                except Exception as e:
                    # A single bad check should never take the whole scheduler down.
                    print(f"ERROR: unexpected failure checking {tracked_page['label']}: {e}")
                last_checked_at[url] = time.monotonic()

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
