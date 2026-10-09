"""
Reads the full text ("body") of a press release, for the Google Sheet log.

Neither site needs a browser for this:
- WAM: the article text comes from WAM's own small data feed (the same one
  checker.py uses for the list and the keyword check).
- MBZ site: article pages are built on the server, and the text is sitting in
  the page's embedded data block (the "__JSS_STATE__" script), so one plain
  request returns it in about a second and a half.

fetch_body() never raises. If the text can't be read it returns "" and logs a
warning, so a body problem can never get in the way of an alert.
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from urllib.parse import quote, unquote, urlparse

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
}
_TIMEOUT_SECONDS = 20
_ATTEMPTS = 2

# Google Sheets caps a single cell at 50,000 characters.
MAX_BODY_CHARS = 45_000
_TRUNCATED_NOTE = "\n\n[Text shortened — open the link for the full release.]"


def html_to_text(fragment: str) -> str:
    """Turns an HTML snippet into plain text, keeping paragraph breaks."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=_HEADERS)
    last_error: Exception | None = None
    for _ in range(_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return response.read()
        except Exception as e:  # network error, HTTP error — try once more, then give up
            last_error = e
    raise last_error


def wam_body(article_url: str) -> str:
    slug = unquote(urlparse(article_url).path.rstrip("/").rsplit("/", 1)[-1])
    raw = _fetch("https://www.wam.ae/api/app/articles/GetArticleBySlug?slug=" + quote(slug, safe=""))
    return html_to_text(json.loads(raw).get("body") or "")


def mbz_body(article_url: str) -> str:
    page = _fetch(article_url).decode("utf-8", errors="replace")
    match = re.search(r'<script[^>]*id="__JSS_STATE__"[^>]*>(.*?)</script>', page, re.S)
    if not match:
        raise ValueError("no embedded article data in the page")
    placeholders = json.loads(match.group(1))["sitecore"]["route"]["placeholders"]

    candidates: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            fields = node.get("fields")
            if isinstance(fields, dict):
                description = fields.get("Description")
                if isinstance(description, dict) and isinstance(description.get("value"), str):
                    candidates.append(description["value"])
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(placeholders)
    texts = [html_to_text(c) for c in candidates]
    # The release text is the longest "Description" on the page (the others are short blurbs).
    return max(texts, key=len, default="")


def fetch_body(article_url: str) -> str:
    """The press release's text, or "" if it can't be read. Never raises."""
    try:
        host = urlparse(article_url).netloc.lower()
        if host.endswith("wam.ae"):
            body = wam_body(article_url)
        elif host.endswith("mohamedbinzayed.ae"):
            body = mbz_body(article_url)
        else:
            return ""
        if not body:
            print(f"WARNING: no text found for {article_url}")
            return ""
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS].rstrip() + _TRUNCATED_NOTE
        return body
    except Exception as e:
        print(f"WARNING: could not read the text of {article_url} ({type(e).__name__}: {str(e)[:100]})")
        return ""
