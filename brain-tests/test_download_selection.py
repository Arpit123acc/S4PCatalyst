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
        # country-specific configuration: same name AND scope item as DE, but
        # each country's version differs -- that is what makes it localization
        {"name": "Preconfigured tax codes", "country": "DE", "scope_item": "2LH"},
        {"name": "Preconfigured tax codes", "country": "BR", "scope_item": "2LH"},
        {"name": "Highlights of finance", "country": "XX", "scope_item": None,
         "scenario_id": "ours"},
        # a different SOLUTION, not a different country: on-premise / IBP / Ariba
        {"name": "Test script", "country": "DE", "scope_item": "9ZZ",
         "scenario_id": "some-other-product"},
    ]


class TestDownloadSelection(unittest.TestCase):

    def setUp(self):
        self.rows = manifest()
        for r in self.rows:
            r.setdefault("scenario_id", "ours")
        k.mark_downloads(self.rows, "DE", "ours")
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
        self.assertEqual(len(self.rows), 10)
        self.assertEqual(sum(1 for r in self.rows if r["download"]), 7)

    def test_a_document_the_primary_country_lacks_is_kept(self):
        """Keyed on (scope item, name), so a country-specific DOCUMENT for a
        shared scope item survives -- SAP Note 3335519 exists only for Brazil."""
        note = [r for r in self.rows if r["name"].startswith("SAP Note")][0]
        self.assertTrue(note["download"], "BR-only document for a shared scope item")
        self.assertIsNone(note["skip_reason"])
        # ...while the test script for that same scope item IS a duplicate
        self.assertFalse(self.row("BR", "2LH")["download"])

    def test_country_specific_configuration_is_never_a_duplicate(self):
        """Only TEST SCRIPTS are skipped as locale duplicates.

        German and Brazilian "Preconfigured tax codes" share a name and a scope
        item precisely because each is its country's version of the same thing.
        An earlier rule keyed on (scope item, name) alone discarded all 236 such
        rows -- tax codes, local YCOA G/L master data, Forms, prerequisites
        matrices, per-country SAP Notes -- which is the localization these
        countries were added for. Test scripts are 14,267 of the 14,503
        secondary rows, so excluding only those still removes 98% of the bulk.
        """
        row = self.row("BR", "2LH", "Preconfigured tax codes")
        self.assertTrue(row["download"])
        self.assertIsNone(row["skip_reason"])
        self.assertFalse(self.row("BR", "2LH")["download"], "the test script IS a duplicate")

    def test_another_solution_scenario_is_never_downloaded(self):
        """bom_manifest spans all 99 scenarios SAP publishes, because Tier B
        filters on country and validity but never on scenario. 11,483 of 13,841
        rows marked for download were other products -- S/4HANA on-premise, IBP,
        Ariba. A Cloud support contract does not entitle you to the on-premise
        library, so SAP answers with a SAML page indistinguishable from an
        expired session, which is how this surfaced.
        """
        row = self.row("DE", "9ZZ")
        self.assertFalse(row["download"])
        self.assertIn("solution scenario", row["skip_reason"])

    def test_every_row_is_decided(self):
        for r in self.rows:
            self.assertIn("download", r)
            self.assertIs(type(r["download"]), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
