#!/usr/bin/env python3
"""Which catalog rows get downloaded, and which are skipped as locale duplicates.

WHY THIS EXISTS
    The fetch covers DE plus BR, ES and US, because those three carry the 22
    localized scope items DE lacks. That takes the catalog from 6,204 rows to
    27,707, and roughly 14,500 of the new ones are the same test script in
    another locale. Test scripts are already 75% of the corpus and needed
    BULK_PENALTY to stop them crowding out everything else, so downloading the
    duplicates would undo that for almost no new content.

    Both failure directions are silent. Skip too much and a localized scope item
    is missing from the brain, looking exactly like a scope item SAP never
    documented. Skip too little and the corpus quietly triples its most
    over-represented content. Neither shows up as an error.

    The first version of this got it wrong in the second direction and the
    dry-run said "27707 of 27707 rows, all DE" -- because the manifest stamped
    the REQUESTED country on every row rather than the row's own country_ID.
    That was correct while the fetch was single-country. The test below would
    have caught it, which is why it is here.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sapbp_catalog as k                                   # noqa: E402


def manifest():
    return [
        {"name": "Test script", "country": "DE", "scope_item": "2LH"},
        {"name": "Test script", "country": "DE", "scope_item": "1GA"},
        {"name": "Test script", "country": "BR", "scope_item": "2LH"},
        {"name": "Test script", "country": "BR", "scope_item": "2UU"},
        # same scope item as DE but a document DE does not have -> keep
        {"name": "SAP Note 3335519 (Brazil)", "country": "BR", "scope_item": "2LH"},
        {"name": "Test script", "country": "ES", "scope_item": "1GA"},
        {"name": "Highlights of finance", "country": "XX", "scope_item": None},
    ]


class TestDownloadSelection(unittest.TestCase):

    def setUp(self):
        self.rows = manifest()
        k.mark_downloads(self.rows, "DE")
    def row(self, country, scope_item, name="Test script"):
        """One row. Name included because BR carries two rows for 2LH: the
        duplicate test script and the Brazil-only SAP Note."""
        hits = [r for r in self.rows if r["country"] == country
                and r["scope_item"] == scope_item and r["name"] == name]
        self.assertEqual(len(hits), 1, (country, scope_item, name))
        return hits[0]

    def test_primary_country_is_always_downloaded(self):
        self.assertTrue(self.row("DE", "2LH")["download"])

    def test_generic_country_is_always_downloaded(self):
        """XX holds the accelerators — the whole reason it was added."""
        self.assertTrue(self.row("XX", None, "Highlights of finance")["download"])

    def test_secondary_country_duplicate_is_skipped_with_a_reason(self):
        row = self.row("BR", "2LH")
        self.assertFalse(row["download"])
        self.assertIn("duplicate of DE", row["skip_reason"])
        self.assertFalse(self.row("ES", "1GA")["download"])

    def test_secondary_country_localized_scope_item_is_kept(self):
        """The 22 scope items BR/ES/US were added for must survive the filter."""
        row = self.row("BR", "2UU")
        self.assertTrue(row["download"], "2UU exists only in BR; skipping it loses it")
        self.assertIsNone(row["skip_reason"])

    def test_skipped_rows_remain_in_the_manifest(self):
        """Excluded from the corpus, not denied.

        lookup_accelerator must still find them and report their URL: a human
        asking for the Brazilian variant should get a link, not silence.
        """
        self.assertEqual(len(self.rows), 7)
        self.assertEqual(sum(1 for r in self.rows if r["download"]), 5)

    def test_a_document_the_primary_country_lacks_is_kept(self):
        """Keyed on (scope item, name), so a country-specific DOCUMENT for a
        shared scope item survives -- SAP Note 3335519 exists only for Brazil."""
        note = [r for r in self.rows if r["name"].startswith("SAP Note")][0]
        self.assertTrue(note["download"], "BR-only document for a shared scope item")
        self.assertIsNone(note["skip_reason"])
        # ...while the test script for that same scope item IS a duplicate
        self.assertFalse(self.row("BR", "2LH")["download"])

    def test_every_row_is_decided(self):
        for r in self.rows:
            self.assertIn("download", r)
            self.assertIs(type(r["download"]), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
