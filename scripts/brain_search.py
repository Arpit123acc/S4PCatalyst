#!/usr/bin/env python3
"""
Search the Public Cloud Brain — hybrid dense + keyword retrieval.

Two retrievers, fused with Reciprocal Rank Fusion:
  * dense    Bedrock Titan embedding + cosine over the FAISS index (embed_chunks.py)
  * keyword  BM25 over the SQLite FTS5 index (keyword_index.py)

WHAT THE KEYWORD HALF ACTUALLY BUYS -- measured, because the obvious answer is wrong
    The expected justification was exact identifiers: "cosine answers
    API_CLFN_PRODUCT_SRV or 'scope item 1NN' by vibe". Measured 2026-09-04 against
    this corpus, that is mostly FALSE -- Titan v2 embeds rare tokens well, and
    vector-only already returns the right record at rank 1 for
    UnusedParametersRule, CreateObjectRule, EmptyCommandRule, "scope item 4AN",
    "scope item J45" and others. On AlignDeclarationsRule hybrid is slightly WORSE
    (rank 2 -> 4). Do not raise the keyword weight expecting identifier gains; there
    are none to get, and the sweep shows R-020 breaking at 0.40.

    The real win is a different failure: a query dominated by a COMMON word pulls
    the embedding into the wrong cluster, and a small authoritative source cannot
    outvote a large one on term competition. R-062 is the case -- "ATC check profile
    that must pass before a transport" returns SharePoint transport-process
    documents, and the 8-chunk ABAP Cloud standard that answers it verbatim is not
    in the dense top 100 at all. BM25 ranks it 1st at 41.08 against 22.67, because
    IDF makes "ATC" decisive. That is what the lexical half is for here.

    Fusion is by weighted sum of within-list normalised scores, NOT RRF: RRF is
    rank-only by construction, so it discards the margin that makes the above work,
    and measured against the full regression set it churns a third of the
    known-good results (67% baseline overlap against 86% for weighted sum).

Auth: EC2 IAM instance profile (no keys). Filters: phase, agent_role,
deliverable_type, source_system — with vendor docs and SAP catalogs exempt from the
provenance filters (see vectorstore.PROVENANCE_EXEMPT_SOURCES).

Usable two ways:
  * CLI:      python3.11 scripts/brain_search.py "how do we do cutover" --phase Deploy
  * import:   from brain_search import search;  hits = search("...", k=5, phase="Realize")

Mode: --mode hybrid|vector|keyword, or BRAIN_SEARCH_MODE. `keyword` makes no Bedrock
call at all, which makes it the cheap way to sanity-check the lexical half.

Install:
    pip3.11 install boto3 faiss-cpu numpy      # FTS5 ships with stdlib sqlite3
"""

import os
import sys
import json
import argparse
from pathlib import Path
from functools import lru_cache

BASE_DIR   = Path(__file__).resolve().parent.parent
INDEX_DIR  = BASE_DIR / "brain" / "index"
INDEX_PATH = INDEX_DIR / "faiss.index"
META_PATH  = INDEX_DIR / "metadata.json"

REGION     = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID   = os.environ.get("TITAN_MODEL", "amazon.titan-embed-text-v2:0")
MAX_CHARS  = 40_000


@lru_cache(maxsize=1)
def _load():
    """Load and cache the vector store (pluggable backend) + Bedrock client."""
    try:
        import boto3
    except ImportError:
        sys.exit("Missing deps. Run: pip3.11 install boto3 faiss-cpu numpy")
    from vectorstore import get_store
    backend = os.environ.get("BRAIN_BACKEND", "faiss").lower()
    try:
        store = get_store(0, load=True, backend=backend)   # dim inferred on load
    except FileNotFoundError as e:
        sys.exit(str(e))
    except ImportError as e:
        sys.exit(f"Backend '{backend}' deps missing: {e}. "
                 f"pgvector needs: pip3.11 install psycopg2-binary")
    except Exception as e:
        sys.exit(f"Backend '{backend}' connection failed: {e}")
    client = boto3.client("bedrock-runtime", region_name=REGION)
    return store, client, getattr(store, "dim", 1024)


