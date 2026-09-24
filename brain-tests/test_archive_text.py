#!/usr/bin/env python3
"""Bundles are documents too, and an Office file must not be mistaken for one.

WHY THIS EXISTS
    The catalogue links .zip bundles, not only single files.
    Two-Tier_ERP_Assets_User_Guides_Templates.zip is 19.7 MB of user guides and
    templates. It reached docx_text -- python-docx -- which raised on a plain
    archive and returned "", so the file was counted as too_short and the whole
    bundle was dropped. No exception surfaced, no line in the log: 19.7 MB of
    content simply never appeared in the corpus.

THE TRAP THIS GUARDS
    .docx, .xlsx and .pptx ARE zips. classify() reads PK magic bytes and cannot
    tell them from an archive, so a naive "if it is a zip, unpack it" branch
    would swallow every Word document whose URL carried no extension -- turning
    one silent loss into a much larger one. Only the internal part names (word/,
    xl/, ppt/) separate them, which is what is_office_package checks.

AND THE OTHER DIRECTION
    An archive is attacker-shaped even when nobody is attacking. The limits are
    asserted here because a decompression bomb is exactly the kind of thing that
    gets "optimised" away later by someone who has never seen one.
"""

import io
import sys
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import sapme_ingest as ing                                  # noqa: E402

PROSE = "This user guide explains two-tier ERP replication. " * 30


def _zip(tmp, members):
    p = Path(tmp) / "bundle.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return p


def _docx_bytes(text):
    """A minimal real .docx, so python-docx actually parses it."""
    try:
        import docx                                          # noqa: PLC0415
    except ImportError:
        return None
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class Archives(unittest.TestCase):

    def setUp(self):
        import tempfile                                      # noqa: PLC0415
        self.tmp = tempfile.mkdtemp()

    # -- the loss this fixes -------------------------------------------------

    def test_a_plain_zip_yields_its_members_text(self):
        z = _zip(self.tmp, [("guide.txt", PROSE), ("readme.txt", "second file")])
        out = ing.archive_text(z)
        self.assertIn("two-tier ERP replication", out)
        self.assertIn("second file", out)

    def test_members_are_named_so_a_hit_is_traceable(self):
        """A retrieval hit inside a bundle must say which document it came from."""
        z = _zip(self.tmp, [("docs/guide.txt", PROSE)])
        self.assertIn("docs/guide.txt", ing.archive_text(z))

    def test_it_clears_the_ingest_threshold(self):
        """The point is not 'some text' but 'enough to be kept'."""
        z = _zip(self.tmp, [("guide.txt", PROSE)])
        self.assertGreater(len(ing.archive_text(z)), ing.MIN_USEFUL_CHARS)

    def test_the_old_path_returned_nothing(self):
        """Pin the failure: python-docx on a plain archive yields ''.

        If this ever stops being true the dispatch could regress unnoticed.
        """
        z = _zip(self.tmp, [("guide.txt", PROSE)])
        self.assertEqual(ing.docx_text(z), "")

    # -- the trap: an Office file is also a zip ------------------------------

    def test_a_docx_is_not_treated_as_an_archive(self):
        blob = _docx_bytes(PROSE)
        if blob is None:
            self.skipTest("python-docx not installed")
        p = Path(self.tmp) / "real.docx"
        p.write_bytes(blob)
        self.assertTrue(ing.is_office_package(p))

    def test_a_plain_zip_is_not_an_office_package(self):
        z = _zip(self.tmp, [("guide.txt", PROSE)])
        self.assertFalse(ing.is_office_package(z))

    def test_office_detection_survives_junk(self):
        p = Path(self.tmp) / "notazip.bin"
        p.write_bytes(b"\x00\x01 not a zip at all")
        self.assertFalse(ing.is_office_package(p))

    # -- limits ---------------------------------------------------------------

    def test_an_oversized_member_is_skipped_not_read(self):
        """Refused on the DECLARED size, before any bytes are decompressed."""
        # The cap has to sit BETWEEN the two members, or the test proves
        # nothing: at 1000 it skipped the 1,500-byte PROSE as well and the
        # empty result looked like a pass for the wrong reason.
        self.assertLess(len(PROSE), 2000)
        big = "x" * 8192
        z = _zip(self.tmp, [("ok.txt", PROSE), ("bomb.txt", big)])
        old = ing.MAX_MEMBER_BYTES
        try:
            ing.MAX_MEMBER_BYTES = 2000
            out = ing.archive_text(z)
        finally:
            ing.MAX_MEMBER_BYTES = old
        self.assertIn("two-tier ERP replication", out)
        self.assertNotIn("xxxx", out)

    def test_the_member_cap_is_reported_not_silent(self):
        z = _zip(self.tmp, [("f%03d.txt" % i, PROSE) for i in range(12)])
        old = ing.MAX_ARCHIVE_MEMBERS
        try:
            ing.MAX_ARCHIVE_MEMBERS = 5
            out = ing.archive_text(z)
        finally:
            ing.MAX_ARCHIVE_MEMBERS = old
        self.assertIn("not read", out)          # truncation must announce itself

    def test_unreadable_archive_returns_empty_not_raises(self):
        p = Path(self.tmp) / "truncated.zip"
        p.write_bytes(b"PK\x03\x04truncated rubbish")
        self.assertEqual(ing.archive_text(p), "")

    def test_nested_archives_are_skipped(self):
        """One level only -- unbounded recursion over untrusted zips is the bomb."""
        inner = _zip(self.tmp, [("deep.txt", PROSE)])
        z = _zip(self.tmp, [("outer.txt", PROSE),
                            ("nested.zip", inner.read_bytes())])
        out = ing.archive_text(z)
        self.assertIn("outer.txt", out)
        self.assertNotIn("deep.txt", out)


