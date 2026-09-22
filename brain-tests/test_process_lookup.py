#!/usr/bin/env python3
"""The process index: its joins, and the lookup over it.

WHY THIS EXISTS
    sapbp_build_process_index.py turns four raw Process Navigator files into the
    one index lookup_process serves. Three of its behaviours are the kind that
    fail by producing a smaller, entirely plausible result:

      * THE APPLICATION JOIN goes through solutionProcessID, a GUID. Break it and
        every scope item simply reports no applications — which is also what a
        scope item with genuinely none looks like. 120 of 657 really do have none,
        so there is no count that obviously signals the join died.
      * COMMA-JOINED APP NAMES. SAP packs several Fiori apps into one
        applicationName: 67 of 2,298 distinct names are lists, one of them 21 apps
        long. Not splitting undercounts silently — 2LH reports 3 applications
        instead of 4 — and makes each packed app unfindable by name.
      * THE DIAGRAM JOIN matches the scope-item code prefixing a diagram name,
        because diagrams carry no externalId and their business_id is null. It is
        90% effective on real data; a regression here just means fewer steps.

    Capabilities are asserted ABSENT-TOLERANT on purpose: SAP had published no
    SolutionCapabilityHierarchy rows for 2608 (0, against 53,067 for 2602), so the
    builder must handle an empty or missing file without failing, and must pick
    the data up automatically if a later release publishes it.

Synthetic inputs in a temp dir; never reads brain/.

Usage:
    python brain-tests/test_process_lookup.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sapbp_build_process_index as bld                     # noqa: E402
import process_lookup as pl                                 # noqa: E402

OURS = "scenario-ours"
PID_A, PID_B = "pid-a", "pid-b"

PROCESSES = [
    {"solutionProcessId": PID_A, "externalId": "2LH",
     "enName": "Automated Invoice Settlement (2LH)",
     "businessProcessGroupName": "Finance", "changeCategory": "No Change",
     "solutionScenarioTargetRelease": "2608"},
    {"solutionProcessId": PID_B, "externalId": "1GA",
     "enName": "Accounting and Financial Close (1GA)",
     "businessProcessGroupName": "Finance", "changeCategory": "Update",
     "solutionScenarioTargetRelease": "2608"},
]
APPLICATIONS = [
    # the comma-joined case: ONE row, TWO apps
    {"solutionProcessID": PID_A,
     "applicationName": "Create Purchase Order, Create Purchase Order - Advanced"},
    {"solutionProcessID": PID_A, "applicationName": "Post Goods Receipt for Inbound Delivery"},
    {"solutionProcessID": PID_B, "applicationName": "Audit Journal"},
    # belongs to another scenario's process — must not leak in
    {"solutionProcessID": "pid-foreign", "applicationName": "Should Not Appear"},
]
DIAGRAMS = [
    {"name": "2LH - 01 - Automated Invoice Settlement", "scenario_id": OURS,
     "steps": ["Create Purchase Order", "Post Goods Receipt"],
     "roles": ["Purchaser"], "events": []},
    {"name": "1GA - 01 - Period Close", "scenario_id": OURS,
     "steps": ["Carry Forward Balances"], "roles": ["Accountant"], "events": []},
    # other scenario — must be filtered out
    {"name": "2LH - 01 - Automated Invoice Settlement", "scenario_id": "scenario-other",
     "steps": ["WRONG RELEASE"], "roles": [], "events": []},
    # unmatched: no scope-item prefix
    {"name": "Warehouse Outbound Processing", "scenario_id": OURS,
     "steps": ["x"], "roles": [], "events": []},
    # known scope item, but no process in THIS country — must still contribute
    {"name": "1WQ - 01 - Bill of Exchange", "scenario_id": OURS,
     "steps": ["Present a Check to a Bank"], "roles": ["Cash Manager"], "events": []},
]
CATALOG = {"scope_items": [
    {"scope_item_id": "2LH", "description": "Automated Invoice Settlement"},
    {"scope_item_id": "1WQ", "description": "Bill of Exchange"}]}


class TestBuilder(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._saved = (bld.PROCESSES, bld.APPLICATIONS, bld.DIAGRAMS,
                       bld.CAPABILITIES, bld.MANIFEST, bld.OUT, bld.SCOPE_CATALOG)
        bld.PROCESSES    = d / "processes.json"
        bld.APPLICATIONS = d / "applications.json"
        bld.DIAGRAMS     = d / "diagram_steps.json"
        bld.CAPABILITIES = d / "capabilities.json"
        bld.MANIFEST     = d / "manifest.json"
        bld.SCOPE_CATALOG = d / "scope_items.json"
        bld.SCOPE_CATALOG.write_text(json.dumps(CATALOG), encoding="utf-8")
        bld.OUT          = d / "process_index.json"
        bld.PROCESSES.write_text(json.dumps(PROCESSES), encoding="utf-8")
        bld.APPLICATIONS.write_text(json.dumps(APPLICATIONS), encoding="utf-8")
        bld.DIAGRAMS.write_text(json.dumps(DIAGRAMS), encoding="utf-8")
        bld.MANIFEST.write_text(json.dumps({"scenario_id": OURS,
                                            "target_release": "2608"}), encoding="utf-8")

    def tearDown(self):
        (bld.PROCESSES, bld.APPLICATIONS, bld.DIAGRAMS,
         bld.CAPABILITIES, bld.MANIFEST, bld.OUT, bld.SCOPE_CATALOG) = self._saved
        self.tmp.cleanup()

    def test_comma_joined_app_names_are_split(self):
        self.assertEqual(bld.split_apps("A, B ,C"), ["A", "B", "C"])
        self.assertEqual(bld.split_apps("Single App"), ["Single App"])
        self.assertEqual(bld.split_apps(None), [])
        rows, counts, _ = bld.build(OURS)
        self.assertEqual(rows["2LH"]["applications"],
                         ["Create Purchase Order", "Create Purchase Order - Advanced",
                          "Post Goods Receipt for Inbound Delivery"],
                         "one comma-joined row must yield two applications")

    def test_application_join_is_by_process_id(self):
        rows, _, _ = bld.build(OURS)
        self.assertEqual(rows["1GA"]["applications"], ["Audit Journal"])
        every = [a for r in rows.values() for a in r["applications"]]
        self.assertNotIn("Should Not Appear", every,
                         "an application for a foreign process must not leak in")

    def test_diagrams_are_filtered_by_scenario(self):
        rows, counts, _ = bld.build(OURS)
        self.assertIn("Create Purchase Order", rows["2LH"]["steps"])
        self.assertNotIn("WRONG RELEASE", rows["2LH"]["steps"],
                         "another scenario's diagram must not contribute steps")
        self.assertEqual(counts["diagrams_for_scenario"], 4)

    def test_diagram_for_a_scope_item_without_a_country_process_is_kept(self):
        """The 2026-09-22 recovery: 74 diagrams, 18 scope items, 406 steps.

        The process list is per country, so a scope item with no German process
        is absent from it while its diagrams still exist. Keying the join on the
        process list discarded them silently -- 1WQ "Bill of Exchange" lost 112
        steps that way.
        """
        rows, counts, _ = bld.build(OURS)
        self.assertIn("1WQ", rows, "a catalog scope item with diagrams must appear")
        self.assertEqual(rows["1WQ"]["steps"], ["Present a Check to a Bank"])
        self.assertEqual(rows["1WQ"]["name"], "Bill of Exchange", "named from the catalog")
        self.assertFalse(rows["1WQ"]["has_country_process"])
        self.assertTrue(rows["2LH"]["has_country_process"])
        self.assertEqual(counts["scope_items_from_catalog_only"], 1)

    def test_unmatched_diagrams_are_counted_not_hidden(self):
        _, counts, unmatched = bld.build(OURS)
        self.assertEqual(counts["diagrams_matched"], 3)
        self.assertEqual(counts["diagrams_unmatched"], 1)
        self.assertIn("Warehouse Outbound Processing", unmatched)

    def test_missing_capabilities_is_tolerated_and_reported_as_zero(self):
        """SAP published none for 2608; absence must not be an error."""
        self.assertFalse(bld.CAPABILITIES.exists())
        _, counts, _ = bld.build(OURS)
        self.assertEqual(counts["capability_rows_used"], 0)

    def test_capabilities_join_on_scope_item_and_carry_their_release(self):
        """Keyed on the scope item, not the process GUID.

        That is the only reason capability data from an earlier release can be
        attached at all: GUIDs are per release, scope item codes are not. The
        release is recorded on every row so nobody quotes 2602 taxonomy as 2608.
        """
        bld.CAPABILITIES.write_text(json.dumps({
            "_meta": {"source_release": "2602"},
            "by_scope_item": {"2LH": [{"solution_capability": "Invoice Processing (S/4)",
                                       "business_capability": "Invoice Processing",
                                       "business_area": "Invoice Management",
                                       "line_of_business": "Finance"}]}}), encoding="utf-8")
        rows, counts, _ = bld.build(OURS)
        self.assertEqual(counts["capability_rows_used"], 1)
        self.assertEqual(counts["capability_source_release"], "2602")
        self.assertEqual(rows["2LH"]["capabilities"][0]["business_area"], "Invoice Management")
        self.assertEqual(rows["2LH"]["capability_source_release"], "2602")
        self.assertNotIn("capabilities", rows["1GA"], "only scope items with data get the key")

    def test_values_are_deduplicated_and_deterministic(self):
        bld.DIAGRAMS.write_text(json.dumps(DIAGRAMS + [DIAGRAMS[0]]), encoding="utf-8")
        rows, _, _ = bld.build(OURS)
        steps = rows["2LH"]["steps"]
        self.assertEqual(len(steps), len(set(steps)))


class TestLookup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        idx = Path(self.tmp.name) / "process_index.json"
        idx.write_text(json.dumps({
            "_meta": {"built_at": "2026-09-22T00:00:00+00:00", "target_release": "2608",
                      "counts": {"scope_items": 2, "scope_items_with_apps": 2}},
            "scope_items": [
                {"scope_item": "2LH", "name": "Automated Invoice Settlement (2LH)",
                 "lob": "Finance", "change_category": "No Change", "target_release": "2608",
                 "capability_source_release": "2602",
                 "capabilities": [{"solution_capability": "Invoice Processing (S/4)",
                                   "business_capability": "Invoice Processing",
                                   "business_area": "Invoice Management",
                                   "line_of_business": "Finance"}],
                 "applications": ["Create Purchase Order", "Post Goods Receipt for Inbound Delivery"],
                 "steps": ["Create Purchase Order"], "roles": ["Purchaser"]},
                {"scope_item": "1GA", "name": "Accounting and Financial Close (1GA)",
                 "lob": "Finance", "change_category": "Update", "target_release": "2608",
                 "applications": ["Audit Journal"], "steps": [], "roles": []},
            ]}), encoding="utf-8")
        self._saved = (pl.INDEX, pl._CACHE)
        pl.INDEX, pl._CACHE = idx, None

    def tearDown(self):
        pl.INDEX, pl._CACHE = self._saved
        self.tmp.cleanup()

    def test_exact_scope_item_wins(self):
        r = pl.lookup(query="2LH")
        self.assertEqual(r["results"][0]["scope_item"], "2LH")

    def test_reverse_edge_finds_every_user_of_an_app(self):
        r = pl.lookup(application="Purchase Order")
        self.assertEqual([x["scope_item"] for x in r["results"]], ["2LH"],
                         "substring match, because callers rarely have the exact label")

    def test_unmatched_query_returns_nothing_rather_than_a_guess(self):
        self.assertEqual(pl.lookup(query="quantum teleportation")["total_matches"], 0)

    def test_empty_steps_is_data_not_failure(self):
        r = pl.lookup(scope_item="1GA")["results"][0]
        self.assertEqual(r["steps"], [])
        self.assertEqual(r["step_count"], 0)

    def test_coverage_is_reported_so_absence_is_interpretable(self):
        self.assertIn("scope_items_with_apps", pl.lookup(query="2LH")["coverage"])

    def test_with_steps_false_omits_the_bulky_fields(self):
        r = pl.lookup(scope_item="2LH", with_steps=False)["results"][0]
        self.assertNotIn("steps", r)
        self.assertEqual(r["step_count"], 1, "the count survives even when the list is dropped")

    def test_capability_filter_matches_any_level_of_the_taxonomy(self):
        """A caller says "Invoice Management" without knowing it is a business area."""
        for term in ("Invoice Management", "Invoice Processing", "invoice processing (s/4)"):
            with self.subTest(term=term):
                r = pl.lookup(capability=term)
                self.assertEqual([x["scope_item"] for x in r["results"]], ["2LH"], term)
        self.assertEqual(pl.lookup(capability="Warehousing")["total_matches"], 0)

    def test_capability_source_release_is_surfaced(self):
        """A consumer must be able to see the taxonomy is from another release."""
        r = pl.lookup(scope_item="2LH")["results"][0]
        self.assertEqual(r["capability_source_release"], "2602")

    def test_missing_index_names_the_command_that_builds_it(self):
        pl.INDEX, pl._CACHE = Path(self.tmp.name) / "nope.json", None
        r = pl.lookup(query="2LH")
        self.assertEqual(r["total_matches"], 0)
        self.assertTrue(any("sapbp_build_process_index.py" in p for p in r["problems"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
