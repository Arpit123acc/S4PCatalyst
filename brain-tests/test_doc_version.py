#!/usr/bin/env python3
"""Document lifecycle: version parsing, supersession, and the collapsed reverse edge.

WHY A SEPARATE TEST FILE
    Like test_object_mentions.py, this is data-structure correctness rather than
    retrieval quality, so it runs on any laptop against a synthetic corpus instead of
    needing the 49k-chunk brain. Which means it runs BEFORE a change ships.

WHAT IT PINS
    * numeric version ordering -- v10.0 must beat v9.0, which string comparison gets
      backwards, and the real corpus contains exactly that pair;
    * versions (_v3.0, _R2, _Round2) are ORDERED but duplicate suffixes " (1)" are
      NOT -- a downloaded copy is not a newer revision;
    * an explicit "do not use" marker means NOT current even when the document is the
      only member of its family (it came back is_current=True before the fix, which is
      the dangerous way round);
    * end-to-end through keyword.db: the lifecycle columns are populated at index
      time and the reverse edge collapses revisions into artifacts;
    * a pre-lifecycle keyword.db degrades to "no information", never to a wrong claim;
    * the "Copy of ..." and "- BACKUP <date>" copy conventions, including that
      "Backup Strategy.docx" is ordinary vocabulary and must NOT be stripped;
    * words that only LOOK like revision markers stay in the family name. "Final" is
      data-migration load terminology in this corpus ("final load" vs "mock load"),
      and a bare date identifies content rather than ordering it. Those assertions
      exist so a future "improvement" that starts stripping them fails loudly rather
      than silently merging distinct documents.

Usage:
    python brain-tests/test_doc_version.py
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "mcp-server"))

import doc_version as dv                                      # noqa: E402
import keyword_index                                          # noqa: E402
import keyword_search                                         # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="s4pc-lifecycle-"))
DB = TMP / "keyword_test.db"
keyword_index.DB_PATH = DB
keyword_index.INDEX_DIR = TMP
keyword_search.DB_PATH = DB

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-52s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def _row(cid, source, text):
    return {"text": text,
            "meta": {"id": cid, "chunk_id": cid, "source": source,
                     "source_system": "sharepoint", "phase": "General",
                     "agent_role": "general", "deliverable_type": "reference_document",
                     "chunk_file": "sharepoint/chunks/%s.json" % cid,
                     "scope_item_id": None}}


def test_parsing():
    print("\nversion parsing")
    check("_v3.0 -> ordered key", dv.parse("Spec_v3.0.xlsx")["version_key"], (3, 0))
    check("_V0.1 uppercase", dv.parse("Plan_V0.1.xls")["version_key"], (0, 1))
    check("-R2 revision suffix", dv.parse("SUPPLIER-FD-R2")["version_key"], (2,))
    check("_Round2", dv.parse("Data_Round2.xlsx")["version_key"], (2,))
    # A copy index is not a version: treating it as one would make the copy current.
    check("' (1)' is a duplicate, not a version",
          (dv.parse("Rep (1).xlsx")["version_key"], dv.parse("Rep (1).xlsx")["duplicate"]),
          (None, 1))
    check("'(2024)' is not a copy index", dv.parse("Report (2024).xlsx")["duplicate"], None)
    check("no token -> no version", dv.parse("Cutover Plan.xlsx")["version_key"], None)
    check("family strips the token",
          dv.parse("850_Purchase Order_v11.0.xlsx")["family"], "850 purchase order")


def test_copy_conventions():
    print("\ncopy conventions (unordered, like ' (1)')")
    # Real names from the corpus.
    p = dv.parse("Copy of 20240814 ___ATL01_Payment File V1.xlsx")
    check("'Copy of ' prefix marks a duplicate", p["duplicate"], 1)
    check("and collapses to the original's family",
          p["family"], dv.parse("20240814 ___ATL01_Payment File V1.xlsx")["family"])
    p = dv.parse("SAP S4 Public Cloud Security Run Book - BACKUP 1-26-2024.docx")
    check("trailing '- BACKUP <date>' marks a duplicate", p["duplicate"], 1)
    check("and rejoins the live document's family",
          p["family"], dv.parse("SAP S4 Public Cloud Security Run Book.docx")["family"])
    check("the live document wins over its backup",
          dv.pick_current(["Run Book.docx", "Run Book - BACKUP 1-26-2024.docx"]),
          "Run Book.docx")
    # A lone backup is still the only copy there is, so it must stay usable.
    lone = "Run Book - BACKUP 1-26-2024.docx"
    check("a lone backup remains current",
          dv.resolve_families([lone])[lone]["is_current"], True)
    # Anchoring: 'backup' as ordinary vocabulary must not be stripped.
    check("'Backup Strategy' is not a copy", dv.parse("Backup Strategy.docx")["duplicate"], None)
    check("'SAP Backup and Recovery Plan' is not a copy",
          dv.parse("SAP Backup and Recovery Plan.docx")["duplicate"], None)
    check("a copy marker does not eat the version",
          dv.parse("Spec_v2.0 - BACKUP 2024.xlsx")["version_key"], (2, 0))


def test_measured_non_markers():
    """Words that LOOK like revision markers but measured as business vocabulary.

    "Final" is data-migration load terminology in this corpus ("final load" vs "mock
    load"), and bare dates identify content rather than ordering it. Both must be left
    in the family name -- these assertions exist so a future "improvement" that starts
    stripping them fails loudly instead of silently merging distinct documents.
    """
    print("\nmeasured NON-markers stay in the family name")
    check("'Final Load' is not a version",
          dv.parse("Final Load Material Classification.xlsx")["version_key"], None)
    check("a final load and a mock load stay distinct",
          dv.parse("Master recipe Final Load.xlsx")["family"]
          != dv.parse("Master recipe Mock Load.xlsx")["family"], True)
    check("a workshop date is not a version",
          dv.parse("Finance Workshop - Discovery #2 APRIL 24 2023.docx")["version_key"], None)
    check("a data-as-of date stays in the family",
          "2023" in dv.parse("CFIN Plants (11 SEPT 2023).xlsx")["family"], True)
    check("'Final' alone is not an obsolescence marker",
          dv.parse("DDA Export - Final Phase 1.xlsx")["obsolete_marker"], False)


def test_ordering():
    print("\nordering and supersession")
    fam = ["850_Purchase Order_v%d.0.xlsx" % i for i in range(2, 12)]
    # The headline case: lexically "9" > "10", so a string sort picks v9.
    check("v11 beats v10 and v9 (numeric, not string)",
          dv.pick_current(fam), "850_Purchase Order_v11.0.xlsx")
    res = dv.resolve_families(fam)
    check("exactly one current in a 10-version family",
          sum(1 for m in res.values() if m["is_current"]), 1)
    check("superseded members name their successor",
          res["850_Purchase Order_v2.0.xlsx"]["superseded_by"],
          "850_Purchase Order_v11.0.xlsx")
    check("family_size is reported", res["850_Purchase Order_v5.0.xlsx"]["family_size"], 10)
    check("unversioned original beats its own copy",
          dv.pick_current(["Rep.xlsx", "Rep (1).xlsx"]), "Rep.xlsx")


def test_obsolete():
    print("\nobsolescence markers")
    real = "CLIENT NAME- Cutover _InitialPlan- O NO USE THIS.xlsx"   # verbatim from the corpus
    m = dv.resolve_families([real])[real]
    check("'O NO USE THIS' detected despite the missing D", m["obsolete_marker"], True)
    # Regression on the fix: a lone obsolete document must NOT be current.
    check("lone obsolete document is NOT current", m["is_current"], False)
    check("dead, not superseded -> no successor named", m["superseded_by"], None)
    pair = ["Cutover Plan_v1.0.xlsx", "Cutover Plan_v2.0 DO NOT USE.xlsx"]
    res = dv.resolve_families(pair)
    check("a live v1 outranks an obsolete v2",
          res["Cutover Plan_v1.0.xlsx"]["is_current"], True)
    check("OBSOLETE keyword", dv.parse("Plan OBSOLETE.xlsx")["obsolete_marker"], True)
    check("ordinary name is not flagged",
          dv.parse("Cutover Detail Plan.xlsx")["obsolete_marker"], False)
    # "old"/"draft" alone must not trip it -- plenty of live documents carry them.
    check("'old' alone is not an obsolescence marker",
          dv.parse("Old Cutover Plan.xlsx")["obsolete_marker"], False)


def test_ties_are_not_supersession():
    """A tie broken by filename ordering must NOT be reported as a supersession.

    All three names below are verbatim from the corpus, and all three were being
    marked superseded on 2026-09-08 purely because pick_current's deterministic
    tie-break compares filenames: "pptx" > "pdf", "_" > ".", "c" > " ". None of them
    carries a version, a copy marker or an obsolescence marker, so there is no
    evidence of ordering -- and telling a reader to ignore a document on the strength
    of ASCII order is what this module's header exists to forbid.

    Choosing a representative and asserting death are different claims: collapse()
    still needs the first, and only the second needs proof.
    """
    print("\na tie is not evidence of supersession")
    twins = ["Treasury - Process Review.pdf", "Treasury - Process Review.pptx"]
    res = dv.resolve_families(twins)
    check("format twins share a family", res[twins[0]]["family"], res[twins[1]]["family"])
    check("both formats stay current",
          [res[s]["is_current"] for s in twins], [True, True])
    check("and neither claims a successor",
          [res[s]["superseded_by"] for s in twins], [None, None])
    # collapse() must still reduce them to ONE artifact -- the count question is
    # unaffected by refusing to declare a death.
    check("but they still collapse to one artifact",
          len(dv.collapse([{"source": s, "mentions": 2} for s in twins])), 1)
    check("summing the mentions across the pair",
          dv.collapse([{"source": s, "mentions": 2} for s in twins])[0]["mentions"], 4)

    stray = ["SD - Sales order (only open SO).xlsx", "SD - Sales order (only open SO)_.xlsx"]
    r2 = dv.resolve_families(stray)
    check("a stray underscore is not a revision",
          [r2[s]["is_current"] for s in stray], [True, True])
    case = ["OTC- Condition Record for Pricing.xlsx", "OTC-condition record for pricing.xlsx"]
    r3 = dv.resolve_families(case)
    check("case and spacing are not a revision",
          [r3[s]["is_current"] for s in case], [True, True])

    # The other half of the contract: real evidence must STILL supersede. A tie
    # rule that also silenced version ordering would be a worse bug than the one
    # it replaced.
    ev = ["Spec_v1.0.xlsx", "Spec_v2.0.xlsx"]
    r4 = dv.resolve_families(ev)
    check("a version token still supersedes", r4["Spec_v1.0.xlsx"]["is_current"], False)
    check("naming its successor", r4["Spec_v1.0.xlsx"]["superseded_by"], "Spec_v2.0.xlsx")
    dup = ["Rep.xlsx", "Rep (1).xlsx"]
    r5 = dv.resolve_families(dup)
    check("a copy marker still loses to the original",
          r5["Rep (1).xlsx"]["is_current"], False)
    check("exactly one current in an evidenced pair",
          sum(1 for m in r5.values() if m["is_current"]), 1)


def test_windows_copy_suffix():
    """Explorer's own duplicate suffix -- the commonest copy convention in the corpus.

    Measured 2026-09-08: ~28 files in the SAP BPD set carry " - Copy", and it was the
    one copy convention doc_version did not know, so each sat in a family of its own --
    never collapsed for counting, never demoted, competing with its own original for
    top-k. The counter-cases matter as much: a copy somebody then annotated for a team
    is a working document, and "Master Copy" is ordinary vocabulary.
    """
    print("\nWindows ' - Copy' suffix")
    orig = "18J_S4CLD2402_BPD_EN_US.xlsx"
    for name in ("18J_S4CLD2402_BPD_EN_US - Copy.xlsx",          # the plain case
                 "3BU_S4CLD2402_BPD_EN_US- Copy.xlsx",           # no space before dash
                 "BMY_S4CLD2402_BPD_EN_US - Copy - Copy.xlsx",   # a copy of a copy
                 "Cutover Plan (Copy).xlsx"):                    # parenthesised
        check("'%s' is a duplicate" % name[-22:], dv.parse(name)["duplicate"], 1)
    check("and it rejoins the original's family",
          dv.parse("18J_S4CLD2402_BPD_EN_US - Copy.xlsx")["family"],
          dv.parse(orig)["family"])
    check("the original wins over its copy",
          dv.pick_current([orig, "18J_S4CLD2402_BPD_EN_US - Copy.xlsx"]), orig)
    lone = "SL4_S4CLD2402_BPD_EN_US - Copy.xlsx"
    check("a lone copy remains current",
          dv.resolve_families([lone])[lone]["is_current"], True)

    # Anything AFTER the word means it was renamed with intent, not duplicated.
    for name in ("54U_S4CLD2402_BPD_EN_US - Copy - SCM.xlsx",
                 "54V_S4CLD2402_BPD_EN_US - Copy SCM.xlsx"):
        check("'%s' is annotated, not a copy" % name[-20:],
              dv.parse(name)["duplicate"], None)
    # A bare trailing " Copy" with no dash is vocabulary.
    check("'Invoice Master Copy' is not a copy",
          dv.parse("Invoice Master Copy.xlsx")["duplicate"], None)
    check("a copy marker does not eat the version",
          dv.parse("Spec_v2.0 - Copy.xlsx")["version_key"], (2, 0))


def test_successor_keeps_the_format():
    """superseded_by must point inside the format the reader was already in.

    The family ignores the extension so one artifact exported twice counts once. That
    is right for counting and wrong for a successor pointer: measured 2026-09-08,
    "54U_S4CLD2402_BPD_EN_US (1).docx" was told to go read "..._BPD_EN_US.xlsx", and
    for an SAP BPD the .docx is the process narrative while the .xlsx is the step
    table. CLAUDE.md tells agents to read superseded_by INSTEAD of their hit, so the
    pointer has to land on the right document.
    """
    print("\na successor stays in the same format")
    fam = ["54U_S4CLD2402_BPD_EN_US (1).docx", "54U_S4CLD2402_BPD_EN_US (2).xlsx",
           "54U_S4CLD2402_BPD_EN_US.docx", "54U_S4CLD2402_BPD_EN_US.xlsx"]
    res = dv.resolve_families(fam)
    check("the docx copy is sent to the docx",
          res[fam[0]]["superseded_by"], "54U_S4CLD2402_BPD_EN_US.docx")
    check("the xlsx copy is sent to the xlsx",
          res[fam[1]]["superseded_by"], "54U_S4CLD2402_BPD_EN_US.xlsx")
    check("both originals stay current",
          [res[fam[2]]["is_current"], res[fam[3]]["is_current"]], [True, True])
    check("and all four are one artifact",
          len(dv.collapse([{"source": s} for s in fam])), 1)

    # The dangerous case: no plain .docx exists, so the copy is the ONLY narrative.
    sole = ["1P7_S4CLD2402_BPD_EN_US (1).docx", "1P7_S4CLD2402_BPD_EN_US.xlsx",
            "1P7_S4CLD2402_BPD_EN_US - Copy.xlsx"]
    r = dv.resolve_families(sole)
    check("the only docx is NOT demoted to a spreadsheet",
          r[sole[0]]["is_current"], True)
    check("and claims no successor", r[sole[0]]["superseded_by"], None)
    check("while the xlsx copy is still superseded by the xlsx",
          r[sole[2]]["superseded_by"], "1P7_S4CLD2402_BPD_EN_US.xlsx")
    check("still one artifact for counting",
          len(dv.collapse([{"source": s} for s in sole])), 1)


def test_index_roundtrip():
    print("\nend-to-end through keyword.db")
    rows = []
    for i, v in enumerate([2, 3, 11], start=1):
        rows.append(_row("c%d" % i, "850_Purchase Order_v%d.0.xlsx" % v,
                         "EDI mapping for VBAK and VBAP header fields."))
    rows.append(_row("c4", "Cutover Plan- DO NOT USE.xlsx", "Legacy VBAK notes."))
    rows.append(_row("c5", "Clean Design.docx", "Uses I_PurchaseOrder only."))
    keyword_index.build(rows)
    keyword_search.has_mentions.cache_clear()
    keyword_search._lifecycle_cols.cache_clear()
    keyword_search._con.cache_clear()

    check("lifecycle columns present", bool(keyword_search._lifecycle_cols()), True)
    life = keyword_search.lifecycle_for_chunks(["c1", "c2", "c3", "c4", "c5"])
    check("v11 chunk is current", life["c3"]["is_current"], True)
    check("v2 chunk is superseded", life["c2"]["is_current"], False)
    check("superseded chunk names its successor",
          life["c2"]["superseded_by"], "850_Purchase Order_v11.0.xlsx")
    check("obsolete-marked chunk is not current", life["c4"]["is_current"], False)
    check("unversioned standalone is current", life["c5"]["is_current"], True)
    check("version string stored", life["c3"]["doc_version"], "11.0")

    print("\nreverse edge collapses revisions into artifacts")
    r = keyword_search.documents_for_object("VBAK")
    check("4 filenames mention VBAK", r["total_documents"], 4)
    check("but only 2 distinct artifacts", r["total_artifacts"], 2)
    check("collapsed list has 2 entries", len(r["documents"]), 2)
    edi = next((d for d in r["documents"] if "850" in d["source"]), None)
    check("the surviving revision is v11", edi and edi["source"],
          "850_Purchase Order_v11.0.xlsx")
    check("mentions summed across the family", edi and edi["mentions"], 3)
    check("and it says what it folded in", edi and edi["collapsed_versions"], 3)
    check("opting out returns every filename",
          len(keyword_search.documents_for_object("VBAK", collapse_versions=False)["documents"]), 4)


def test_legacy_db():
    print("\npre-lifecycle keyword.db degrades honestly")
    old = TMP / "legacy.db"
    con = sqlite3.connect(str(old))
    con.execute("CREATE TABLE meta (rowid INTEGER PRIMARY KEY, chunk_id TEXT, source TEXT)")
    con.commit()
    con.close()
    keyword_search.DB_PATH = old
    keyword_search._lifecycle_cols.cache_clear()
    keyword_search.has_mentions.cache_clear()
    keyword_search._con.cache_clear()
    check("no lifecycle columns detected", keyword_search._lifecycle_cols(), ())
    # {} rather than a guess: absence of information must not read as "current".
    check("lifecycle lookup returns {}", keyword_search.lifecycle_for_chunks(["c1"]), {})


def main():
    test_parsing()
    test_copy_conventions()
    test_measured_non_markers()
    test_ordering()
    test_obsolete()
    test_ties_are_not_supersession()
    test_windows_copy_suffix()
    test_successor_keeps_the_format()
    test_index_roundtrip()
    test_legacy_db()
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
