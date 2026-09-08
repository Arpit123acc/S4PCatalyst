#!/usr/bin/env python3
"""A long-running reader must notice the vector index being rebuilt underneath it.

WHY THIS EXISTS
    _load_dense was @lru_cache(maxsize=1), so a reader held the index it loaded at
    import for the life of the process. Measured 2026-09-08: brain-ui had three days
    of uptime across a full re-ingest, a re-embed and two keyword-index rebuilds, and
    was still answering from the previous 49k-chunk corpus while the MCP served the
    new 36,912-chunk one.

    The failure was worse than stale. Its hits carried chunk ids that no longer existed
    on disk, so the lifecycle and mention joins against the rebuilt keyword.db matched
    nothing, and the viewer rendered no version tags and no object names at all --
    which reads as a corpus that has neither, not as a process needing a restart.

    Restarting readers on refresh fixes the scheduled path only, and that day's
    rebuilds were manual. Hence the cache is keyed on the index files.

Tests the SIGNATURE and the cache decision, not a real FAISS load: the point is
whether a changed file invalidates, and that is decidable without boto3, faiss or a
650 MB index — which is what lets this run on a laptop.

Usage:
    python brain-tests/test_index_reload.py
"""

import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import brain_search as bs                                     # noqa: E402
import vectorstore                                            # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-54s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


TMP = Path(tempfile.mkdtemp(prefix="s4pc-reload-"))
IDX, META = TMP / "faiss.index", TMP / "metadata.json"
vectorstore.FaissStore.INDEX_PATH = IDX
vectorstore.FaissStore.META_PATH = META

LOADS = []


def _fake_load():
    LOADS.append(1)
    return ("store-%d" % len(LOADS), "client", 1024)


bs._load_dense_uncached = _fake_load


def _reset():
    LOADS.clear()
    bs._DENSE_CACHE.update(sig=None, loaded=None)


def main():
    print("signature reflects the files on disk")
    IDX.write_bytes(b"v1")
    META.write_text("[]", encoding="utf-8")
    sig1 = bs._index_signature()
    check("a signature is produced", sig1 is not None and len(sig1) == 2, True)
    check("stable while nothing changes", bs._index_signature(), sig1)

    print("\nan unchanged index is loaded exactly once")
    _reset()
    first = bs._load_dense()
    for _ in range(5):
        bs._load_dense()
    check("one load across six calls", len(LOADS), 1)
    check("and the same object comes back", bs._load_dense(), first)

    print("\na rebuilt index is picked up without a restart")
    # An atomic publish replaces the file; mtime and/or size moves.
    time.sleep(0.01)
    IDX.write_bytes(b"v2-and-longer")
    second = bs._load_dense()
    check("reloaded", len(LOADS), 2)
    check("and the new store is returned", second != first, True)
    check("then cached again", (bs._load_dense(), len(LOADS)), (second, 2))

    print("\nthe metadata half alone also invalidates")
    # metadata.json is positional and 1:1 with the vectors, so a change to either
    # means the pair a reader holds no longer describes the corpus.
    time.sleep(0.01)
    META.write_text('[{"id": "c1"}]', encoding="utf-8")
    bs._load_dense()
    check("reloaded on metadata change", len(LOADS), 3)

    print("\nsame mtime but a different size still invalidates")
    # A coarse-granularity filesystem can land a publish inside the same tick as the
    # file it replaced; size is the second half of the signature for exactly that.
    st = IDX.stat()
    IDX.write_bytes(b"v3-different-length-entirely")
    import os
    os.utime(IDX, (st.st_atime, st.st_mtime))       # force the mtime back
    check("mtime restored", IDX.stat().st_mtime, st.st_mtime)
    bs._load_dense()
    check("reloaded on size change alone", len(LOADS), 4)

    print("\nno index / non-file backend keeps the old cache-forever behaviour")
    _reset()
    IDX.unlink()
    META.unlink()
    check("signature is None", bs._index_signature(), None)
    bs._load_dense()
    bs._load_dense()
    bs._load_dense()
    # None == None, so the cache holds. That is deliberate: with no files to stat
    # there is nothing to invalidate on, and re-loading pgvector per query would be
    # a connection storm.
    check("loaded once and held", len(LOADS), 1)

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
