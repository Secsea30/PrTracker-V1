"""
Checks tracked pages and alerts on any new press release.

Can be run standalone for a one-off check of everything:
    python checker.py

Or imported by scheduler.py, which calls check_one_page() per page on its
own timer (each page can follow the global Comfort/Sport mode, or be
pinned to always-fast via "force_sport" — see tracked_pages.py). A single
bad check never raises — callers get a status dict back instead, so one
failing page doesn't kill the long-running scheduler process.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

import health
import tracked_pages as tracked_pages_store
from history import append_history
from mailer import send_alert, send_health_alert

TARGET_URL = "https://www.mohamedbinzayed.ae/en/latest-news-listing"
STATE_FILE = Path(__file__).parent / "state.json"

# A plain, non-"Headless" browser identity. Some sites (WAM among them)
# serve a stripped-down, link-free version of a page when they detect
# "HeadlessChrome" in the user agent — this is a standard compatibility
# fix, not an attempt to bypass any real security control.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def fetch_news_items(url: str, link_pattern: str) -> list[dict]:
    """Load a tracked page in a headless browser and read its rendered list of press releases."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle", timeout=30_000)

        page.wait_for_selector(f"a[href*='{link_pattern}']", timeout=15_000)
        cards = page.query_selector_all(f"a[href*='{link_pattern}']")

        items = {}  # keyed by resolved absolute url, to naturally de-duplicate repeated cards
        for card in cards:
            href = card.get_attribute("href")
            heading = card.query_selector("h1, h2, h3, h4")
            title = heading.inner_text().strip() if heading else card.inner_text().strip()
            if not href or not title:
                continue
            absolute_url = urljoin(url, href)
            items[absolute_url] = {"title": title, "url": absolute_url}

        browser.close()
        return list(items.values())


def fetch_article_text(url: str) -> str:
    """Grabs the visible text of an article page, for checking a keyword filter against the body."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=30_000)
            text = page.inner_text("body")
            browser.close()
            return text
    except Exception as e:
        print(f"WARNING: could not read article body of {url}: {e}")
        return ""


def capture_screenshot(url: str) -> bytes | None:
    """Screenshots the top of the actual article page, for embedding in the alert email."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=30_000)
            screenshot_bytes = page.screenshot(full_page=False)
            browser.close()
            return screenshot_bytes
    except Exception as e:
        print(f"WARNING: could not capture screenshot of {url}: {e}")
        return None


def _matches_keyword(item: dict, keyword: str) -> bool:
    """Title match is enough on its own; otherwise falls back to checking the article body."""
    keyword = keyword.lower()
    if keyword in item["title"].lower():
        return True
    body = fetch_article_text(item["url"])
    return keyword in body.lower()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"pages": {}}

    state = json.loads(STATE_FILE.read_text())

    # Migrate the old single-page format ({"seen_urls": [...]}) into the
    # per-page format, keyed by tracked-page URL, so upgrading doesn't wipe
    # the baseline and flood everyone with "new" alerts for old articles.
    if "seen_urls" in state:
        state = {"pages": {TARGET_URL: {"seen_urls": state["seen_urls"]}}}
        STATE_FILE.write_text(json.dumps(state, indent=2))

    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _finish(ok: bool, checked_at, new_items: list, error: str = None) -> dict:
    """Records health for this check attempt, sending a health alert email if
    failures just crossed the threshold, then returns the standard result dict."""
    health_state = health.record_check(ok=ok, error=error)
    if health_state["should_alert"]:
        print(f"ALERT: {health_state['consecutive_failures']} failures in a row — sending health alert email.")
        send_health_alert(error, health_state["consecutive_failures"])
    return {"ok": ok, "checked_at": checked_at, "new_items": new_items, "error": error}


def check_one_page(tracked_page: dict) -> dict:
    """Checks a single tracked page and returns {"ok", "new_items", "error"}.
    Loads/saves state.json itself, so this is safe to call independently
    per page on its own schedule."""
    checked_at = datetime.now(timezone.utc)
    label = tracked_page["label"]
    url = tracked_page["url"]
    link_pattern = tracked_page.get("link_pattern") or tracked_pages_store.DEFAULT_LINK_PATTERN
    keyword_filter = tracked_page.get("keyword_filter")
    print(f"[{checked_at.isoformat()}] Checking {label} ({url}) ...")

    state = load_state()
    state.setdefault("pages", {})

    try:
        items = fetch_news_items(url, link_pattern)
    except Exception as e:
        error = f"{label}: {e}"
        print(f"ERROR: check failed for {label}: {e}")
        return _finish(False, checked_at, [], error)

    if not items:
        error = f"{label}: no items found — the page structure may have changed"
        print(f"WARNING: {error}")
        return _finish(False, checked_at, [], error)

    page_state = state["pages"].setdefault(url, {"seen_urls": []})
    seen = set(page_state["seen_urls"])
    new_items = [i for i in items if i["url"] not in seen]
    alerted_items: list[dict] = []

    if not seen:
        print(f"First run for {label} — recording {len(items)} existing items as the baseline (not alerting on these).")
    elif new_items:
        print(f"\n*** {len(new_items)} NEW ITEM(S) FOUND on {label} ***")
        for item in new_items:
            if keyword_filter and not _matches_keyword(item, keyword_filter):
                print(f"- (skipped, no match for {keyword_filter!r}) {item['title']}")
                continue
            item["detected_at"] = checked_at
            item["page_label"] = label
            item["source_label"] = tracked_page.get("source_label", label)
            item["badge_color"] = tracked_page.get("badge_color", tracked_pages_store.DEFAULT_BADGE_COLOR)
            item["source_url"] = url
            print(f"- {item['title']}\n  {item['url']}")
            screenshot_bytes = capture_screenshot(item["url"])
            sent_at = send_alert(item, screenshot_bytes)
            append_history({
                "title": item["title"],
                "url": item["url"],
                "page_label": label,
                "source_label": item["source_label"],
                "badge_color": item["badge_color"],
                "detected_at": checked_at.isoformat(),
                "sent_at": sent_at.isoformat(),
            })
            alerted_items.append(item)
    else:
        print(f"No new items on {label}.")

    # Every item seen this check counts as "seen" going forward, whether or
    # not it matched the keyword filter — otherwise a skipped item would
    # get re-evaluated (and its body re-fetched) on every future check.
    page_state["seen_urls"] = list(seen | {i["url"] for i in items})
    save_state(state)

    return _finish(True, checked_at, alerted_items, None)


def run_check() -> dict:
    """Checks every tracked page, one after another. Used for manual/standalone
    runs (`python checker.py`) — the scheduler checks pages independently instead."""
    all_new_items: list[dict] = []
    any_success = False
    last_error = None

    for tracked_page in tracked_pages_store.load_pages():
        result = check_one_page(tracked_page)
        if result["ok"]:
            any_success = True
            all_new_items.extend(result["new_items"])
        else:
            last_error = result["error"]

    return {"ok": any_success, "new_items": all_new_items, "error": None if any_success else last_error}


if __name__ == "__main__":
    result = run_check()
    sys.exit(0 if result["ok"] else 1)