def _embed_query(client, text, dim):
    body = json.dumps({"inputText": text[:MAX_CHARS], "dimensions": dim, "normalize": True})
    resp = client.invoke_model(modelId=MODEL_ID, body=body)
    return json.loads(resp["body"].read())["embedding"]


DEFAULT_MODE = os.environ.get("BRAIN_SEARCH_MODE", "hybrid").lower()
FUSION       = os.environ.get("BRAIN_FUSION", "wsum").lower()      # wsum | rrf
RRF_K        = int(os.environ.get("BRAIN_RRF_K", "60"))
CAND_DEPTH   = int(os.environ.get("BRAIN_CAND_DEPTH", "100"))
# Weight on the lexical half when fusing normalised scores. The dense retriever is
# the better generalist, so BM25 gets the smaller share and earns its keep through
# _promote() when it is decisively right.
#
# 0.25 is the MIDPOINT of the measured passing window, not a hand-tuned optimum.
# Swept against all 38 regression cases 2026-09-04 with the quota enabled: every
# weight in [0.15, 0.35] passes 38/38, and 0.40 fails R-020 (the scope-catalog
# lookup) because BM25 starts preferring long SharePoint documents that repeat the
# phrase over the terse catalog entry that IS the answer. Picking the midpoint keeps
# maximum distance from both edges instead of sitting next to one, and gives the
# lexical half a real quarter-share of the ranking rather than a token vote.
KW_WEIGHT    = float(os.environ.get("BRAIN_KW_WEIGHT", "0.25"))
# Top-N BM25 hits guaranteed a place by rank KW_QUOTA_POS, but only when BM25's top
# score clears KW_QUOTA_MARGIN x the median of its own candidate list. See _promote().
KW_QUOTA        = int(os.environ.get("BRAIN_KW_QUOTA", "1"))
KW_QUOTA_POS    = int(os.environ.get("BRAIN_KW_QUOTA_POS", "3"))
KW_QUOTA_MARGIN = float(os.environ.get("BRAIN_KW_QUOTA_MARGIN", "1.5"))

_warned_no_keyword = False


