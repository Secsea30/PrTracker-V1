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

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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


def _block_heavy_resources(page, blocked_types=_BLOCKED_RESOURCE_TYPES) -> None:
    page.route(
        "**/*",
        lambda route: route.abort() if route.request.resource_type in blocked_types else route.continue_(),
    )


# WAM's server serves its larger static files (main.js ~260KB, styles.css
# ~325KB) very slowly to this server — measured on 24 Sep: single fetches
# taking the full 45s, and 8 parallel fetches of the same file taking
# 7-18s, while small files and the HTML itself came back in ~1s. WAM's
# news list is built client-side by that JavaScript, so a browser waiting
# on those files timed out and a few in a row tripped the health alert
# (twice that day). A page opts in to "lean_load" (tracked_pages.json) to
# get a load path built for that (verified on the server: 4/4 loads OK in
# 16-25s versus timing out at 45s before):
#   - block stylesheets too: ~550KB of the stalled downloads, and not
#     needed to read links and titles;
#   - goto only waits for the response to start ("commit") and the news
#     links appearing is the gate, since "domcontentloaded" itself waits
#     on those slow files;
#   - longer timeouts, since a healthy load here still takes ~20s.
# This is opt-in because it isn't safe everywhere: MBZ's links never count
# as visible without its stylesheets, so lean mode makes every MBZ check
# fail. Both paths get one retry with a fresh browser for a one-off stall;
# worst case for lean is 2 x (45s + 50s) = 190s, inside the scheduler's
# 5-minute watchdog.
_FETCH_ATTEMPTS = 2
_LEAN_BLOCKED_TYPES = _BLOCKED_RESOURCE_TYPES | {"stylesheet"}
_LEAN_GOTO_TIMEOUT_MS = 45_000
_LEAN_SELECTOR_TIMEOUT_MS = 50_000
_STANDARD_GOTO_TIMEOUT_MS = 30_000
_STANDARD_SELECTOR_TIMEOUT_MS = 20_000


def fetch_news_items(url: str, link_pattern: str, lean_load: bool = False) -> list[dict]:
    """Reads a tracked page's list of press releases, retrying once on a timeout."""
    last_error: Exception | None = None
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        try:
            return _fetch_news_items_once(url, link_pattern, lean_load)
        except PlaywrightTimeoutError as e:
            last_error = e
            print(f"WARNING: attempt {attempt}/{_FETCH_ATTEMPTS} timed out loading {url}: {str(e).splitlines()[0]}")
    raise last_error


def _fetch_news_items_once(url: str, link_pattern: str, lean_load: bool) -> list[dict]:
    """Load a tracked page in a headless browser and read its rendered list of press releases.

    Standard path waits only for the DOM itself (wait_until="domcontentloaded"),
    not for the network to go fully idle. MBZ's site renders its news list
    client-side via its own API call after the initial page load, and can
    have ongoing background network activity beyond that — "networkidle"
    doesn't actually wait for the content we care about, only for traffic
    to quiet down in general, and that can hang indefinitely if it never
    fully does (observed in production: checks hanging for minutes, well
    past their intended timeout). The explicit wait_for_selector below is
    the real gate on the content actually being there. See the lean_load
    note above for the slower-server variant.
    """
    if lean_load:
        blocked, wait_until = _LEAN_BLOCKED_TYPES, "commit"
        goto_timeout, selector_timeout = _LEAN_GOTO_TIMEOUT_MS, _LEAN_SELECTOR_TIMEOUT_MS
        # Without stylesheets, Playwright's default "visible" check can fail
        # even though the links are plainly in the page (seen in production
        # on 24 Sep: "locator resolved to 37 elements" 21 times, never
        # visible, so every attempt timed out). All we read is each link's
        # href and title text, so "present in the DOM" is the right gate.
        selector_state = "attached"
    else:
        blocked, wait_until = _BLOCKED_RESOURCE_TYPES, "domcontentloaded"
        goto_timeout, selector_timeout = _STANDARD_GOTO_TIMEOUT_MS, _STANDARD_SELECTOR_TIMEOUT_MS
        selector_state = "visible"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        _block_heavy_resources(page, blocked)
        page.goto(url, wait_until=wait_until, timeout=goto_timeout)

        page.wait_for_selector(f"a[href*='{link_pattern}']", state=selector_state, timeout=selector_timeout)
        cards = page.query_selector_all(f"a[href*='{link_pattern}']")

        items = {}  # keyed by resolved absolute url, to naturally de-duplicate repeated cards
        for card in cards:
            href = card.get_attribute("href")
            heading = card.query_selector("h1, h2, h3, h4")
            element = heading or card
            # inner_text() is empty for anything not rendered as visible;
            # text_content() reads the DOM text regardless (lean mode only —
            # standard mode waited for visibility, so inner_text() is fine).
            raw = (element.text_content() if lean_load else element.inner_text()) or ""
            title = " ".join(raw.split())
            if not href or not title:
                continue
            absolute_url = urljoin(url, href)
            items[absolute_url] = {"title": title, "url": absolute_url}

        browser.close()
        return list(items.values())


# Attempts at reading an article's body before giving up on it (see
# check_one_page). At Sport pace that's roughly 10 minutes of retrying.
_MAX_UNREADABLE_ATTEMPTS = 5


