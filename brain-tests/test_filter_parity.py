#!/usr/bin/env python3
"""The two filter implementations must agree.

Search filtering exists twice: vectorstore.row_excluded() in Python for the
dense half, and keyword_search._where() in SQL for the lexical half. They are
the same rule expressed in two languages, and keyword_search's docstring has
said "mirrors ... exactly" since it was written — which is a comment, not a
guarantee. This asserts it.

Why it is worth a test rather than trust. On 2026-09-22 both had drifted from
the data in the same two ways, unnoticed for weeks:

  * PROVENANCE_EXEMPT_SOURCES was measured against a 49,857-chunk corpus in
    which it covered 954 chunks. The SAP Best Practices ingest then added
    133,871 chunks carrying no phase at all, and neither implementation was
    updated, so a phase filter silently hid three quarters of the corpus.
  * Multi-valued facets are stored ", "-joined ("Prepare, Explore") and both
    implementations compared the whole string for equality, so 163 of 731 SAP
    Activate accelerators could not be found by either of their phases.

Both failures return FEWER results, which is indistinguishable from a correct
empty answer — the reason they survived so long, and the reason the parity
matters more than either implementation being individually plausible.

Run: python3.11 brain-tests/test_filter_parity.py
"""

import sys
import sqlite3
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import vectorstore                                          # noqa: E402
import keyword_search                                       # noqa: E402

FIELDS = ("source_system", "phase", "agent_role", "deliverable_type")

# (meta, filters, should_match, why)
CASES = [
    ({"source_system": "sap_activate", "phase": "Prepare, Explore"},
     {"phase": "Explore"}, True, "joined list matches a member"),
    ({"source_system": "sap_activate", "phase": "Prepare, Explore"},
     {"phase": "explore"}, True, "member match is case-insensitive"),
    ({"source_system": "sap_activate", "phase": "Explore, Realize, Deploy, Run"},
     {"phase": "Deploy"}, True, "member in the middle of a long list"),
    ({"source_system": "sap_activate", "phase": "Explore"},
     {"phase": "Explore"}, True, "plain equality still works"),
    ({"source_system": "sap_activate", "phase": "Explore"},
     {"phase": "Realize"}, False, "non-member is excluded"),
    ({"source_system": "sap_activate", "phase": "Prepare, Explore"},
     {"phase": "Prep"}, False, "a prefix is not a member"),

    # provenance exemption: sources with no phase must survive a phase filter
    ({"source_system": "sap_best_practices", "phase": None},
     {"phase": "Realize"}, True, "Best Practices has no phase and is exempt"),
    ({"source_system": "sap_bpd", "phase": None},
     {"phase": "Explore"}, True, "sap_bpd is exempt"),
    ({"source_system": "developer_docs", "phase": None},
     {"phase": "Explore"}, True, "vendor docs stay exempt"),
    ({"source_system": "sharepoint", "phase": None},
     {"phase": "Explore"}, False, "delivery docs DO have provenance, so they filter"),
    ({"source_system": "sharepoint", "phase": "Explore"},
     {"phase": "Explore"}, True, "delivery doc with the right phase"),

    # exemption is per-field: descriptive filters still bite on exempt sources
    ({"source_system": "sap_best_practices", "deliverable_type": "test_script"},
     {"deliverable_type": "test_script"}, True, "descriptive filter matches"),
    ({"source_system": "sap_best_practices", "deliverable_type": "configuration"},
     {"deliverable_type": "test_script"}, False,
     "exemption covers phase/agent_role only, never deliverable_type"),

    # LIKE wildcards in the value must not widen the match
    ({"source_system": "sharepoint", "deliverable_type": "test_strategy"},
     {"deliverable_type": "test_strategy"}, True, "underscore value matches itself"),
    ({"source_system": "sharepoint", "deliverable_type": "testXstrategy"},
     {"deliverable_type": "test_strategy"}, False,
     "underscore is escaped, not a LIKE wildcard"),
    ({"source_system": "sharepoint", "deliverable_type": "anything"},
     {"deliverable_type": "%"}, False, "a percent value is escaped, not a wildcard"),

    # agent_role values contain spaces but never commas
    ({"source_system": "sap_activate", "agent_role": "Integration Implementation Expert"},
     {"agent_role": "Integration Implementation Expert"}, True,
     "internal spaces are preserved"),
    ({"source_system": "sap_activate",
      "agent_role": "Integration Implementation Expert, Data Migration Expert"},
     {"agent_role": "Data Migration Expert"}, True,
     "multi-word member of a joined list"),
]


def sql_matches(meta, filters):
    """Run keyword_search._where() against a one-row table."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE meta (%s)" % ", ".join("%s TEXT" % f for f in FIELDS))
    con.execute("INSERT INTO meta VALUES (%s)" % ",".join("?" * len(FIELDS)),
                [meta.get(f) for f in FIELDS])
    where, params = keyword_search._where(filters)
    row = con.execute("SELECT count(*) FROM meta m WHERE 1=1" + where, params).fetchone()
    con.close()
    return bool(row[0])


def py_matches(meta, filters):
    return not vectorstore.row_excluded(meta, filters)


class TestFilterParity(unittest.TestCase):

    def test_each_case_matches_expectation(self):
        for meta, filters, want, why in CASES:
            with self.subTest(why=why, engine="python"):
                self.assertEqual(py_matches(meta, filters), want, why)
            with self.subTest(why=why, engine="sql"):
                self.assertEqual(sql_matches(meta, filters), want, why)

    def test_the_two_engines_agree(self):
        """The property that matters: identical verdicts, whatever they are."""
        for meta, filters, _want, why in CASES:
            with self.subTest(why=why):
                self.assertEqual(py_matches(meta, filters), sql_matches(meta, filters),
                                 "python and SQL disagree on: %s" % why)

    def test_sap_sources_are_exempt_from_provenance_filters(self):
        """Regression guard for the 2026-09-22 finding.

        133,871 sap_best_practices chunks carry no phase because SAP publishes
        none for them. If this set is ever trimmed back, a phase filter starts
        hiding three quarters of the corpus again, and it does so silently.
        """
        for src in ("sap_best_practices", "sap_bpd"):
            self.assertIn(src, vectorstore.PROVENANCE_EXEMPT_SOURCES, src)
        # sap_activate must NOT be exempt: SAP publishes a real phase for it, so
        # filtering it by phase is meaningful and has to keep working.
        self.assertNotIn("sap_activate", vectorstore.PROVENANCE_EXEMPT_SOURCES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
