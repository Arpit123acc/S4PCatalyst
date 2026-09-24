#!/usr/bin/env python3
"""The pre-flight session check: does it actually detect a dead cookie?

WHY THIS EXISTS
    session_is_live is the guard that stops a 13,600-document run before it
    starts when the pasted support.sap.com cookie has already expired. It was
    written because the alternative -- discovering it on the first document --
    costs a paste cycle to learn one bit, repeatedly.

    It never worked. fetch() returns (body, content_type, final_url), and the
    guard unpacked the third slot into a variable named `kind` and compared it
    to "login". A URL is never equal to "login", so the guard returned True for
    every cookie ever passed to it, including dead ones. There was no test, and
    a guard that always passes looks exactly like a guard that keeps passing
    because everything is fine.

    So the load-bearing case here is the FALSE one. A test that only checks
    "live cookie -> True" would have passed against the broken version too, and
    that is the trap worth naming: assert the negative, or the test is theatre.

WHAT A LOGIN PAGE LOOKS LIKE
    classify() owns that decision -- magic bytes first, then SAML_MARKERS in the
    first 4 KB. These fixtures go through the real classify(), not a stub of it,
    because the bug was precisely that a second code path had its own idea.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sapme_fetch as f                                     # noqa: E402

XLSX  = b"PK\x03\x04" + b"\x00" * 200                       # a real document
PDF   = b"%PDF-1.7\n" + b"x" * 200
LOGIN = (b"<!doctype html><html><head><title>Log On</title></head>"
         b"<body><form id='samlform' action='https://accounts.sap.com/saml2/idp'>"
         b"</form></body></html>")

ROWS = [{"id": "a", "url": "https://support.sap.com/a.xlsx", "needs_auth": True},
        {"id": "b", "url": "https://support.sap.com/b.xlsx", "needs_auth": True},
        {"id": "c", "url": "https://support.sap.com/c.xlsx", "needs_auth": True},
        {"id": "d", "url": "https://support.sap.com/d.xlsx", "needs_auth": True},
        {"id": "p", "url": "https://help.sap.com/p.html",   "needs_auth": False}]


class FakeFetch:
    """Stands in for fetch(), returning its REAL shape: (body, ctype, final_url).

    Returning the true 3-tuple is the point. A stub that returned a
    classification would have hidden the bug all over again.
    """

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def __call__(self, url, cookie, attempts=3):
        self.calls.append((url, cookie))
        b = self.bodies.pop(0)
        if isinstance(b, Exception):
            raise b
        return b, "application/octet-stream", url


class SessionCheck(unittest.TestCase):

    def setUp(self):
        self._real = f.fetch

    def tearDown(self):
        f.fetch = self._real

    def _run(self, bodies, cookie="SUPPORT_IDS_PROD=x", rows=None):
        f.fetch = FakeFetch(bodies)
        return f.session_is_live(rows if rows is not None else ROWS, cookie), f.fetch

    # -- the case the bug made impossible -----------------------------------

    def test_dead_cookie_is_detected(self):
        """A login page must return False. This failed before the fix."""
        live, _ = self._run([LOGIN, LOGIN, LOGIN])
        self.assertIs(live, False)

    def test_one_login_among_good_ones_is_conclusive(self):
        """Any login page condemns the session -- the others served anonymously."""
        live, _ = self._run([XLSX, LOGIN, XLSX])
        self.assertIs(live, False)

    def test_login_short_circuits(self):
        """Stop at the first login page; there is nothing left to learn."""
        _live, fake = self._run([XLSX, LOGIN, XLSX])
        self.assertEqual(len(fake.calls), 2)

    # -- the ordinary cases --------------------------------------------------

    def test_live_cookie_passes(self):
        live, _ = self._run([XLSX, PDF, XLSX])
        self.assertIs(live, True)

    def test_probes_several_not_one(self):
        """PROBE_N rows, because some DAM assets serve without a session."""
        _live, fake = self._run([XLSX, XLSX, XLSX])
        self.assertEqual(len(fake.calls), f.PROBE_N)

    def test_only_authenticated_rows_are_probed(self):
        """A public row proves nothing about the session, so it is not used."""
        _live, fake = self._run([XLSX, XLSX, XLSX])
        for url, _cookie in fake.calls:
            self.assertNotIn("help.sap.com", url)

    def test_the_cookie_is_actually_sent(self):
        _live, fake = self._run([XLSX, XLSX, XLSX])
        self.assertTrue(all(c == "SUPPORT_IDS_PROD=x" for _u, c in fake.calls))

    # -- "cannot tell" is a third answer, distinct from both -----------------

    def test_no_cookie_is_none_not_false(self):
        """Nothing to test is not the same as a failed test."""
        self.assertIsNone(f.session_is_live(ROWS, ""))

    def test_no_authenticated_rows_is_none(self):
        public = [r for r in ROWS if not r["needs_auth"]]
        self.assertIsNone(f.session_is_live(public, "x=1"))

    def test_all_transient_failures_is_none(self):
        """A network wobble says nothing about the cookie. Do not blame it."""
        live, _ = self._run([OSError("timed out")] * 3)
        self.assertIsNone(live)

    def test_transient_then_login_is_false(self):
        """One dead probe does not excuse a login page in the next."""
        live, _ = self._run([OSError("reset"), LOGIN, XLSX])
        self.assertIs(live, False)

    def test_transient_then_good_is_true(self):
        live, _ = self._run([OSError("reset"), XLSX, XLSX])
        self.assertIs(live, True)

    # -- the classifier this delegates to ------------------------------------

    def test_classify_owns_the_login_rule(self):
        """Guard against a future copy of the rule drifting again."""
        self.assertEqual(f.classify(LOGIN, "https://x"), "login")
        self.assertEqual(f.classify(XLSX, "https://x"), "zip")
        self.assertEqual(f.classify(PDF, "https://x"), "pdf")

    def test_fetch_returns_three_values_not_a_kind(self):
        """The shape whose misreading caused the bug. Pin it."""
        f.fetch = FakeFetch([XLSX])
        body, ctype, final = f.fetch("https://x/a.xlsx", "c=1")
        self.assertEqual(body[:4], b"PK\x03\x04")
        self.assertIsInstance(ctype, str)
        self.assertTrue(final.startswith("https://"))
        self.assertNotEqual(final, "login")


if __name__ == "__main__":
    unittest.main(verbosity=2)
