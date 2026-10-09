#!/usr/bin/env python3
"""One-off: copies every alert already in history.json into the Google Sheet,
oldest first. Safe to run more than once — the sheet skips rows it already has.

Run on the server, from the project folder, once SHEET_WEBHOOK_URL and
SHEET_WEBHOOK_TOKEN are set in .env:
    venv/bin/python backfill_sheet.py
"""

import sys

import sheets_log
from history import load_history


def main() -> int:
    if not sheets_log.is_configured():
        print("SHEET_WEBHOOK_URL / SHEET_WEBHOOK_TOKEN are not set in .env — nothing to do.")
        return 1

    rows = [sheets_log.build_row(entry) for entry in reversed(load_history())]  # history is newest-first
    print(f"{len(rows)} past alerts to send.")
    added = skipped = 0
    for start in range(0, len(rows), sheets_log.BATCH_SIZE):
        result = sheets_log.post_rows(rows[start:start + sheets_log.BATCH_SIZE])
        added += result.get("added", 0)
        skipped += result.get("skipped", 0)
        print(f"  sent {min(start + sheets_log.BATCH_SIZE, len(rows))}/{len(rows)}")
    print(f"Done: {added} added, {skipped} already in the sheet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
