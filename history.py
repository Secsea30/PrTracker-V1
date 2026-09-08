"""
Persistent log of every press release PRTracker has ever caught — what the
"Change history" section of the dashboard reads from.
"""

import json
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "history.json"


def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return []


def append_history(entry: dict) -> None:
    """entry = {"title", "url", "detected_at" (iso str), "sent_at" (iso str)}"""
    history = load_history()
    history.insert(0, entry)  # newest first
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
