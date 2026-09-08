"""
Self-hosted usage analytics for the dashboard itself — who's using PRTracker,
not the press releases it catches. Nothing here ever leaves your server;
there's no Google Analytics/GTM involved, just a local event log.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EVENTS_FILE = Path(__file__).parent / "analytics_events.json"


def log_event(event_type: str, user_email: str) -> None:
    events = _load()
    events.append({
        "event": event_type,
        "user": user_email,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    EVENTS_FILE.write_text(json.dumps(events, indent=2))


def _load() -> list[dict]:
    if EVENTS_FILE.exists():
        return json.loads(EVENTS_FILE.read_text())
    return []


def summary() -> dict:
    events = _load()
    views = [e for e in events if e["event"] == "dashboard_view"]
    sport_activations = [e for e in events if e["event"] == "sport_mode_activated"]

    views_per_user = Counter(e["user"] for e in views)
    recent_events = sorted(events, key=lambda e: e["at"], reverse=True)[:10]

    return {
        "total_views": len(views),
        "total_sport_activations": len(sport_activations),
        "views_per_user": dict(views_per_user),
        "recent_events": recent_events,
    }
