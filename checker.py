"""
Checks every tracked page and alerts on any new press release.

Can be run standalone for a one-off check:
    python checker.py

Or imported by scheduler.py, which calls run_check() repeatedly on a timer
(Comfort/Sport mode). run_check() never raises for an ordinary failure (e.g.
a site being briefly unreachable) — it returns a status dict instead, so
one bad check doesn't kill the long-running scheduler process.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import health
import tracked_pages as tracked_pages_store
from history import append_history
from mailer import send_alert, send_health_alert

TARGET_URL = "https://www.mohamedbinzayed.ae/en/latest-news-listing"
STATE_FILE = Path(__file__).parent / "state.json"


def fetch_news_items(url: str) -> list[dict]:
    """Load a tracked page in a headless browser and read its rendered list of press releases."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30_000)

        # Each news item is a link wrapping a heading (title) + two text lines (date, location).
        page.wait_for_selector("main a[href*='/latest-news-listing/']", timeout=15_000)
        cards = page.query_selector_all("main a[href*='/latest-news-listing/']")

        items = []
        for card in cards:
            href = card.get_attribute("href")
            heading = card.query_selector("h1, h2, h3, h4")
            title = heading.inner_text().strip() if heading else card.inner_text().strip()
            if not href or not title:
                continue
            items.append({
                "title": title,
                "url": href if href.startswith("http") else f"https://www.mohamedbinzayed.ae{href}",
            })

        browser.close()
        return items


def capture_screenshot(url: str) -> bytes | None:
    """Screenshots the top of the actual article page, for embedding in the alert email."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=30_000)
            screenshot_bytes = page.screenshot(full_page=False)
            browser.close()
            return screenshot_bytes
    except Exception as e:
        print(f"WARNING: could not capture screenshot of {url}: {e}")
        return None


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


def run_check() -> dict:
    """Checks every tracked page. Returns {"ok": bool, "new_items": [...], "error": str|None}.
    "ok" is True as long as at least one page was checked successfully."""
    checked_at = datetime.now(timezone.utc)
    state = load_state()
    state.setdefault("pages", {})

    all_new_items: list[dict] = []
    any_success = False
    last_error = None

    for tracked_page in tracked_pages_store.load_pages():
        label, url = tracked_page["label"], tracked_page["url"]
        print(f"[{checked_at.isoformat()}] Checking {label} ({url}) ...")

        try:
            items = fetch_news_items(url)
        except Exception as e:
            last_error = f"{label}: {e}"
            print(f"ERROR: check failed for {label}: {e}")
            continue

        if not items:
            last_error = f"{label}: no items found — the page structure may have changed"
            print(f"WARNING: {last_error}")
            continue

        any_success = True
        page_state = state["pages"].setdefault(url, {"seen_urls": []})
        seen = set(page_state["seen_urls"])
        new_items = [i for i in items if i["url"] not in seen]

        if not seen:
            print(f"First run for {label} — recording {len(items)} existing items as the baseline (not alerting on these).")
        elif new_items:
            print(f"\n*** {len(new_items)} NEW PRESS RELEASE(S) FOUND on {label} ***")
            for item in new_items:
                item["detected_at"] = checked_at
                item["page_label"] = label
                print(f"- {item['title']}\n  {item['url']}")
                screenshot_bytes = capture_screenshot(item["url"])
                sent_at = send_alert(item, screenshot_bytes)
                append_history({
                    "title": item["title"],
                    "url": item["url"],
                    "page_label": label,
                    "detected_at": checked_at.isoformat(),
                    "sent_at": sent_at.isoformat(),
                })
            all_new_items.extend(new_items)
        else:
            print(f"No new items on {label}.")

        page_state["seen_urls"] = list(seen | {i["url"] for i in items})

    save_state(state)

    return _finish(any_success, checked_at, all_new_items, None if any_success else last_error)


if __name__ == "__main__":
    result = run_check()
    sys.exit(0 if result["ok"] else 1)
