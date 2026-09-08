#!/usr/bin/env python3
"""Chunk files whose source document is gone must be removed — and nothing else.

WHY THIS EXISTS
    _stale_chunks removes the surplus when a document re-chunks into FEWER files. It is
    keyed on that document's own doc_id, so it covers a document that CHANGED and
    cannot cover one that went away: doc_id is md5(relative_path), so a rename, a move
    or a deletion produces a new id (or none), the old chunks are never popped, and
    they stay in the corpus for good. The embedder then indexes text that exists in no
    source file.

    Measured 2026-09-08, the same accumulation had already left 21,950 orphans in the
    S3 backup, where nothing pruned either. The local tree had escaped only because
    paths happened to be stable between runs.

WHAT IT PINS, AND WHY THE SECOND ONE MATTERS MOST
    * a deleted / renamed / moved source loses its chunks;
    * a document that merely FAILED TO EXTRACT this run keeps its chunks. Six files
      fail extraction on every run of the real corpus, and they raise before
      _stale_chunks is reached, so their entries are still in the index at the end.
      Pruning "whatever is left over" would delete good, recoverable content on a
      transient parse failure. The expectation is therefore derived from the FILE LIST,
      not from what the loop managed to process;
    * a mass-orphan event is REFUSED, because a partially-mounted raw/ folder looks
      exactly like a mass deletion and the wrong guess is unrecoverable.

Usage:
    python brain-tests/test_orphan_prune.py
"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import sharepoint_ingest as si                                # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-52s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def _fresh(chunks_by_doc):
    """Rebuild a synthetic chunks/ tree and reset the module's per-run state."""
    tmp = Path(tempfile.mkdtemp(prefix="s4pc-orphan-"))
    si.CHUNKS_DIR = tmp / "chunks"
    si.CHUNKS_DIR.mkdir(parents=True)
    for doc, n in chunks_by_doc.items():
        for i in range(n):
            (si.CHUNKS_DIR / ("%s_%04d.json" % (doc, i))).write_text("{}", encoding="utf-8")
    si._CHUNK_INDEX = None
    return tmp


def _live():
    return sorted(p.name for p in si.CHUNKS_DIR.rglob("*.json"))


def main():
    print("doc_id is stable for a path and changes when the path changes")
    a = si.doc_id_for("MM/Spec.docx")
    check("same path -> same id", si.doc_id_for("MM/Spec.docx"), a)
    check("moved file -> different id", si.doc_id_for("SD/Spec.docx") != a, True)
    check("8 hex chars", len(a) == 8 and all(c in "0123456789abcdef" for c in a), True)

    print("\na source that is gone loses its chunks")
    # The corpus has to be realistically sized for this: 2 orphans out of 5 files is
    # 40% and the guard rightly refuses it, whereas the real event is 2 out of 35,814.
    # The first draft of this test used 5 files and read the guard firing as a bug.
    keep, gone = si.doc_id_for("keep.docx"), si.doc_id_for("gone.docx")
    _fresh({keep: 20, gone: 2})
    n = si.prune_orphan_chunks({keep})
    check("2 orphan files removed", n, 2)
    check("only the surviving document's chunks remain",
          all(p.startswith(keep) for p in _live()) and len(_live()) == 20, True)

    print("\nTHE DANGEROUS CASE: extraction failed, but the file still exists")
    # This document raised before _stale_chunks, so it was never popped from the index.
    # It is still in raw/, so it must keep the chunks a previous run produced.
    broken = si.doc_id_for("corrupt.pptx")
    _fresh({keep: 2, broken: 4})
    n = si.prune_orphan_chunks({keep, broken})       # both still in the file list
    check("nothing pruned", n, 0)
    check("the unparseable document keeps its chunks",
          len([p for p in _live() if p.startswith(broken)]), 4)

    print("\na re-chunked document is not treated as an orphan")
    # _stale_chunks pops it during the run; the leftovers it deliberately left behind
    # belong to the SAME doc_id, which is still expected, so pruning must not fire.
    _fresh({keep: 5})
    si._stale_chunks(keep)                            # simulate the in-run reclaim
    check("no orphans after a normal re-chunk",
          si.prune_orphan_chunks({keep}), 0)

    print("\nmass-orphan events are refused, not obeyed")
    ids = [si.doc_id_for("doc%02d.docx" % i) for i in range(10)]
    _fresh({d: 4 for d in ids})
    # Only one document still present: 90% of the corpus would be deleted.
    n = si.prune_orphan_chunks({ids[0]})
    check("refused", n, 0)
    check("every file survives", len(_live()), 40)
    check("and --allow-shrink overrides it",
          si.prune_orphan_chunks({ids[0]}, allow_shrink=True), 36)
    check("leaving only the surviving document", len(_live()), 4)

    print("\njust under the ceiling still prunes")
    # 20 documents, 4 files each; 4 documents gone = 16/80 = 20%, below the 25% ceiling.
    ids = [si.doc_id_for("d%02d.docx" % i) for i in range(20)]
    _fresh({d: 4 for d in ids})
    check("pruned without an override",
          si.prune_orphan_chunks(set(ids[4:])), 16)

    print("\ndegradation")
    _fresh({})
    check("empty tree", si.prune_orphan_chunks({si.doc_id_for("x")}), 0)
    si.CHUNKS_DIR = Path(tempfile.mkdtemp(prefix="s4pc-orphan-none-")) / "missing"
    si._CHUNK_INDEX = None
    check("absent chunks dir", si.prune_orphan_chunks({"deadbeef"}), 0)

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