class OneDispatch(unittest.TestCase):
    """extract_text is the only router. Copies of it go stale within a day.

    diagnose_zero_text imported the extractor functions but inlined the
    BRANCHES, which reads like reuse. When the .zip branch landed in the
    ingest, the diagnostic kept sending archives to python-docx and reported a
    bundle as broken minutes after the ingest had recovered it. Same shape as
    session_is_live re-deriving classify(), and as the size check that applied
    to one format -- three in one day, this one self-inflicted.
    """

    def setUp(self):
        import tempfile                                      # noqa: PLC0415
        self.tmp = tempfile.mkdtemp()

    def test_zip_by_suffix(self):
        z = _zip(self.tmp, [("g.txt", PROSE)])
        self.assertGreater(len(ing.extract_text(z, None)), ing.MIN_USEFUL_CHARS)

    def test_zip_by_kind_when_the_url_had_no_extension(self):
        """The real case: PK magic, no suffix, so only `kind` identifies it."""
        z = _zip(self.tmp, [("g.txt", PROSE)])
        noext = Path(self.tmp) / "abcd-1234"
        noext.write_bytes(z.read_bytes())
        self.assertGreater(len(ing.extract_text(noext, "zip")), ing.MIN_USEFUL_CHARS)

    def test_office_file_with_no_suffix_still_goes_to_docx(self):
        blob = _docx_bytes(PROSE)
        if blob is None:
            self.skipTest("python-docx not installed")
        noext = Path(self.tmp) / "efgh-5678"
        noext.write_bytes(blob)
        self.assertIn("two-tier ERP", ing.extract_text(noext, "zip"))

    def test_html_falls_through_to_the_shared_parser(self):
        p = Path(self.tmp) / "page.html"
        p.write_bytes(b"<html><body><p>" + PROSE.encode() + b"</p></body></html>")
        self.assertIn("two-tier ERP", ing.extract_text(p, "html"))

    def test_the_diagnostic_calls_it_rather_than_copying_it(self):
        """Pin the fix: no second dispatch anywhere in the diagnostic."""
        src = (REPO / "scripts" / "diagnose_zero_text.py").read_text(encoding="utf-8")
        self.assertIn("ing.extract_text(", src)
        for copied in ("ing.xlsx_text(", "ing.docx_text(", "ing.pdf_text("):
            self.assertNotIn(copied, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