def _rrf_fuse(rankings, rrf_k=RRF_K):
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank) over each retriever's list.

    KEPT FOR COMPARISON, NOT THE DEFAULT. Measured 2026-09-04 on regression case
    R-062, RRF at the standard k=60 over 100-deep lists gets that case WRONG, and
    the reason is arithmetic rather than tuning: a single rank-1 hit is worth
    1/(k+1) = 0.0164, while a hit present in both lists is worth
    1/(k+r1) + 1/(k+r2), which only falls below 0.0164 once both ranks exceed ~120
    -- past the end of a 100-deep list. So at these parameters ANY double match
    outranks ANY single match, however large the margin. BM25 put the document that
    answers R-062 verbatim at 41.08 against 22.67 for the runner-up; RRF discards
    that margin by construction, and a mediocre-in-both configuration document won.

    That is RRF working as designed -- it is rank-only by definition, which is
    precisely the property that makes it drift-free and the property that loses here.
    Set BRAIN_FUSION=rrf to use it.
    """
    merged = {}
    for name, ranking, _field, _w in rankings:
        for rank, hit in enumerate(ranking, 1):
            key = hit.get("id")
            if not key:
                continue
            slot = merged.get(key)
            if slot is None:
                slot = merged[key] = {"hit": {}, "fused": 0.0, "retrievers": []}
            slot["hit"].update({f: v for f, v in hit.items() if v is not None})
            slot["fused"] += 1.0 / (rrf_k + rank)
            slot["retrievers"].append(name)
    return _finish(merged)


def _wsum_fuse(rankings):
    """Weighted sum of scores min-max normalised WITHIN each candidate list.

    WHY THIS AND NOT PLAIN SCORE FUSION
        Cosine is bounded 0-1; BM25 is unbounded and its scale moves with document
        length and corpus term statistics. Summing them raw is meaningless, and
        summing them against a FIXED scaling constant is worse -- that constant
        drifts as the corpus grows, so retrieval quality would change silently with
        ingest volume. That is the objection that makes RRF attractive.

        Normalising within the returned candidate list answers it: min-max over
        those N hits is scale-free and recomputed per query, so there is no constant
        to drift and no dependence on corpus size. What it preserves, and RRF
        cannot, is MARGIN -- the difference between "top hit by a mile" and "top hit
        by a hair". For a corpus where 44,586 of 49,857 chunks are one source, an
        8-chunk authoritative source can only ever win on margin, so throwing
        margin away is not an acceptable default here.

        A hit absent from a retriever's list contributes 0 from it. Agreement is
        therefore still rewarded (two positive terms), but no longer unconditionally
        -- a decisive single-retriever hit can outrank a lukewarm double.
    """
    merged = {}
    for name, ranking, field, weight in rankings:
        vals = [h.get(field) for h in ranking]
        vals = [v for v in vals if v is not None]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
        span = hi - lo
        for hit in ranking:
            key = hit.get("id")
            if not key:
                continue
            raw = hit.get(field)
            # A single-hit list, or an all-equal one, carries no ranking
            # information; give it a neutral 0.5 rather than a full vote.
            norm = 0.5 if (raw is None or span <= 1e-12) else (raw - lo) / span
            slot = merged.get(key)
            if slot is None:
                slot = merged[key] = {"hit": {}, "fused": 0.0, "retrievers": []}
            slot["hit"].update({f: v for f, v in hit.items() if v is not None})
            slot["fused"] += weight * norm
            slot["retrievers"].append(name)
    return _finish(merged)


def _finish(merged):
    """Emit fused hits, best first, with a fully deterministic order.

    Determinism matters: the regression harness compares result sets, so an
    arbitrary tiebreak would surface as phantom drift on every run.
    """
    out = []
    for slot in merged.values():
        h = slot["hit"]
        h["rrf"] = round(slot["fused"], 6)     # field name kept for compatibility
        h["retrievers"] = slot["retrievers"]
        out.append(h)
    out.sort(key=lambda h: (-h["rrf"], -(h.get("score") or 0.0), str(h.get("id"))))
    return out


def _fuse(rankings):
    return _rrf_fuse(rankings) if FUSION == "rrf" else _wsum_fuse(rankings)


def _promote(fused, khits, quota=None, pos=None):
    """Guarantee the lexical retriever's strongest hits a visible position.

    WHY A QUOTA IS NEEDED AT ALL -- WEIGHT TUNING CANNOT REPLACE IT
        Any averaging fusion scores a hit that ONE retriever missed entirely as
        (its weight x 0) from that side. With 44,586 of 49,857 chunks from a single
        source, an authoritative 8-chunk source is often outside the dense
        retriever's top 100 completely -- so it can be BM25's number 1 by a
        landslide (measured on R-062: 41.08 against 22.67 for the best hit from any
        other document) and still lose to a document that is merely adequate in
        both lists. That is arithmetic, not a bad weight.

        Measured 2026-09-04 across the weight sweep, the whole viable window is a
        knife edge: at kw=0.30 and 0.32 the R-062 document lands at rank 10 of 10 --
        inside the assertion, but with zero headroom, so any corpus growth silently
        pushes it out. At kw=0.35 it improves to 9 while the scope-catalog case
        (R-020) degrades from 5 to 8. There is no weight at which both are safe,
        because the two cases want opposite things.

        So the guarantee is made explicit instead of hoped for: the point of running
        two retrievers is that they disagree, and a fusion that averages away the
        minority signal removes the reason for having the second one. For a
        grounding system, a missed authoritative document is not a ranking
        inaccuracy -- it produces a confidently wrong deliverable.

    COST is bounded and deliberate: at most `quota` slots of the returned k, and
    only when the fused order had them lower. Set BRAIN_KW_QUOTA=0 to disable.
    """
    quota = KW_QUOTA if quota is None else quota
    pos   = KW_QUOTA_POS if pos is None else pos
    if quota <= 0 or not khits:
        return fused

    # CONFIDENCE GATE. A guarantee is only worth spending a slot on when the lexical
    # retriever is actually sure. Measured 2026-09-04 on "AlignDeclarations rule":
    # BM25's top hit was an unrelated XRAY test template at 8.42, which won purely by
    # repeating the common word "rule" -- the abap-cleaner page never matched because
    # `tokenchars '_'` keeps AlignDeclarationsRule as ONE token, so it does not match
    # the term "AlignDeclarations". Promoting that cost a top-3 slot for noise.
    #
    # The test uses only BM25's own distribution -- top score against the median of
    # its candidate list -- so it is scale-free, needs no cross-retriever comparison,
    # and has no constant that drifts as the corpus grows. A landslide (R-062: 41.08
    # against a ~12 median) clears it comfortably; matching one frequent term does not.
    scores = sorted((h.get("keyword_score") or 0.0) for h in khits)
    if scores:
        median = scores[len(scores) // 2]
        top = scores[-1]
        if median > 0 and top < KW_QUOTA_MARGIN * median:
            return fused

    byid = {h.get("id"): h for h in fused}
    out = list(fused)
    for offset, cid in enumerate([h.get("id") for h in khits[:quota] if h.get("id")]):
        h = byid.get(cid)
        if h is None:
            continue
        cur = out.index(h)
        target = min(pos - 1 + offset, len(out) - 1)
        if cur > target:
            out.insert(target, out.pop(cur))
            h["promoted"] = True      # visible in the CLI/UI as `via=...+quota`
    return out


def _keyword_hits(query, depth, filters, hard):
    """BM25 candidates. In hybrid mode a missing index degrades LOUDLY, not silently."""
    global _warned_no_keyword
    try:
        import keyword_search
        if not keyword_search.available():
            raise FileNotFoundError("no keyword index at %s" % keyword_search.DB_PATH)
        return keyword_search.search(query, depth, filters=filters)
    except Exception as e:
        if hard:
            raise
        # Hybrid still answers from the vector half, but at reduced precision on
        # exact identifiers. Announce it once -- a quality regression that looks
        # identical to normal operation is how this project has lost time before.
        if not _warned_no_keyword:
            _warned_no_keyword = True
            print("[brain_search] WARNING: keyword half unavailable (%s); "
                  "falling back to vector-only. Exact-identifier lookups will be "
                  "weaker. Build it: python3.11 scripts/keyword_index.py" % e,
                  file=sys.stderr)
        return []


def search(query, k=5, phase=None, agent_role=None, deliverable_type=None,
           source_system=None, dedup_source=False, mode=None):
    """Return the top-k brain chunks for a query, optionally filtered by metadata.

    Hybrid by default: a dense vector search (Bedrock Titan + cosine) and a BM25
    keyword search are fused with RRF. Override per call with mode=, or globally
    with BRAIN_SEARCH_MODE=vector|keyword|hybrid.

    Filters (applied by the backend): phase, agent_role, deliverable_type,
    source_system (sharepoint / sap_scope_catalog / developer_docs / ...).
    dedup_source=True collapses to one hit per source document (keeps the
    best-ranked chunk of each), so one big file can't fill every slot.

    Each hit: {score, keyword_score, rrf, retrievers, id, source, ...} where
    `score` is ALWAYS the cosine similarity -- including for hits the keyword half
    found on its own, which are scored exactly via the backend rather than left
    null or faked. Ranking is by `rrf`; `score` stays comparable across modes so
    consumers reading it as a 0-1 relevance figure keep working.
    """
    mode = (mode or DEFAULT_MODE).lower()
    if mode not in ("hybrid", "vector", "keyword"):
        raise ValueError("mode must be hybrid | vector | keyword, got %r" % mode)

    store, client, dim = _load()
    filters = {"phase": phase, "agent_role": agent_role,
               "deliverable_type": deliverable_type, "source_system": source_system}
    want  = k * 8 if dedup_source else k       # over-fetch, then collapse dupes
    depth = max(want, CAND_DEPTH) if mode == "hybrid" else want

    # (name, hits, score field, weight) — the weight is only read by _wsum_fuse.
    rankings, qvec, khits = [], None, []
    if mode in ("hybrid", "vector"):
        qvec = _embed_query(client, query, dim)
        vhits = store.search(qvec, depth, filters=filters)
        if vhits:
            rankings.append(("vector", vhits, "score", 1.0 - KW_WEIGHT))
    if mode in ("hybrid", "keyword"):
        khits = _keyword_hits(query, depth, filters, hard=(mode == "keyword"))
        if khits:
            rankings.append(("keyword", khits, "keyword_score", KW_WEIGHT))

    # Always fuse, even for a single ranking: with one list the fused order equals
    # that list's own order, and every hit still carries `retrievers` so callers
    # see one shape regardless of mode.
    raw = _fuse(rankings)
    # Applied BEFORE the trim to k, so a promoted hit displaces a weaker one rather
    # than being appended out of view. Only meaningful when both halves ran.
    if mode == "hybrid":
        raw = _promote(raw, khits)

    if dedup_source:
        seen, unique = set(), []
        for h in raw:                             # raw is rank-ordered → first wins
            if h.get("source") in seen:
                continue
            seen.add(h.get("source"))
            unique.append(h)
            if len(unique) >= k:
                break
        raw = unique
    raw = raw[:k]

    # Backfill cosine for keyword-only hits — after trimming, so this costs at most
    # k lookups rather than one per candidate.
    if qvec is not None:
        need = [h["id"] for h in raw if h.get("score") is None and h.get("id")]
        if need:
            got = store.score_ids(qvec, need)
            for h in raw:
                if h.get("score") is None:
                    h["score"] = got.get(h.get("id"))

    keep = ("score", "keyword_score", "rrf", "retrievers", "promoted", "id",
            "source", "source_system", "phase", "agent_role", "deliverable_type",
            "scope_item_id", "chunk_file")
    return [{k2: h.get(k2) for k2 in keep} for h in raw]


def _read_chunk_text(chunk_file):
    fp = BASE_DIR / "brain" / chunk_file
    try:
        return json.loads(fp.read_text(encoding="utf-8")).get("text", "")
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Natural-language query")
    ap.add_argument("-k", type=int, default=5, help="Number of results")
    ap.add_argument("--phase", help="Filter: Discover/Prepare/Explore/Realize/Deploy/Run")
    ap.add_argument("--agent", dest="agent_role", help="Filter: e.g. build_agent, qe_agent")
    ap.add_argument("--deliverable", dest="deliverable_type", help="Filter: e.g. test_strategy")
    ap.add_argument("--source", dest="source_system",
                    help="Filter: sharepoint | sap_scope_catalog | accelerator_hub | ...")
    ap.add_argument("--dedup", action="store_true",
                    help="Collapse to one hit per source document")
    ap.add_argument("--mode", default=None,
                    help="hybrid (default) | vector | keyword")
    ap.add_argument("--text", action="store_true", help="Print the matched chunk text")
    args = ap.parse_args()

    hits = search(args.query, k=args.k, phase=args.phase,
                  agent_role=args.agent_role, deliverable_type=args.deliverable_type,
                  source_system=args.source_system, dedup_source=args.dedup,
                  mode=args.mode)
    if not hits:
        print("No matches (check filters or that the index is built).")
        return
    for i, h in enumerate(hits, 1):
        tags = " · ".join(str(h[f]) for f in ("phase", "agent_role", "deliverable_type") if h.get(f))
        # `via` is the diagnostic that matters: "both" means the semantic and the
        # lexical retriever agreed, which is a much stronger hit than either alone.
        via = "+".join(sorted(set(h.get("retrievers") or []))) or "?"
        if h.get("promoted"):
            via += "+quota"
        kw = f" bm25={h['keyword_score']}" if h.get("keyword_score") is not None else ""
        print(f"\n[{i}] score={h['score']}{kw} via={via}  {tags}")
        print(f"    source: {h['source']}"
              + (f"  scope={h['scope_item_id']}" if h.get("scope_item_id") else ""))
        if args.text:
            snippet = _read_chunk_text(h["chunk_file"])[:500].replace("\n", " ")
            print(f"    {snippet}...")


if __name__ == "__main__":
    main()
