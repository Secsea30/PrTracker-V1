"""Tests for reading a press release's text (release_body.py). No network."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import release_body

WAM_URL = "https://www.wam.ae/en/article/c2n1hx6-uae-president-receives-invitation"
MBZ_URL = "https://www.mohamedbinzayed.ae/en/latest-news-listing/2026/10/UAE-President-and-Syrian-President-discuss"


def mbz_page(*descriptions, state_key="__JSS_STATE__"):
    """A cut-down copy of the real page structure: the text lives in 'Description' fields
    of the page's components, next to short blurbs and unrelated fields."""
    placeholders = {"jss-main": [
        {"fields": {"Headline": {"value": "Title"}, "Teaser": {"value": "<span>Short blurb</span>"}}},
        *[{"fields": {"Description": {"value": d}}} for d in descriptions],
        {"fields": {"SelectedItems": [{"fields": {"CTALink": {"value": {"href": "https://x/y.pdf"}}}}]}},
    ]}
    state = {"sitecore": {"route": {"placeholders": placeholders}}}
    return f'<html><body><nav>Biography Strategic Priorities</nav><script id="{state_key}" type="application/json">{json.dumps(state)}</script></body></html>'.encode()


class HtmlToTextTests(unittest.TestCase):
    def test_keeps_paragraph_breaks_and_strips_tags(self):
        text = release_body.html_to_text('<p style="margin:0"><strong>First</strong> paragraph.</p><p>Second&nbsp;one &amp; more.</p>')
        self.assertEqual(text, "First paragraph.\n\nSecond one & more.")

    def test_line_breaks_and_lists(self):
        self.assertEqual(release_body.html_to_text("Line one<br>Line two"), "Line one\nLine two")
        self.assertEqual(release_body.html_to_text("<ul><li>A</li><li>B</li></ul>"), "• A\n\n• B")

    def test_drops_scripts_and_styles(self):
        self.assertEqual(release_body.html_to_text("<style>p{}</style><p>Hi</p><script>alert(1)</script>"), "Hi")

    def test_collapses_runs_of_blank_lines(self):
        self.assertEqual(release_body.html_to_text("<p>A</p><p></p><p></p><p>B</p>"), "A\n\nB")


class FetchBodyTests(unittest.TestCase):
    def test_wam_text_comes_from_the_article_feed(self):
        feed = json.dumps({"body": "<p>ABU DHABI (WAM) -- Text.</p><p>More.</p>"}).encode()
        with mock.patch.object(release_body, "_fetch", return_value=feed) as fetch:
            self.assertEqual(release_body.fetch_body(WAM_URL), "ABU DHABI (WAM) -- Text.\n\nMore.")
        self.assertIn("GetArticleBySlug?slug=c2n1hx6-uae-president-receives-invitation", fetch.call_args[0][0])

    def test_wam_slug_with_encoded_characters_is_decoded_then_re_encoded(self):
        with mock.patch.object(release_body, "_fetch", return_value=b'{"body": "<p>x</p>"}') as fetch:
            release_body.fetch_body("https://www.wam.ae/en/article/c2-china%E2%80%99s-10000%2B")
        self.assertTrue(fetch.call_args[0][0].endswith("slug=c2-china%E2%80%99s-10000%2B"))

    def test_mbz_text_is_the_longest_description_not_the_blurbs(self):
        page = mbz_page("<p>Too short.</p>", "<p><strong>Real</strong> release text.</p><p>Second paragraph of the release.</p>")
        with mock.patch.object(release_body, "_fetch", return_value=page):
            self.assertEqual(release_body.fetch_body(MBZ_URL), "Real release text.\n\nSecond paragraph of the release.")

    def test_mbz_page_chrome_is_not_included(self):
        with mock.patch.object(release_body, "_fetch", return_value=mbz_page("<p>Only the release.</p>")):
            self.assertNotIn("Biography", release_body.fetch_body(MBZ_URL))

    def test_mbz_page_without_the_data_block_gives_empty_not_an_error(self):
        with mock.patch.object(release_body, "_fetch", return_value=b"<html><body>nothing here</body></html>"):
            self.assertEqual(release_body.fetch_body(MBZ_URL), "")

    def test_mbz_page_with_no_description_gives_empty(self):
        with mock.patch.object(release_body, "_fetch", return_value=mbz_page()):
            self.assertEqual(release_body.fetch_body(MBZ_URL), "")

    def test_network_failure_gives_empty_and_never_raises(self):
        with mock.patch.object(release_body, "_fetch", side_effect=OSError("down")):
            self.assertEqual(release_body.fetch_body(WAM_URL), "")
            self.assertEqual(release_body.fetch_body(MBZ_URL), "")

    def test_bad_json_gives_empty_and_never_raises(self):
        with mock.patch.object(release_body, "_fetch", return_value=b"<html>not json"):
            self.assertEqual(release_body.fetch_body(WAM_URL), "")

    def test_unknown_site_gives_empty_without_a_request(self):
        with mock.patch.object(release_body, "_fetch") as fetch:
            self.assertEqual(release_body.fetch_body("https://example.com/article/1"), "")
        fetch.assert_not_called()

    def test_very_long_text_is_shortened_to_fit_a_sheet_cell(self):
        feed = json.dumps({"body": "<p>" + "word " * 20000 + "</p>"}).encode()
        with mock.patch.object(release_body, "_fetch", return_value=feed):
            body = release_body.fetch_body(WAM_URL)
        self.assertLess(len(body), 50_000)
        self.assertTrue(body.endswith("open the link for the full release.]"))

    def test_retries_once_on_a_network_error(self):
        calls = {"n": 0}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"ok"

        def flaky(request, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("blip")
            return Resp()

        with mock.patch("urllib.request.urlopen", side_effect=flaky):
            self.assertEqual(release_body._fetch("https://www.wam.ae/x"), b"ok")
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
