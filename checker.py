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
from mailer import send_alert, send_health_alert, send_health_resolved

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

# Images/media/fonts are irrelevant when all we're reading is text and
# hrefs — blocking them cuts a check's peak memory noticeably. This server
# has just under 1GB of RAM total; a single unrestricted Chromium launch
# was observed pushing free memory from ~240MB down to ~60MB and forcing
# the kernel to swap, which is what turned ordinary page loads into the
# multi-minute stalls the watchdog had to kill. With these blocked, ten
# consecutive launches against the real site (measured directly on this
# server) all completed in 3-7s with zero swap activity, versus roughly a
# third succeeding before. Screenshots are the one case that still needs
# real images, so capture_screenshot deliberately doesn't use this.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _block_heavy_resources(page) -> None:
    page.route(
        "**/*",
        lambda route: route.abort() if route.request.resource_type in _BLOCKED_RESOURCE_TYPES else route.continue_(),
    )


def fetch_news_items(url: str, link_pattern: str) -> list[dict]:
    """Load a tracked page in a headless browser and read its rendered list of press releases.

    Waits only for the DOM itself (wait_until="domcontentloaded"), not for
    the network to go fully idle. MBZ's site renders its news list
    client-side via its own API call after the initial page load, and can
    have ongoing background network activity beyond that — "networkidle"
    doesn't actually wait for the content we care about, only for traffic
    to quiet down in general, and that can hang indefinitely if it never
    fully does (observed in production: checks hanging for minutes, well
    past their intended timeout). The explicit wait_for_selector below is
    the real gate on the content actually being there.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        _block_heavy_resources(page)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        page.wait_for_selector(f"a[href*='{link_pattern}']", timeout=20_000)
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
    """Grabs the article's own text, for checking a keyword filter against the body.

    Scoped to the <article> element rather than the whole page: the full page
    body also includes sidebar widgets like "Related" or "Latest News", whose
    unrelated headlines can otherwise leak into the keyword check and cause
    false-positive alerts for articles that never actually mention it.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            _block_heavy_resources(page)
            page.goto(url, wait_until="networkidle", timeout=30_000)
            if page.locator("article").count() > 0:
                text = page.locator("article").first.inner_text()
            else:
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


def _finish(page_url: str, page_label: str, ok: bool, checked_at, new_items: list, error: str = None) -> dict:
    """Records health for this page's check attempt, sending a health alert
    email if that page's failures just crossed the threshold, then returns
    the standard result dict."""
    health_state = health.record_check(page_url, ok=ok, error=error)
    if health_state["should_alert"]:
        print(f"ALERT: {page_label} has failed {health_state['consecutive_failures']} times in a row — sending health alert email.")
        send_health_alert(page_label, error, health_state["consecutive_failures"])
    elif health_state["should_alert_resolved"]:
        print(f"RESOLVED: {page_label} is checking successfully again — sending resolved email.")
        send_health_resolved(page_label)
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
        return _finish(url, label, False, checked_at, [], error)

    if not items:
        error = f"{label}: no items found — the page structure may have changed"
        print(f"WARNING: {error}")
        return _finish(url, label, False, checked_at, [], error)

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

    return _finish(url, label, True, checked_at, alerted_items, None)


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
