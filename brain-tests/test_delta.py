#!/usr/bin/env python3
"""The refresh delta: what changed, and what the fetch state cannot see.

WHY THIS EXISTS
    sapme_fetch keys its state on URL, so new documents are picked up for free
    and already-fetched ones are skipped forever. That second half is the
    problem: when SAP republishes a document behind an UNCHANGED URL, the
    fetcher skips it and the corpus silently holds the previous release. No
    error, no empty result, just a stale answer that looks current — the same
    shape as every other defect this codebase has had to hunt.

    contentReleaseVersion_ID is the field that moves when the URL does not, so
    the delta's real job is that one case. Everything else it reports is
    context.

WHAT EACH CLASS MEANS FOR THE FETCHER
    new              absent from state -> fetched anyway, no action needed
    url_changed      a new URL, so also absent from state -> no action needed
    content_changed  present in state as "ok" -> MUST be cleared or it is
                     skipped forever. This is the only class that needs --apply.
    retired          gone from the catalogue -> flagged, never deleted, because
                     the corpus is often the only record of how something worked
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sapbp_delta as d                                     # noqa: E402

PREV = [
    {"id": "a", "name": "Test script", "url": "https://x/a.docx",
     "content_release_version": "v1", "download": True},
    {"id": "b", "name": "Test script", "url": "https://x/b.docx",
     "content_release_version": "v1", "download": True},
    {"id": "c", "name": "Forms", "url": "https://x/c.docx",
     "content_release_version": "v1", "download": True},
    {"id": "d", "name": "Withdrawn", "url": "https://x/d.docx",
     "content_release_version": "v1", "download": True},
    {"id": "e", "name": "Locale duplicate", "url": "https://x/e.docx",
     "content_release_version": "v1", "download": False},
]
CUR = [
    dict(PREV[0]),                                              # unchanged
    dict(PREV[1], content_release_version="v2"),                # republished
    dict(PREV[2], url="https://x/c-new.docx"),                  # moved
    {"id": "f", "name": "Brand new", "url": "https://x/f.docx",
     "content_release_version": "v1", "download": True},
    dict(PREV[4]),                                              # still excluded
]                                                               # "d" has vanished


class TestDelta(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self._saved = (d.CUR, d.PREV, d.STATE, d.OUT)
        d.CUR, d.PREV = t / "cur.json", t / "prev.json"
        d.STATE, d.OUT = t / "state.json", t / "delta.json"
        d.CUR.write_text(json.dumps(CUR), encoding="utf-8")
        d.PREV.write_text(json.dumps(PREV), encoding="utf-8")
        d.STATE.write_text(json.dumps(
            {r["url"]: {"status": "ok"} for r in PREV}), encoding="utf-8")
        self.d, _ = d.diff()

    def tearDown(self):
        d.CUR, d.PREV, d.STATE, d.OUT = self._saved
        self.tmp.cleanup()

    def ids(self, key):
        return sorted(r["id"] for r in self.d[key])

    def test_republished_behind_an_unchanged_url_is_caught(self):
        """The case the fetch state is blind to — the reason this exists."""
        self.assertEqual(self.ids("content_changed"), ["b"])

    def test_new_and_moved_documents_are_classified(self):
        self.assertEqual(self.ids("new"), ["f"])
        self.assertEqual(self.ids("url_changed"), ["c"])

    def test_disappearance_is_retired_not_deleted(self):
        self.assertEqual(self.ids("retired"), ["d"])

    def test_rows_excluded_from_the_corpus_are_ignored(self):
        """download:false rows are locale duplicates or other solutions."""
        every = [r["id"] for k in ("new", "content_changed", "url_changed", "retired")
                 for r in self.d[k]]
        self.assertNotIn("e", every)

    def test_unchanged_is_counted_not_listed(self):
        self.assertEqual(self.d["unchanged"], 1)

    def test_apply_clears_only_what_would_otherwise_be_skipped(self):
        """Clearing the whole state would work — and re-download 13,600 files."""
        d.apply_delta(self.d)
        state = json.loads(d.STATE.read_text(encoding="utf-8"))
        self.assertNotIn("https://x/b.docx", state, "republished must be re-fetched")
        self.assertIn("https://x/a.docx", state, "unchanged must stay skipped")
        self.assertIn("https://x/e.docx", state, "excluded rows are not touched")

    def test_first_run_is_explained_not_crashed(self):
        """No previous snapshot is a normal state, not an error."""
        d.PREV.unlink()
        res, why = d.diff()
        self.assertIsNone(res)
        self.assertIn("first run", why)

    def test_missing_current_manifest_is_a_hard_error(self):
        d.CUR.unlink()
        with self.assertRaises(SystemExit):
            d.diff()


if __name__ == "__main__":
    unittest.main(verbosity=2)
