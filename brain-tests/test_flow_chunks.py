#!/usr/bin/env python3
"""Process-flow chunks: the L4 projection of an L1 fact.

WHY THIS EXISTS
    These chunks exist so that "what is the process flow for supplier
    invoicing" works as a single search, instead of requiring the two-hop
    lookup_scope_item -> lookup_process path that only someone who already
    knows the system would take.

    They are a PROJECTION. L1 stays the record; each chunk says so in its own
    text and names the tool that returns the authoritative ordered list. The
    risks worth pinning are therefore about the projection going wrong:

      * A MISSING source_system is not neutral. embed_chunks defaults an absent
        one to "sharepoint", so a chunk that forgot to set it would be filed as
        a client delivery document — wrong provenance, wrong masking story, and
        invisible as a flow.
      * STALE CHUNKS OUTLIVING THEIR SOURCE. If a scope item loses its steps,
        its chunk has to go with them, or search keeps returning a flow the
        index no longer has.
      * Scope items with NO flow must produce nothing rather than an empty
        shell that matches "process flow" and then says nothing.

Usage:
    python brain-tests/test_flow_chunks.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sapbp_build_flow_chunks as fc                        # noqa: E402

ROWS = [
    {"scope_item": "2LH", "name": "Automated Invoice Settlement (2LH)",
     "lob": "Finance", "target_release": "2608",
     "steps": ["Create Purchase Order", "Post Goods Receipt"],
     "roles": ["Purchaser", "Warehouse Clerk"],
     "applications": ["Create Purchase Order", "Post Goods Receipt for Inbound Delivery"],
     "capabilities": [{"business_area": "Invoice Management"},
                      {"business_area": "Financial Operations"}],
     "diagrams": ["2LH - 01 - Automated Invoice Settlement"]},
    # no steps and no roles -> no chunk at all
    {"scope_item": "9ZZ", "name": "Undocumented Thing (9ZZ)", "lob": "Sales",
     "steps": [], "roles": [], "applications": ["Some App"]},
]


class TestFlowChunks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._saved = (fc.INDEX, fc.OUT_DIR)
        fc.INDEX   = d / "process_index.json"
        fc.OUT_DIR = d / "chunks"
        fc.INDEX.write_text(json.dumps(
            {"_meta": {"target_release": "2608"}, "scope_items": ROWS}), encoding="utf-8")
        fc.build()
        self.out = fc.OUT_DIR

    def tearDown(self):
        fc.INDEX, fc.OUT_DIR = self._saved
        self.tmp.cleanup()

    def load(self, sid):
        return json.loads((self.out / ("%s.json" % sid)).read_text(encoding="utf-8"))

    def test_source_system_is_explicit(self):
        """Absent would be filed as a SharePoint delivery document."""
        self.assertEqual(self.load("2LH")["source_system"], "sap_process_flow")
        self.assertEqual(self.load("2LH")["deliverable_type"], "process_flow")

    def test_scope_item_without_a_flow_produces_no_chunk(self):
        self.assertFalse((self.out / "9ZZ.json").exists(),
                         "an empty flow chunk would match the query and answer nothing")

    def test_text_carries_what_a_question_would_use(self):
        t = self.load("2LH")["text"]
        for want in ("process flow", "2LH", "Automated Invoice Settlement",
                     "Create Purchase Order", "Post Goods Receipt",
                     "Purchaser", "Invoice Management", "Finance"):
            self.assertIn(want.lower(), t.lower(), want)

    def test_text_points_back_at_the_authoritative_source(self):
        """The chunk is a signpost; L1 is the record, and it says so."""
        t = self.load("2LH")["text"]
        self.assertIn('lookup_process(scope_item="2LH")', t)
        self.assertIn("authoritative", t.lower())

    def test_name_is_not_doubly_parenthesised(self):
        t = self.load("2LH")["text"]
        self.assertNotIn("(2LH).(", t)
        self.assertIn("2LH — Automated Invoice Settlement.", t)

    def test_rebuild_drops_a_chunk_whose_flow_disappeared(self):
        """Stale flows must not outlive the index they came from."""
        self.assertTrue((self.out / "2LH.json").exists())
        stripped = [dict(ROWS[0], steps=[], roles=[]), ROWS[1]]
        fc.INDEX.write_text(json.dumps({"scope_items": stripped}), encoding="utf-8")
        fc.build()
        self.assertFalse((self.out / "2LH.json").exists(),
                         "the chunk survived after its source lost its steps")

    def test_chunk_stays_well_inside_the_embed_limit(self):
        """MAX_CHARS is 8,000; a 60-step flow must not silently truncate."""
        big = dict(ROWS[0], steps=["Step %d with a reasonably long name" % i
                                   for i in range(60)])
        fc.INDEX.write_text(json.dumps({"scope_items": [big]}), encoding="utf-8")
        fc.build()
        self.assertLess(len(self.load("2LH")["text"]), 8000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
