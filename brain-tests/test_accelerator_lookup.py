#!/usr/bin/env python3
"""accelerator_lookup — the L1 catalog lookups, and the claims they rest on.

WHY THIS EXISTS
    Two MCP tools shipped on 2026-09-22 on the strength of four assertions, each
    of which is a behaviour a future change could quietly undo:

      * The SCOPE-ITEM JOIN is load-bearing. Best Practices titles are generic —
        6,127 of 6,204 real rows read "Test script", "Test script (SAP Cloud
        ALM)" or "Test script (SAP Help Portal)" — so a title search for a
        business topic finds nothing. Only joining the row's scope item to the
        scope catalog's description makes the catalog searchable at all. Break
        the join and the tools still run, still return rows for a scope-item
        filter, and answer every topic query with zero. That is the failure this
        file is mostly here to catch.
      * AN EMPTY RESULT IS AN ANSWER. "supplier invoice processing" matches no
        scope item, and saying so is the point: a top-k retriever cannot express
        absence and answered that query with 4N6 at cosine 0.5555 with nothing
        marking it a guess.
      * NEWEST RELEASE FIRST. The catalog holds several releases side by side
        (2LH exists as S4CLD2602 and S4CLD2608); unordered, an agent may hand
        someone the superseded one.
      * RETIRED IS FLAGGED, NOT FILTERED. CLAUDE.md treats a retired catalog hit
        as meaning the OPPOSITE of available, so it has to be visible rather
        than silently absent.

Synthetic catalogs in a temp dir; never reads brain/ or mcp-server/catalog/.

Usage:
    python brain-tests/test_accelerator_lookup.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import accelerator_lookup as al                             # noqa: E402

BOM = [
    # Generic title, topic reachable only through the scope-item join.
    {"id": "b-2lh-2608", "name": "Test script", "accessLevel": "SAPCUSTOMER",
     "scope_item": "2LH", "bomType": "BOM.115", "host": "support.sap.com",
     "url": "https://support.sap.com/x/2LH_S4CLD2608_BPD_EN_DE.docx", "ext": ".docx"},
    {"id": "b-2lh-2602", "name": "Test script", "accessLevel": "SAPCUSTOMER",
     "scope_item": "2LH", "bomType": "BOM.115", "host": "support.sap.com",
     "url": "https://support.sap.com/x/2LH_S4CLD2602_BPD_EN_DE.docx", "ext": ".docx"},
    # Public host, and a retired scope item.
    {"id": "b-1le", "name": "Test script (SAP Help Portal)", "accessLevel": "PUBLIC",
     "scope_item": "1LE", "bomType": "BOM.115", "host": "help.sap.com",
     "url": "https://help.sap.com/docs/invoice-processing-opentext"},
]
ACT = [
    {"id": "a-scope", "title": "Intelligent Test Scoper", "host": "help.sap.com",
     "url": "https://help.sap.com/docs/scoper", "needs_auth": False,
     "phase": ["Prepare", "Explore"], "utype": "WEB_PAGE"},
]
SCOPE = {
    "scope_items": [
        {"scope_item_id": "2LH", "description": "Automated Invoice Settlement",
         "lob": "Finance", "business_area": "Financial Operations", "retired": False},
    ],
    "retired_scope_items": [
        {"scope_item_id": "1LE", "description": "Invoice Processing by OpenText",
         "lob": None, "business_area": None, "retired": True},
    ],
}


class TestAcceleratorLookup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "bom.json").write_text(json.dumps(BOM), encoding="utf-8")
        (d / "acc.json").write_text(json.dumps(ACT), encoding="utf-8")
        (d / "scope.json").write_text(json.dumps(SCOPE), encoding="utf-8")
        self._saved = (al.CATALOGS, al.SCOPE_CATALOG, al._CACHE, al._SCOPE)
        al.CATALOGS = {"sap_best_practices": d / "bom.json",
                       "sap_activate": d / "acc.json"}
        al.SCOPE_CATALOG = d / "scope.json"
        al._CACHE, al._SCOPE = {}, None

    def tearDown(self):
        al.CATALOGS, al.SCOPE_CATALOG, al._CACHE, al._SCOPE = self._saved
        self.tmp.cleanup()

    # ── the join ────────────────────────────────────────────────────────────
    def test_topic_is_found_through_the_scope_item_join(self):
        """The load-bearing one. No row's TITLE contains 'invoice settlement'."""
        self.assertNotIn("invoice settlement",
                         " ".join(b["name"] for b in BOM).lower(),
                         "fixture must reproduce the generic-title problem")
        res = al.lookup(query="invoice settlement")
        self.assertEqual(res["total_matches"], 2)
        for r in res["results"]:
            self.assertEqual(r["scope_item_name"], "Automated Invoice Settlement")

    def test_join_fills_lob_the_manifest_omits(self):
        res = al.lookup(scope_item="2LH", limit=1)
        self.assertEqual(res["results"][0]["lob"], "Finance")

    # ── absence is an answer ────────────────────────────────────────────────
    def test_unmatched_topic_returns_nothing_rather_than_a_guess(self):
        self.assertEqual(al.lookup(query="supplier invoice")["total_matches"], 0)
        self.assertEqual(al.scope_items(query="supplier invoice")["total_matches"], 0)

    # ── ordering ────────────────────────────────────────────────────────────
    def test_newest_release_first(self):
        got = [r["release"] for r in al.lookup(scope_item="2LH")["results"]]
        self.assertEqual(got, ["2608", "2602"])

    def test_exact_identifier_outranks_a_text_match(self):
        self.assertEqual(al.lookup(query="2LH")["results"][0]["scope_item"], "2LH")
        self.assertEqual(al.scope_items(query="1LE")["results"][0]["scope_item_id"], "1LE")

    # ── retired ─────────────────────────────────────────────────────────────
    def test_retired_scope_items_are_returned_and_flagged(self):
        hit = [r for r in al.scope_items(query="invoice")["results"]
               if r["scope_item_id"] == "1LE"]
        self.assertEqual(len(hit), 1, "retired items must not be filtered out")
        self.assertTrue(hit[0]["retired"])
        acc = al.lookup(scope_item="1LE")["results"][0]
        self.assertTrue(acc["scope_item_retired"])

    # ── auth and honesty about unknowns ─────────────────────────────────────
    def test_needs_auth_follows_the_host(self):
        by_id = {r["id"]: r for r in al.lookup(query="invoice")["results"]}
        self.assertTrue(by_id["b-2lh-2608"]["needs_auth"], "support.sap.com is SAML")
        self.assertFalse(by_id["b-1le"]["needs_auth"], "help.sap.com is public")

    def test_needs_auth_is_unknown_not_public_when_the_host_rule_is_missing(self):
        """Telling someone help.sap.com needs a login sends them after nothing."""
        saved = al._PUBLIC_HOSTS
        try:
            al._PUBLIC_HOSTS, al._CACHE = None, {}
            self.assertIsNone(al.lookup(scope_item="2LH")["results"][0]["needs_auth"])
        finally:
            al._PUBLIC_HOSTS, al._CACHE = saved, {}

    def test_absent_fields_stay_none_rather_than_being_invented(self):
        act = al.lookup(query="scoper", source="sap_activate")["results"][0]
        self.assertIsNone(act["access_level"], "Roadmap Viewer publishes none")
        self.assertIsNone(act["scope_item"])

    # ── facets ──────────────────────────────────────────────────────────────
    def test_phase_filter_matches_a_member_of_a_multi_phase_row(self):
        self.assertEqual(al.lookup(query="scoper", phase="Explore")["total_matches"], 1)
        self.assertEqual(al.lookup(query="scoper", phase="Deploy")["total_matches"], 0)

    def test_needs_auth_filter(self):
        self.assertTrue(all(r["needs_auth"] for r in
                            al.lookup(query="invoice", needs_auth=True)["results"]))
        self.assertTrue(all(not r["needs_auth"] for r in
                            al.lookup(query="invoice", needs_auth=False)["results"]))

    # ── degradation ─────────────────────────────────────────────────────────
    def test_missing_catalog_reports_how_to_build_it(self):
        al.CATALOGS = {"sap_best_practices": Path(self.tmp.name) / "nope.json"}
        al._CACHE = {}
        res = al.lookup(query="invoice")
        self.assertEqual(res["total_matches"], 0)
        self.assertTrue(any("sapbp_catalog.py" in p for p in res["problems"]),
                        "an absent catalog must name the command that builds it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
