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

DEFAULT_LINK_PATTERN = "/latest-news-listing/"
DEFAULT_BADGE_COLOR = "#475569"  # neutral slate, used when a page doesn't specify its own

DEFAULT_PAGES = [
    {
        "label": "Latest News",
        "url": "https://www.mohamedbinzayed.ae/en/latest-news-listing",
        "link_pattern": DEFAULT_LINK_PATTERN,
        "keyword_filter": None,
        "source_label": "MBZ Site",
        "badge_color": "#1E3A5F",  # the existing navy — unchanged for this source
    },
]


def load_pages() -> list[dict]:
    if not TRACKED_PAGES_FILE.exists():
        save_pages(DEFAULT_PAGES)
        return list(DEFAULT_PAGES)
    return json.loads(TRACKED_PAGES_FILE.read_text())["pages"]


def save_pages(pages: list[dict]) -> None:
    TRACKED_PAGES_FILE.write_text(json.dumps({"pages": pages}, indent=2))


def add_page(
    label: str,
    url: str,
    link_pattern: str = DEFAULT_LINK_PATTERN,
    keyword_filter: str = None,
    source_label: str = None,
    badge_color: str = DEFAULT_BADGE_COLOR,
    force_sport: bool = False,
) -> None:
    pages = load_pages()
    if any(p["url"] == url for p in pages):
        return
    pages.append({
        "label": label,
        "url": url,
        "link_pattern": link_pattern,
        "keyword_filter": keyword_filter or None,
        "source_label": source_label or label,
        "badge_color": badge_color,
        # If true, this page always checks at the Sport-mode interval,
        # regardless of the dashboard's global Comfort/Sport toggle.
        "force_sport": force_sport,
    })
    save_pages(pages)


def remove_page(url: str) -> None:
    pages = load_pages()
    remaining = [p for p in pages if p["url"] != url]
    if remaining:
        save_pages(remaining)
