"""
The page(s) PRTracker watches — stored in tracked_pages.json so they can be
managed from the dashboard's Settings page instead of editing code.

Note: new pages need the same news-listing layout as the existing one
(same Sitecore JSS site, another listing page). A genuinely different
site would need its own scraping logic, not just an entry here.
"""

from __future__ import annotations

import json
from pathlib import Path

TRACKED_PAGES_FILE = Path(__file__).parent / "tracked_pages.json"

DEFAULT_PAGES = [
    {"label": "Latest News", "url": "https://www.mohamedbinzayed.ae/en/latest-news-listing"},
]


def load_pages() -> list[dict]:
    if not TRACKED_PAGES_FILE.exists():
        save_pages(DEFAULT_PAGES)
        return list(DEFAULT_PAGES)
    return json.loads(TRACKED_PAGES_FILE.read_text())["pages"]


def save_pages(pages: list[dict]) -> None:
    TRACKED_PAGES_FILE.write_text(json.dumps({"pages": pages}, indent=2))


def add_page(label: str, url: str) -> None:
    pages = load_pages()
    if any(p["url"] == url for p in pages):
        return
    pages.append({"label": label, "url": url})
    save_pages(pages)


def remove_page(url: str) -> None:
    pages = load_pages()
    remaining = [p for p in pages if p["url"] != url]
    if remaining:
        save_pages(remaining)
