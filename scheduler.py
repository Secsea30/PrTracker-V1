"""
Runs the checker continuously, respecting Comfort mode (10 min) / Sport mode
(2 min, auto-reverts after 30 min) — see mode.py.

Sleeps in short ticks (rather than one long blocking sleep) so that switching
to Sport mode on the dashboard takes effect within a few seconds, not up to
10 minutes later if it happened to switch mid-sleep on the old interval.

This is the process that's meant to run 24/7 once deployed. Stop it with
Ctrl+C during local testing.
"""

import time
from datetime import datetime, timezone

import mode
from checker import run_check

TICK_SECONDS = 5


def main():
    print("PRTracker scheduler starting.")
    last_check_at = None

    while True:
        interval = mode.get_status()["interval_seconds"]
        due = last_check_at is None or (time.monotonic() - last_check_at) >= interval

        if due:
            status = mode.get_status()
            print(f"[{datetime.now(timezone.utc).isoformat()}] Mode: {status['mode']} "
                  f"(checking every {status['interval_seconds'] // 60} min)")
            try:
                run_check()
            except Exception as e:
                # A single bad check should never take the whole scheduler down.
                print(f"ERROR: unexpected failure during check: {e}")
            last_check_at = time.monotonic()

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
