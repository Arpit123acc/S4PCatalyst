#!/usr/bin/env python3
"""The fetcher's shell guard and the ingest's extractor must agree.

WHY THIS EXISTS
    They did not, and the disagreement was invisible from both ends.

    sapme_fetch decided "is this a usable page or an empty shell" with
    re.sub(r"<[^>]+>", " ", body) -- which strips TAGS but keeps everything
    between them, including the body of every inline <script>. A single-page
    app is mostly inline JavaScript, so its shell measured well over the
    MIN_USEFUL_HTML floor and was written to disk as content.

    sapme_ingest used a real HTMLParser that skips script/style, and got zero
    characters from the identical bytes.

    Result: eleven byte-identical copies of the SAP Discovery Center shell
    (6,244 bytes) stored by the fetcher and silently dropped by the ingest.
    Neither side errored. One believed it had a document, the other believed
    the file was empty, and nothing ever compared them.

WHAT THIS PINS
    The property that matters is not "the parser works" but "there is only one
    parser". A future optimisation that reinstates a regex in the fetcher for
    speed would pass any test written against html_text alone -- so the last
    test here asserts the fetcher uses this exact function object.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import html_to_text as h                                    # noqa: E402
import sapme_fetch as f                                     # noqa: E402

# The shape that defeated the old regex: almost no prose, a lot of script.
SPA_SHELL = (b"<!DOCTYPE html><html><head><meta charset='utf-8'>"
             b"<title>SAP Discovery Center</title>"
             b"<script>" + (b"var x=1;window.__CFG__={a:'b',c:'d'};" * 120) +
             b"</script></head><body><div id='root'></div>"
             b"<script>bootstrapApplication(AppComponent);</script></body></html>")

ARTICLE = (b"<html><head><title>Configure Output Management</title>"
           b"<script>analytics('load');</script></head><body>"
           b"<p>" + (b"This scope item configures output management for billing. " * 40) +
           b"</p></body></html>")


class OneParser(unittest.TestCase):

    def test_the_shell_yields_almost_nothing(self):
        """Script bodies must not count as text. This is the whole defect."""
        self.assertLess(len(h.html_text(SPA_SHELL)), f.MIN_USEFUL_HTML)

    def test_the_old_regex_would_have_passed_it(self):
        """Pin WHY the guard failed, so nobody reintroduces the shortcut.

        If this ever stops being true the regex is no longer dangerous and the
        comment explaining all this can go -- but until then it documents a
        live hazard rather than a historical one.
        """
        import re                                            # noqa: PLC0415
        old = len(re.sub(r"<[^>]+>", " ", SPA_SHELL.decode("utf-8", "replace")))
        self.assertGreater(old, f.MIN_USEFUL_HTML)

    def test_a_real_article_still_passes(self):
        """The fix must not throw away pages that do have prose."""
        self.assertGreater(len(h.html_text(ARTICLE)), f.MIN_USEFUL_HTML)

    def test_script_is_dropped_but_prose_kept(self):
        out = h.html_text(ARTICLE)
        self.assertNotIn("analytics", out)
        self.assertIn("output management", out)

    def test_title_and_head_are_not_counted_as_body_text(self):
        """head is in SKIP: a page whose only text is its <title> is a shell."""
        self.assertEqual(h.html_text(b"<html><head><title>X</title></head>"
                                     b"<body></body></html>"), "")

    def test_accepts_bytes_and_str(self):
        self.assertEqual(h.html_text(b"<p>hi</p>"), h.html_text("<p>hi</p>"))

    def test_never_raises_on_junk(self):
        for junk in (b"", b"\xff\xfe\x00rubbish", b"<p>unclosed", b"<<<>>>"):
            h.html_text(junk)          # must not raise

    # -- the property the whole module exists for ---------------------------

    def test_fetcher_and_ingest_share_the_SAME_function(self):
        """Not 'both behave alike' -- literally one object, so they cannot drift.

        Two implementations that merely agree today is what this codebase keeps
        producing; the copy is always what rots.
        """
        import sapme_ingest as ing                           # noqa: PLC0415
        self.assertIs(f.html_text, h.html_text)
        self.assertIs(ing.html_text, h.html_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
