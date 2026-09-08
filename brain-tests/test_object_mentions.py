#!/usr/bin/env python3
"""Round-trip the object-mention index against a synthetic corpus.

WHY THIS EXISTS SEPARATELY FROM brain_regression.py
    brain_regression measures RETRIEVAL QUALITY, so it needs the real 49k-chunk brain
    and only runs on the delivery host. The mention index is different: it is plain
    data-structure correctness (extract -> insert -> read back, both directions), and
    it can therefore be tested on any laptop against four synthetic chunks. Which
    means it actually gets run before a change ships, rather than after.

WHAT IT PINS
    * the FORWARD edge (chunk -> objects) that replaced per-hit query-time regex,
    * the REVERSE edge (object -> documents) that did not exist before,
    * case-insensitive lookup, source_system filtering, and a genuine zero result,
    * that BM25 search still works with the extra table present,
    * that a keyword.db built BEFORE the mention table degrades to indexed=False
      rather than raising -- the distinction between "never mentioned" and "never
      indexed" is the whole reason `indexed` is in the payload.

Writes its DB to a temp directory, never under brain/, so it cannot leave a synthetic
index where a real one belongs.

Usage:
    python brain-tests/test_object_mentions.py        # exits non-zero on failure
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "mcp-server"))

import keyword_index                                          # noqa: E402
import keyword_search                                         # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="s4pc-mentions-"))
DB = TMP / "keyword_test.db"
keyword_index.DB_PATH = DB
keyword_index.INDEX_DIR = TMP
keyword_search.DB_PATH = DB

ROWS = [
    {"text": "The interface reads from EKKO and EKPO directly, which ATC flags. "
             "Replace with I_PurchaseOrder and API_PURCHASEORDER_PROCESS_SRV.",
     "meta": {"id": "c1", "chunk_id": "c1", "source": "PO Interface TD.docx",
              "source_system": "sharepoint", "phase": "Realize",
              "agent_role": "build_agent", "deliverable_type": "technical_design",
              "chunk_file": "sharepoint/chunks/c1.json", "scope_item_id": None}},
    {"text": "Product classification uses API_CLFN_PRODUCT_SRV with I_ClfnCharacteristic. "
             "No classical table access anywhere in this design.",
     "meta": {"id": "c2", "chunk_id": "c2", "source": "Classification FD.docx",
              "source_system": "sharepoint", "phase": "Explore",
              "agent_role": "functional", "deliverable_type": "functional_design",
              "chunk_file": "sharepoint/chunks/c2.json", "scope_item_id": None}},
    {"text": "Legacy note: the old report selected from EKKO. Superseded.",
     "meta": {"id": "c3", "chunk_id": "c3", "source": "Legacy Report Notes.docx",
              "source_system": "sharepoint", "phase": "Deploy",
              "agent_role": "general", "deliverable_type": "reference_document",
              "chunk_file": "sharepoint/chunks/c3.json", "scope_item_id": None}},
    {"text": "Sorting and filtering a list binding has nothing to do with SAP objects.",
     "meta": {"id": "c4", "chunk_id": "c4", "source": "ui5-sorting.md",
              "source_system": "developer_docs", "phase": "Realize",
              "agent_role": "build_agent", "deliverable_type": "ui5_docs",
              "chunk_file": "webdocs/chunks/c4.json", "scope_item_id": None}},
    # SAME FILENAME, DIFFERENT FOLDER. Verbatim shape of the real collision: the brain
    # UI reports 2,734 distinct sources for 2,861 ingested files, so ~127 names exist
    # in two places. Before relative_path reached the index these two were one row
    # with their mentions summed, and nothing said so.
    {"text": "Interface uses API_CLFN_PRODUCT_SRV for classification.",
     "meta": {"id": "c5", "chunk_id": "c5", "source": "Interface Spec.docx",
              "source_system": "sharepoint", "phase": "Realize",
              "agent_role": "build_agent", "deliverable_type": "technical_design",
              "chunk_file": "sharepoint/chunks/c5.json", "scope_item_id": None,
              "relative_path": "MM/Interface Spec.docx"}},
    {"text": "Interface uses API_CLFN_PRODUCT_SRV and I_ClfnCharacteristic too.",
     "meta": {"id": "c6", "chunk_id": "c6", "source": "Interface Spec.docx",
              "source_system": "sharepoint", "phase": "Realize",
              "agent_role": "build_agent", "deliverable_type": "technical_design",
              "chunk_file": "sharepoint/chunks/c6.json", "scope_item_id": None,
              "relative_path": "SD/Interface Spec.docx"}},
]

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-42s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def _reset():
    keyword_search.has_mentions.cache_clear()
    keyword_search.has_path.cache_clear()
    keyword_search._lifecycle_cols.cache_clear()
    keyword_search._con.cache_clear()


def main():
    n = keyword_index.build(ROWS)
    print("built %d rows -> %s\n" % (n, DB))
    _reset()

    check("mention table present", keyword_search.has_mentions(), True)

    print("\nFORWARD edge - chunk -> objects")
    fwd = keyword_search.mentions_for_chunks(["c1", "c2", "c3", "c4"])
    check("classical tables AND released objects", sorted(fwd.get("c1", [])),
          ["API_PURCHASEORDER_PROCESS_SRV", "EKKO", "EKPO", "I_PurchaseOrder"])
    check("second chunk's objects", sorted(fwd.get("c2", [])),
          ["API_CLFN_PRODUCT_SRV", "I_ClfnCharacteristic"])
    # Prose that names no object must be ABSENT, not present-and-empty: the wrapper in
    # server.py skips hits with no names, and an empty list would make it do work.
    check("object-free prose absent from result", "c4" in fwd, False)

    print("\nREVERSE edge - object -> documents")
    ekko = keyword_search.documents_for_object("EKKO")
    check("indexed", ekko["indexed"], True)
    check("spans 2 documents", ekko["total_documents"], 2)
    check("document names", sorted(d["source"] for d in ekko["documents"]),
          ["Legacy Report Notes.docx", "PO Interface TD.docx"])
    check("carries phase metadata for triage",
          sorted(d["phase"] for d in ekko["documents"]), ["Deploy", "Realize"])

    print("\nlookup semantics")
    check("case-insensitive",
          keyword_search.documents_for_object("ekko")["total_documents"], 2)
    check("source_system filter applies",
          keyword_search.documents_for_object(
              "EKKO", source_system="developer_docs")["total_documents"], 0)
    # A true zero must still say indexed=True, or the caller cannot tell it apart
    # from an unbuilt index.
    unseen = keyword_search.documents_for_object("API_NEVER_SEEN")
    check("true negative is indexed with zero docs",
          (unseen["indexed"], unseen["total_documents"]), (True, 0))

    print("\nBATCHED reverse edge - one query for a page of hits")
    # usage_counts_for_objects backs the L2 -> L4 edge on semantic_search. The reason
    # it exists is cost, so the contract that matters is that it AGREES with
    # documents_for_object -- two paths reporting different amounts of precedent for
    # the same object would be worse than having only the slow one.
    counts = keyword_search.usage_counts_for_objects(
        ["EKKO", "API_CLFN_PRODUCT_SRV", "api_never_seen"])
    check("keys come back uppercased", sorted(counts), ["API_CLFN_PRODUCT_SRV", "EKKO"])
    check("agrees with documents_for_object on artifacts",
          counts["EKKO"]["documents"],
          keyword_search.documents_for_object("EKKO")["total_artifacts"])
    check("and on raw mention count", counts["EKKO"]["mentions"],
          keyword_search.documents_for_object("EKKO")["total_mentions"])
    # Absent rather than zero: the caller skips names with no entry, so a zero row
    # would make it attach an empty prior_usage block to every unused object.
    check("never-seen object is omitted, not zeroed", "API_NEVER_SEEN" in counts, False)
    check("empty input is cheap and empty",
          keyword_search.usage_counts_for_objects([]), {})

    print("\nlesson -> corpus edge (shared object references)")
    # evidence_for_lesson's ranking claim: sharing MORE of the lesson's objects beats
    # mentioning one of them more often. c1 shares both, c3 shares only EKKO.
    docs = keyword_search.documents_for_objects(["EKKO", "I_PurchaseOrder"], limit=5)
    check("document sharing both objects ranks first",
          docs and docs[0]["source"], "PO Interface TD.docx")
    check("and reports HOW MANY it shares", docs and docs[0]["shared_objects"], 2)
    check("the single-object document is still returned",
          sorted(d["source"] for d in docs),
          ["Legacy Report Notes.docx", "PO Interface TD.docx"])
    check("it shares only one",
          next(d["shared_objects"] for d in docs
               if d["source"] == "Legacy Report Notes.docx"), 1)
    # The link has to be explainable, or a reader cannot judge it.
    check("names the shared objects",
          sorted(docs[0]["objects"]) == ["EKKO", "I_PurchaseOrder"], True)
    check("no names -> no documents", keyword_search.documents_for_objects([]), [])
    check("unmatched names -> empty, not everything",
          keyword_search.documents_for_objects(["API_NEVER_SEEN"]), [])

    print("\nsame filename in two folders is TWO documents")
    # The conflation this fixes: both chunks name API_CLFN_PRODUCT_SRV under the
    # filename "Interface Spec.docx", and grouping on the name alone reported one
    # document with 2 mentions.
    check("relative_path reached the index", keyword_search.has_path(), True)
    r = keyword_search.documents_for_object("API_CLFN_PRODUCT_SRV")
    specs = [d for d in r["documents"] if d["source"] == "Interface Spec.docx"]
    check("the collision is two rows, not one", len(specs), 2)
    check("each names its own folder",
          sorted(d["relative_path"] for d in specs),
          ["MM/Interface Spec.docx", "SD/Interface Spec.docx"])
    check("one mention each, not two summed",
          sorted(d["mentions"] for d in specs), [1, 1])
    # The counts have to say both things: how many NAMES and how many LOCATIONS.
    check("distinct filenames still counted by name", r["total_documents"], 2)
    # 3, not 2: MM/, SD/, and Classification FD.docx, which carries no path and so
    # counts as its own location via the coalesce to filename.
    check("but locations are counted too", r["total_locations"], 3)
    check("and it says so in words when they differ",
          "more than one folder" in (r.get("path_note") or ""), True)
    # The lesson-evidence query groups the same way.
    ev = keyword_search.documents_for_objects(
        ["API_CLFN_PRODUCT_SRV", "I_ClfnCharacteristic"], limit=5)
    both = [d for d in ev if d["source"] == "Interface Spec.docx"]
    check("lesson evidence splits them too", len(both), 2)
    check("and the SD copy shares both objects",
          next(d["shared_objects"] for d in both
               if d["relative_path"].startswith("SD/")), 2)
    # BM25 hits carry it, so a reader can tell two same-named hits apart.
    hits = keyword_search.search("Interface classification", k=5)
    check("search hits carry relative_path",
          all("relative_path" in h for h in hits), True)

    print("\nBM25 unaffected by the extra table")
    hits = keyword_search.search("EKKO classical table", k=5)
    check("hits returned with scores",
          bool(hits) and all("keyword_score" in h for h in hits), True)

    print("\nlegacy keyword.db (no mention table) degrades honestly")
    old = TMP / "legacy.db"
    con = sqlite3.connect(str(old))
    con.execute("CREATE TABLE meta (rowid INTEGER PRIMARY KEY, chunk_id TEXT)")
    con.commit()
    con.close()
    keyword_search.DB_PATH = old
    _reset()
    check("has_mentions False", keyword_search.has_mentions(), False)
    check("forward edge returns {}", keyword_search.mentions_for_chunks(["c1"]), {})
    check("reverse edge says indexed=False",
          keyword_search.documents_for_object("EKKO")["indexed"], False)
    # The two new edges must degrade the same way: {} and [] mean "no annotation",
    # which the callers treat as "attach nothing" rather than "no prior usage".
    check("batched counts return {}",
          keyword_search.usage_counts_for_objects(["EKKO"]), {})
    check("lesson evidence returns []",
          keyword_search.documents_for_objects(["EKKO"]), [])
    # A pre-path index must keep the OLD grouping. Splitting on a column that is NULL
    # for every row would report each document as having one unknown location, which
    # is a worse answer than not mentioning paths at all.
    check("has_path False on a legacy index", keyword_search.has_path(), False)

    print()
    if FAILS:
        print("== %d FAILED" % len(FAILS))
        for f in FAILS:
            print("   " + f)
        return 1
    print("== all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
