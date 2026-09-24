#!/usr/bin/env python3
"""The Fulcrum export: does it still produce what BDCQ and KDD actually parse?

WHY THIS EXISTS
    export_scope_catalog.py replaces a Chrome extension that scraped the SAP
    for Me DOM. The BDCQ and KDD agents read the resulting scope-catalog.json
    and were NOT changed, so this file is now the entire contract between the
    brain and two agents in another repository.

    The contract is four fields, and one of them is a trap.
    kdd-generator/pre-generate.js runs extractOverview/extractSteps/
    extractBenefits over `description`, and each scans for exact heading LINES.
    The brain's scope-item `description` is just a title, so the obvious
    mapping produces KDDs with blank overviews for all 657 items, silently.
    The prose lives in processes.json as HTML and only survives if it goes
    through html_to_text.

TWO FAILURES THIS PINS, BOTH ALREADY SEEN
    * The JOIN. scope_items.json holds _meta, scope_items AND a separate
      retired_scope_items list. Indexing the top-level dict matched nothing,
      every row came out with an empty lob, and getDomainScopeRefs() filters
      on lob -- so every BDCQ domain lookup would have returned nothing. The
      export ran "successfully" and wrote 657 rows.
    * The HEADINGS. If SAP restructures its HTML the sections stop appearing
      and every extract*() returns empty, with no error at generation time.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import export_scope_catalog as ex                           # noqa: E402

HAVE_SOURCES = ex.PROCESSES.exists() and ex.SCOPE.exists()


@unittest.skipUnless(HAVE_SOURCES, "brain sources not present on this host")
class Export(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows, cls.rep = ex.build()

    # -- the contract the agents rely on -------------------------------------

    def test_the_four_fields_the_agents_read(self):
        """id, name, lob, description -- measured from their source, not guessed."""
        for r in self.rows[:50]:
            for f in ("id", "name", "lob", "description"):
                self.assertIn(f, r)

    def test_description_is_prose_not_a_title(self):
        """The trap. A title here yields blank KDDs for every scope item."""
        for r in self.rows[:50]:
            self.assertGreater(len(r["description"]), 200)
            self.assertIn("\n", r["description"])

    def test_names_are_titles_not_prose(self):
        """And the other way round: name must stay short."""
        for r in self.rows[:50]:
            self.assertLess(len(r["name"]), 200)

    # -- the join that failed silently ---------------------------------------

    def test_lob_is_populated(self):
        """An empty lob drops the item from every BDCQ domain lookup.

        This is the regression: the first run joined nothing, reported 657
        rows written, and would have been shipped had the run not printed its
        join counts.
        """
        with_lob = sum(1 for r in self.rows if r["lob"].strip())
        self.assertGreater(with_lob / len(self.rows), 0.95)

    def test_almost_everything_joined(self):
        self.assertLess(len(self.rep["no_scope_row"]), len(self.rows) * 0.05)

    def test_ids_are_unique(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    # -- the headings the parsers scan for -----------------------------------

    def test_the_agents_parsers_would_find_their_sections(self):
        counts, worst = ex.verify(self.rows)
        self.assertGreaterEqual(worst, ex.MIN_HEADING_SHARE,
                                "headings missing: %s of %d rows"
                                % (counts, len(self.rows)))

    def test_overview_is_present_everywhere(self):
        counts, _ = ex.verify(self.rows)
        self.assertEqual(counts["Overview"], len(self.rows))

    def test_verify_rejects_a_catalogue_of_titles(self):
        """The check must FAIL on the naive mapping, or it is decoration."""
        naive = [{"id": r["id"], "name": r["name"], "lob": r["lob"],
                  "description": r["name"]} for r in self.rows[:20]]
        _counts, worst = ex.verify(naive)
        self.assertLess(worst, ex.MIN_HEADING_SHARE)

    def test_retired_is_a_separate_list_not_a_flag(self):
        """scope_items.json keeps withdrawn items in retired_scope_items.

        Reading only the flag on the live rows misses all 143 of them.
        """
        import json                                          # noqa: PLC0415
        cat = json.loads(ex.SCOPE.read_text(encoding="utf-8"))
        self.assertIn("retired_scope_items", cat)
        self.assertIn("scope_items", cat)
        self.assertGreater(len(cat["retired_scope_items"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