def fetch_article_text(url: str) -> str | None:
    """Grabs the article's own text, for checking a keyword filter against the body.

    Returns None if the page couldn't be loaded at all (timeout, network
    error) — the caller must treat that as "unknown", NOT as "no match",
    or a real article gets skipped for good just because the site was slow
    for a moment. Returns "" if the page loaded but had no <article>.

    Scoped to the <article> element rather than the whole page: the full page
    body also includes site-wide chrome — a "Related"/"Latest News" sidebar,
    a rotating "Breaking" ticker banner showing whatever the current top
    headline is — whose unrelated text can otherwise leak into the keyword
    check and cause false-positive alerts for articles that never actually
    mention it. This has happened twice in production from two different
    elements, so the fallback below deliberately does NOT read the whole
    page: if <article> can't be found even after waiting for it, that's
    treated as no usable text rather than a reason to fall back to
    page-wide content that's known to carry this risk.

    Like the listing page, WAM's article pages are built client-side from
    slow-to-download JavaScript, so this gates on <article> appearing
    ("commit" + wait_for_selector, generous timeouts) rather than on
    "networkidle", which was timing out. Stylesheets are still allowed
    here — unlike the listing — and given a short best-effort wait to
    finish before reading, so text hidden by CSS isn't read as visible.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            _block_heavy_resources(page)
            page.goto(url, wait_until="commit", timeout=45_000)
            try:
                page.wait_for_selector("article", timeout=50_000)
            except PlaywrightTimeoutError:
                # Either the page never rendered (slow site: unknown) or it
                # rendered with no <article> (no usable text). Tell them
                # apart by whether the page got as far as its own DOM.
                loaded = page.evaluate("document.readyState") in ("interactive", "complete")
                if not loaded:
                    print(f"WARNING: {url} did not finish rendering in time — treating the article body as unknown.")
                    browser.close()
                    return None
                print(f"WARNING: no <article> element found on {url} — treating as no body text "
                      f"rather than falling back to the whole page.")
                browser.close()
                return ""
            try:
                page.wait_for_load_state("load", timeout=10_000)
            except PlaywrightTimeoutError:
                pass  # best effort only; the article text is already rendered
            text = page.locator("article").first.inner_text()
            browser.close()
            return text
    except Exception as e:
        print(f"WARNING: could not read article body of {url}: {e}")
        return None


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


def _matches_keyword(item: dict, keyword: str) -> bool | None:
    """Title match is enough on its own; otherwise falls back to checking the article body.

    Returns None when the title didn't match and the body couldn't be read,
    i.e. "can't tell yet" — distinct from a confirmed False."""
    keyword = keyword.lower()
    if keyword in item["title"].lower():
        return True
    body = fetch_article_text(item["url"])
    if body is None:
        return None
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
        items = fetch_news_items(url, link_pattern, lean_load=bool(tracked_page.get("lean_load")))
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

    def _mark_seen(item_url: str) -> None:
        """Persists a single URL as seen right away, rather than batching
        the update until the whole check finishes. If the process gets
        interrupted anywhere after an alert is sent but before state.json
        is saved — a crash, the watchdog killing a slow later step,
        anything — an already-sent alert would otherwise leave no record
        of itself on disk, so the next check re-detects the same URL as
        new and re-alerts. This happened in production: the same WAM
        article got emailed twice, 2 minutes apart — exactly one check
        cycle — because of this exact gap.
        """
        seen.add(item_url)
        page_state["seen_urls"] = list(seen)
        save_state(state)

    if not seen:
        print(f"First run for {label} — recording {len(items)} existing items as the baseline (not alerting on these).")
        for item in items:
            _mark_seen(item["url"])
    elif new_items:
        print(f"\n*** {len(new_items)} NEW ITEM(S) FOUND on {label} ***")
        for item in new_items:
            if keyword_filter:
                matches = _matches_keyword(item, keyword_filter)
                if matches is None:
                    # Couldn't read the article (site slow/unreachable). Marking it
                    # seen here would skip a real match for good, so leave it
                    # unseen and let the next check try again — up to a limit, so
                    # a permanently unreadable page can't be retried forever.
                    attempts = page_state.setdefault("unreadable_attempts", {})
                    n = attempts.get(item["url"], 0) + 1
                    if n < _MAX_UNREADABLE_ATTEMPTS:
                        attempts[item["url"]] = n
                        save_state(state)
                        print(f"- (couldn't read article, will retry next check — attempt {n}/{_MAX_UNREADABLE_ATTEMPTS}) {item['title']}")
                        continue
                    print(f"- (GIVING UP after {n} unreadable attempts, skipping — check by hand) {item['title']}\n  {item['url']}")
                    attempts.pop(item["url"], None)
                    _mark_seen(item["url"])
                    continue
                if not matches:
                    print(f"- (skipped, no match for {keyword_filter!r}) {item['title']}")
                    page_state.get("unreadable_attempts", {}).pop(item["url"], None)
                    _mark_seen(item["url"])
                    continue
                page_state.get("unreadable_attempts", {}).pop(item["url"], None)
            item["detected_at"] = checked_at
            item["page_label"] = label
            item["source_label"] = tracked_page.get("source_label", label)
            item["badge_color"] = tracked_page.get("badge_color", tracked_pages_store.DEFAULT_BADGE_COLOR)
            item["source_url"] = url
            print(f"- {item['title']}\n  {item['url']}")
            screenshot_bytes = capture_screenshot(item["url"])
            sent_at = send_alert(item, screenshot_bytes)
            _mark_seen(item["url"])
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
