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

Each check runs as its own subprocess (run_one_check.py), with a hard
wall-clock timeout enforced from out here. This isn't just isolation for
its own sake: a Playwright/Chromium hang can outlast its own internal
`timeout=` — this happened for real in production, a check hung for ~11
hours with systemd still reporting the process "active" the whole time,
because it never actually crashed, just got stuck. systemd's
Restart=always only helps once a process exits; it can't rescue one that's
alive but wedged. Running each check out-of-process lets this loop kill
it outright (the whole process group, so any orphaned Chromium goes with
it) and move on, instead of the scheduler itself getting stuck.

This is the process that's meant to run 24/7 once deployed. Stop it with
Ctrl+C during local testing.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import health
import mode
import tracked_pages as tracked_pages_store
from mailer import send_health_alert

TICK_SECONDS = 5

# Generous enough for a legitimate backlog burst (observed: a 10-item WAM
# backlog, each with its own keyword-filter body-fetch, cleared in well
# under a minute) — but bounds a genuine hang to a fixed recovery window
# instead of the indefinite one we hit in production.
CHECK_TIMEOUT_SECONDS = 5 * 60

RUN_ONE_CHECK_SCRIPT = Path(__file__).parent / "run_one_check.py"


def _run_check_with_watchdog(tracked_page: dict) -> None:
    """Runs one page's check in its own process, killing it (and anything
    it spawned) if it doesn't finish within CHECK_TIMEOUT_SECONDS."""
    cmd = [sys.executable, str(RUN_ONE_CHECK_SCRIPT), json.dumps(tracked_page)]
    process = subprocess.Popen(cmd, cwd=Path(__file__).parent, start_new_session=True)

    try:
        process.wait(timeout=CHECK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print(f"WATCHDOG: {tracked_page['label']} check exceeded {CHECK_TIMEOUT_SECONDS}s — "
              f"killing it (and any Chromium it spawned).")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
        process.wait()
        error = f"{tracked_page['label']}: check hung past {CHECK_TIMEOUT_SECONDS}s and was killed"
        # The subprocess died before it could record this (or send an alert) itself.
        health_state = health.record_check(tracked_page["url"], ok=False, error=error)
        if health_state["should_alert"]:
            print(f"ALERT: {tracked_page['label']} has failed {health_state['consecutive_failures']} "
                  f"times in a row — sending health alert email.")
            send_health_alert(tracked_page["label"], error, health_state["consecutive_failures"])


def main():
    print("PRTracker scheduler starting.")
    last_checked_at: dict[str, float] = {}  # keyed by page url

    while True:
        now = time.monotonic()

        for tracked_page in tracked_pages_store.load_pages():
            url = tracked_page["url"]
            interval = mode.interval_for(tracked_page)
            last = last_checked_at.get(url)
            due = last is None or (now - last) >= interval

            if due:
                pace = "forced Sport" if tracked_page.get("force_sport") else mode.get_status()["mode"]
                print(f"[{datetime.now(timezone.utc).isoformat()}] {tracked_page['label']} "
                      f"is due (pace: {pace}, every {interval // 60} min)")
                try:
                    _run_check_with_watchdog(tracked_page)
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
