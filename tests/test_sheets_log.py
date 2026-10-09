"""Tests for the Google Sheet logging (sheets_log.py). No network, no real sheet."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sheets_log

ENTRY = {
    "title": "UAE President receives X",
    "url": "https://www.wam.ae/en/article/abc-x",
    "page_label": "WAM - UAE President",
    "source_label": "WAM",
    "badge_color": "#6D3FA0",
    "detected_at": "2026-10-08T10:37:52+00:00",
    "sent_at": "2026-10-08T10:38:17.756506+00:00",
}


class SheetsLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.outbox = Path(self.dir.name) / "sheet_outbox.json"
        patches = [
            mock.patch.object(sheets_log, "OUTBOX_FILE", self.outbox),
            mock.patch.dict(os.environ, {"SHEET_WEBHOOK_URL": "https://example.test/exec", "SHEET_WEBHOOK_TOKEN": "tok"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)
        self.posted = []

    def _post_ok(self, rows):
        self.posted.append(rows)
        return {"ok": True, "added": len(rows), "skipped": 0}

    def test_row_is_in_gulf_time_and_has_the_right_fields(self):
        row = sheets_log.build_row(ENTRY)
        # 10:38:17 UTC is 14:38:17 in Gulf Standard Time (UTC+4)
        self.assertEqual(row, {"date": "2026-10-08", "time": "14:38:17", "title": ENTRY["title"],
                               "source": "WAM", "url": ENTRY["url"], "body": ""})

    def test_row_carries_the_press_release_text(self):
        self.assertEqual(sheets_log.build_row(ENTRY, "Full text.\n\nSecond paragraph.")["body"], "Full text.\n\nSecond paragraph.")

    def test_log_alert_sends_the_text_with_the_row(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.log_alert(ENTRY, body="Full text.")
        self.assertEqual(self.posted[0][0]["body"], "Full text.")

    def test_the_text_survives_the_retry_queue(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=OSError("down")):
            sheets_log.log_alert(ENTRY, body="Full text.")
        self.assertEqual(json.loads(self.outbox.read_text())[0]["body"], "Full text.")
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.flush_outbox()
        self.assertEqual(self.posted[0][0]["body"], "Full text.")

    def test_date_rolls_over_at_gulf_midnight(self):
        row = sheets_log.build_row({**ENTRY, "sent_at": "2026-10-08T21:30:00+00:00"})  # 01:30 GST next day
        self.assertEqual((row["date"], row["time"]), ("2026-10-09", "01:30:00"))

    def test_a_missing_source_is_worked_out_from_the_link(self):
        wam = {**ENTRY, "source_label": "", "page_label": "WAM - UAE President"}
        self.assertEqual(sheets_log.build_row(wam)["source"], "WAM")
        old_mbz = {**ENTRY, "source_label": None, "page_label": "Latest News",
                   "url": "https://www.mohamedbinzayed.ae/en/latest-news-listing/2026/09/x"}
        self.assertEqual(sheets_log.build_row(old_mbz)["source"], "MBZ Site")

    def test_a_stored_source_name_wins(self):
        self.assertEqual(sheets_log.build_row({**ENTRY, "source_label": "Custom"})["source"], "Custom")

    def test_an_unknown_site_falls_back_to_the_page_name(self):
        row = sheets_log.build_row({**ENTRY, "source_label": "", "url": "https://example.com/a", "page_label": "Other page"})
        self.assertEqual(row["source"], "Other page")

    def test_does_nothing_when_not_configured(self):
        with mock.patch.dict(os.environ, {"SHEET_WEBHOOK_URL": "", "SHEET_WEBHOOK_TOKEN": ""}):
            with mock.patch.object(sheets_log, "post_rows") as post:
                sheets_log.log_alert(ENTRY)
                sheets_log.flush_outbox()
        post.assert_not_called()
        self.assertFalse(self.outbox.exists())

    def test_success_sends_the_row_and_leaves_the_queue_empty(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.log_alert(ENTRY)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0][0]["url"], ENTRY["url"])
        self.assertEqual(json.loads(self.outbox.read_text()), [])

    def test_failure_never_raises_and_keeps_the_row_for_retry(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=OSError("network down")):
            sheets_log.log_alert(ENTRY)  # must not raise
        queued = json.loads(self.outbox.read_text())
        self.assertEqual([r["url"] for r in queued], [ENTRY["url"]])

    def test_queued_rows_go_out_in_order_on_the_next_flush(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=OSError("down")):
            sheets_log.log_alert(ENTRY)
            sheets_log.log_alert({**ENTRY, "url": "https://www.wam.ae/en/article/second", "title": "Second"})
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.flush_outbox()
        sent = [r["url"] for batch in self.posted for r in batch]
        self.assertEqual(sent, [ENTRY["url"], "https://www.wam.ae/en/article/second"])
        self.assertEqual(json.loads(self.outbox.read_text()), [])

    def test_a_new_alert_also_delivers_the_backlog(self):
        with mock.patch.object(sheets_log, "post_rows", side_effect=OSError("down")):
            sheets_log.log_alert(ENTRY)
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.log_alert({**ENTRY, "url": "https://www.wam.ae/en/article/second"})
        self.assertEqual(sum(len(b) for b in self.posted), 2)

    def test_a_failure_part_way_keeps_only_the_unsent_rows(self):
        rows = [{**sheets_log.build_row(ENTRY), "url": f"https://x/{i}"} for i in range(5)]
        self.outbox.write_text(json.dumps(rows))
        calls = {"n": 0}

        def flaky(batch):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("dropped")
            self.posted.append(batch)
            return {"ok": True}

        with mock.patch.object(sheets_log, "BATCH_SIZE", 2), mock.patch.object(sheets_log, "post_rows", side_effect=flaky):
            sheets_log.flush_outbox()
        self.assertEqual([r["url"] for r in json.loads(self.outbox.read_text())], ["https://x/2", "https://x/3", "https://x/4"])

    def test_a_corrupt_queue_is_set_aside_not_overwritten(self):
        self.outbox.write_text("{not json")
        with mock.patch.object(sheets_log, "post_rows", side_effect=self._post_ok):
            sheets_log.log_alert(ENTRY)
        self.assertEqual(self.posted[0][0]["url"], ENTRY["url"])
        self.assertTrue(self.outbox.with_suffix(".corrupt.json").exists())

    def test_the_request_carries_the_token_and_rows(self):
        captured = {}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true, "added": 1, "skipped": 0}'

        def fake_urlopen(request, timeout):
            captured["url"], captured["body"], captured["timeout"] = request.full_url, json.loads(request.data), timeout
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = sheets_log.post_rows([sheets_log.build_row(ENTRY)])
        self.assertEqual(captured["url"], "https://example.test/exec")
        self.assertEqual(captured["body"]["token"], "tok")
        self.assertEqual(captured["body"]["rows"][0]["title"], ENTRY["title"])
        self.assertEqual(result["added"], 1)

    def test_a_rejected_post_counts_as_a_failure(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": false, "error": "unauthorized"}'

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(RuntimeError):
                sheets_log.post_rows([sheets_log.build_row(ENTRY)])
            sheets_log.log_alert(ENTRY)  # and the row must stay queued
        self.assertEqual(len(json.loads(self.outbox.read_text())), 1)


if __name__ == "__main__":
    unittest.main()
