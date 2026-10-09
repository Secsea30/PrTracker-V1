#!/usr/bin/env python3
"""One-off: copies every alert already in history.json into the Google Sheet,
oldest first, including each press release's text. Safe to run more than once —
the sheet skips rows it already has, and only fills in a Body that is still empty
(so re-running repairs any rows whose text couldn't be read the first time).

Run on the server, from the project folder, once SHEET_WEBHOOK_URL and
SHEET_WEBHOOK_TOKEN are set in .env:
    venv/bin/python backfill_sheet.py
"""

import sys
from concurrent.futures import ThreadPoolExecutor

import release_body
import sheets_log
from history import load_history


def main() -> int:
    if not sheets_log.is_configured():
        print("SHEET_WEBHOOK_URL / SHEET_WEBHOOK_TOKEN are not set in .env — nothing to do.")
        return 1

    entries = list(reversed(load_history()))  # history is newest-first; the sheet wants oldest-first
    print(f"Reading the text of {len(entries)} past releases...")
    with ThreadPoolExecutor(4) as pool:
        bodies = list(pool.map(lambda e: release_body.fetch_body(e["url"]), entries))
    rows = [sheets_log.build_row(e, b) for e, b in zip(entries, bodies)]
    print(f"{len(rows)} past alerts to send ({sum(1 for b in bodies if not b)} without a readable text).")
    added = skipped = filled = 0
    for start in range(0, len(rows), sheets_log.BATCH_SIZE):
        result = sheets_log.post_rows(rows[start:start + sheets_log.BATCH_SIZE])
        added += result.get("added", 0)
        skipped += result.get("skipped", 0)
        filled += result.get("filled", 0)
        print(f"  sent {min(start + sheets_log.BATCH_SIZE, len(rows))}/{len(rows)}")
    print(f"Done: {added} added, {filled} empty texts filled in, {skipped} already complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
