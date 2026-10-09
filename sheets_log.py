"""
Adds a row to a Google Sheet for every press-release alert PRTracker sends.

How it works: the sheet has a small Google Apps Script attached
(sheets/apps_script.gs) that exposes a private web address. After an alert
goes out, this module posts that alert's row (date, time, title, source,
link) to that address and the script appends it to the "Alerts" tab.

Safety properties, because a sheet problem must never affect alerting:
- Off by default. Nothing happens unless SHEET_WEBHOOK_URL and
  SHEET_WEBHOOK_TOKEN are set in .env.
- Never raises and never blocks an email: it runs after the alert has been
  sent and recorded, and any failure is just logged.
- Nothing is lost if the sheet is unreachable: each row is written to a small
  local queue (sheet_outbox.json) *before* it is sent, and anything still in
  the queue is retried on every later check (flush_outbox).
- No duplicate rows: the script skips any row whose link is already in the
  sheet, so a retry after a half-finished send, or re-running the backfill,
  is harmless.
- Times are written in Gulf Standard Time, like the rest of the tool.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

import localtime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OUTBOX_FILE = Path(__file__).parent / "sheet_outbox.json"

_TIMEOUT_SECONDS = 20
BATCH_SIZE = 50  # rows per request when catching up on a backlog


def _config() -> tuple[str | None, str | None]:
    return os.environ.get("SHEET_WEBHOOK_URL"), os.environ.get("SHEET_WEBHOOK_TOKEN")


def is_configured() -> bool:
    url, token = _config()
    return bool(url and token)


def build_row(entry: dict) -> dict:
    """Turns one history entry into the row the sheet stores. `entry` is the
    same dict that goes into history.json."""
    sent = localtime.to_local(datetime.fromisoformat(entry["sent_at"]))
    return {
        "date": sent.strftime("%Y-%m-%d"),
        "time": sent.strftime("%H:%M:%S"),
        "title": entry["title"],
        "source": entry.get("source_label") or entry.get("page_label") or "",
        "url": entry["url"],
    }


def post_rows(rows: list[dict]) -> dict:
    """Sends rows to the sheet and returns the script's reply
    ({"ok": true, "added": n, "skipped": n}). Raises if the sheet can't be
    reached or doesn't accept them."""
    url, token = _config()
    payload = json.dumps({"token": token, "rows": rows}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(f"the sheet rejected the rows: {result.get('error', 'unknown reason')}")
    return result


def _load_outbox() -> list[dict]:
    if not OUTBOX_FILE.exists():
        return []
    try:
        rows = json.loads(OUTBOX_FILE.read_text())
        return rows if isinstance(rows, list) else []
    except ValueError:
        # Keep the unreadable file for inspection rather than overwriting it.
        backup = OUTBOX_FILE.with_suffix(".corrupt.json")
        OUTBOX_FILE.replace(backup)
        print(f"WARNING: {OUTBOX_FILE.name} was unreadable; moved to {backup.name}.")
        return []


def _save_outbox(rows: list[dict]) -> None:
    temp = OUTBOX_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(rows, indent=2))
    temp.replace(OUTBOX_FILE)  # atomic: a crash can't leave a half-written queue


def flush_outbox() -> None:
    """Sends everything waiting in the queue, oldest first. Stops quietly at
    the first failure and leaves the rest for next time."""
    if not is_configured():
        return
    try:
        rows = _load_outbox()
        while rows:
            batch = rows[:BATCH_SIZE]
            post_rows(batch)
            rows = rows[len(batch):]
            _save_outbox(rows)
    except Exception as e:
        print(f"WARNING: could not update the Google Sheet yet ({type(e).__name__}: {str(e)[:120]}) — will retry on the next check.")


def log_alert(entry: dict) -> None:
    """Records one sent alert in the sheet. Never raises."""
    if not is_configured():
        return
    try:
        rows = _load_outbox()
        rows.append(build_row(entry))
        _save_outbox(rows)  # queued first, so nothing is lost if the send below fails
    except Exception as e:
        print(f"WARNING: could not queue a Google Sheet row ({type(e).__name__}: {str(e)[:120]}).")
        return
    flush_outbox()
