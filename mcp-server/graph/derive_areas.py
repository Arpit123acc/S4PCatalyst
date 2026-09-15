#!/usr/bin/env python3
"""
S4PC Digital Brain — propagate business area from the curated seed to the rest of L1.

THE PROBLEM
    271 of 10,736 catalog objects carry a business area (2.5%). The Hub's generic
    artifacts listing supplies none, so area exists essentially only where a human
    wrote it. get_area_map was therefore a view of the seed while reading as the
    catalog's taxonomy, and the area fallback in get_object_graph could not fire for
    the 1,152 objects that are both isolated and arealess.

THE APPROACH
    The 271 labelled objects are training data, and L2 already holds an embedding for
    every object. So the area does not need a new source — it can be propagated.
    For each unlabelled object, take its k nearest LABELLED neighbours in that
    embedding space and adopt their area when they agree.

    Measured by leave-one-out over the 271 labelled objects:

        majority-class baseline   11.8%
        k=1                       81.9%
        k=3                       82.7%     <- chosen
        k=5                       80.1%
        k=3 + unanimous + >=0.55  97.8%  on 137 of 271 (the confident half)

    Only the CONFIDENT subset is emitted. Half the catalog at ~98% precision beats all
    of it at 83%: a wrong area is a wrong turn for whoever trusts it, and this layer's
    whole discipline is refusing to state what it cannot back.

WHY A DERIVED AREA IS LEGITIMATE HERE
    Area is a NAVIGATION aid, not a release contract. Getting one wrong sends a reader
    to the wrong browse list; getting a verdict wrong puts an unreleased object in a
    client deliverable. That asymmetry is why a derived area may ship with its
    provenance attached, while `evidence: naming_heuristic_only` must never be written
    up as released. Derived values land in `areas_derived`, never in `areas`, and every
    node carries `area_source` so the two can never be confused downstream.

SEQUENCING — this reads BOTH derived stores, so run it last:
    python mcp-server/graph/build_graph.py       # writes graph.json
    python mcp-server/vector/build_index.py      # writes index.npy
    python mcp-server/graph/derive_areas.py      # this

    build_graph.py REWRITES graph.json, so a graph rebuild drops the derived areas and
    this must run again. That is deliberate: a stale derived area on a rebuilt graph
    would be worse than none.

Requires numpy (already required by the vector engine). Never required by L1 itself.
"""

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_HERE)

GRAPH_PATH = os.path.join(_HERE, "graph.json")
EMBED_PATH = os.path.join(BASE_DIR, "vector", "index.npy")
INDEX_PATH = os.path.join(BASE_DIR, "vector", "index.json")

CATALOG_TYPES = ("api", "cds_view", "badi")

# UNANIMITY IS THE PRECISION LEVER. THE SIMILARITY FLOOR IS NOT — READ BEFORE TUNING.
#
#   thresh  unanimous  LOO precision  LOO n  derived  coverage
#   0.40    yes        97.8%          137    2065     21.8%
#   0.55    yes        97.8%          770 -> 770       9.7%
#   0.55    no         82.6%          270    1380     15.4%
#
# Precision is 97.8% at EVERY threshold from 0.40 to 0.60 when unanimous, and ~82.7%
# at every threshold when not. Dropping unanimity costs 15 points; moving the floor
# costs nothing measurable.
#
# So why keep 0.55? Because `LOO n` is 137 at 0.40, 0.45, 0.50 AND 0.55 — no labelled
# object has its nearest neighbour in that band, so the sweep cannot tell those values
# apart. It is flat because it is inactive, not because it is safe: see the
# flat-sweep lesson. Lowering to 0.40 would add ~1,295 objects drawn from a band the
# measurement never sampled, and the 97.8% figure — the thing that makes a DERIVED
# area publishable at all — would not cover them.
#
# Raise coverage by labelling more seed objects, not by loosening this.
K          = 3
UNANIMOUS  = True      # all K neighbours must name the same area — the real guard

# PER BACKEND, because a cosine floor is not portable between embedding models and
# the two measure differently in kind, not just in value:
#
#   dense/384d (MiniLM)   sample 137 at 0.40, 0.45, 0.50 AND 0.55 — frozen. The floor
#                         is INACTIVE across that range, so its flat 97.8% is measuring
#                         nothing there. Lowering it would ship ~1,295 objects from a
#                         band the sweep never sampled. Stay at 0.55.
#   bedrock/1024d (Titan) sample 115/113/109/107 across the same floors — active, and
#                         precision holds at 98.3/98.2/98.2/98.1. 0.40 is therefore
#                         evidenced, and derives 1,129 instead of 287 (11.9% vs 4.8%).
#
# Same table, opposite conclusion, because what matters is whether the sample moves.
# An unlisted backend gets the conservative floor and NO precision claim — see
# get_area_map, which refuses to quote a figure measured elsewhere.
#
# (min_similarity, measured_precision, leave-one-out sample size)
CALIBRATION = {
    "bedrock/1024d": (0.40, 0.983, 115),
    "dense/384d":    (0.55, 0.978, 137),
}
DEFAULT_CALIBRATION = (0.55, None, 0)


def _fail(msg):
    print("ERROR: %s" % msg)
    sys.exit(1)


def measure(np, lab_mat, lab_area, unl_mat, n_nodes, n_lab):
    """Leave-one-out precision vs coverage, for THIS host's embedding backend.

    WHY THIS IS A FLAG AND NOT A NUMBER IN A COMMENT
        The first version hardcoded 0.55 and a 97.8% precision claim measured on a
        laptop running MiniLM at 384 dims. The delivery host runs Bedrock Titan at
        1024 dims, where the same floor declined 11,167 of 11,454 objects instead of
        9,695 of 10,465 — and the precision figure travelled with it, unmeasured.

        semantic_search already warns that thresholds are NOT comparable across
        backends. The calibration therefore belongs with the code, runnable wherever
        the index actually lives, rather than as a constant someone once measured
        somewhere else.

    Run:  python mcp-server/graph/derive_areas.py --measure
    """
    sim_ll = lab_mat @ lab_mat.T
    np.fill_diagonal(sim_ll, -1.0)
    top_ll = np.argsort(sim_ll, axis=1)[:, ::-1][:, :K]
    sim_ul = unl_mat @ lab_mat.T
    top_ul = np.argsort(sim_ul, axis=1)[:, ::-1][:, :K]

    print("  %-7s %-9s %-11s %-7s %-9s %s"
          % ("floor", "unanim", "precision", "sample", "derived", "coverage"))
    print("  " + "-" * 62)
    rows = []
    for floor in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65):
        for unan in (True, False):
            ok = tot = 0
            for r in range(lab_mat.shape[0]):
                c = top_ll[r]
                votes = Counter(lab_area[j] for j in c)
                pred, nv = votes.most_common(1)[0]
                if sim_ll[r][c[0]] < floor or (unan and nv != K):
                    continue
                tot += 1
                ok += (pred == lab_area[r])
            d = 0
            for r in range(unl_mat.shape[0]):
                c = top_ul[r]
                nv = Counter(lab_area[j] for j in c).most_common(1)[0][1]
                if sim_ul[r][c[0]] < floor or (unan and nv != K):
                    continue
                d += 1
            prec = (100.0 * ok / tot) if tot else None
            rows.append((floor, unan, prec, tot, d))
            print("  %-7.2f %-9s %-11s %-7d %-9d %.1f%%"
                  % (floor, "yes" if unan else "no",
                     ("%.1f%%" % prec) if prec is not None else "n/a",
                     tot, d, 100.0 * (n_lab + d) / n_nodes))

    print()
    print("  READ IT LIKE THIS: a floor whose `sample` does not change as you lower it")
    print("  is INACTIVE over that range, not safe — its flat precision is measuring")
    print("  nothing. Pick the lowest floor at which `sample` is still moving, and")
    print("  prefer unanimity: on every backend so far it is worth ~15 points and the")
    print("  floor is worth none.")


def main():
    try:
        import numpy as np
    except ImportError:
        _fail("numpy is required (it already backs the vector engine)")

    if not os.path.exists(GRAPH_PATH):
        _fail("graph not built — run: python mcp-server/graph/build_graph.py")
    if not os.path.exists(EMBED_PATH) or not os.path.exists(INDEX_PATH):
        _fail("L2 index not built — run: python mcp-server/vector/build_index.py")

    with open(GRAPH_PATH, encoding="utf-8") as fh:
        graph = json.load(fh)
    with open(INDEX_PATH, encoding="utf-8") as fh:
        index = json.load(fh)
    matrix = np.load(EMBED_PATH)

    nodes = graph.get("nodes") or {}
    docs  = index.get("docs") or []
    if len(docs) != matrix.shape[0]:
        _fail("index.json (%d docs) and index.npy (%d rows) disagree — rebuild L2"
              % (len(docs), matrix.shape[0]))

    print("S4PC Digital Brain — deriving business areas (L2 -> L1)...")
    print("  graph: %d nodes   index: %d docs x %d dims"
          % (len(nodes), matrix.shape[0], matrix.shape[1]))

    lab_rows, lab_area = [], []
    unl_rows, unl_name = [], []
    for i, d in enumerate(docs):
        if d.get("type") not in CATALOG_TYPES:
            continue
        name = d.get("id")
        node = nodes.get(name)
        if node is None:
            continue                                   # indexed but not a graph node
        if (node.get("area") or "").strip():
            lab_rows.append(i)
            lab_area.append(node["area"].strip())
        else:
            unl_rows.append(i)
            unl_name.append(name)

    print("  labelled: %d    unlabelled: %d" % (len(lab_rows), len(unl_rows)))
    if len(lab_rows) < 30:
        _fail("only %d labelled objects — too few to propagate from" % len(lab_rows))
    if not unl_rows:
        print("  nothing to derive."); return

    lab_mat = matrix[np.array(lab_rows)]               # rows are already L2-normalised

    backend = "%s/%dd" % (index.get("engine") or "?", matrix.shape[1])
    if "--measure" in sys.argv:
        print("\n  calibration for THIS backend: %s\n" % backend)
        measure(np, lab_mat, lab_area, matrix[np.array(unl_rows)], len(nodes), len(lab_rows))
        return

    min_sim, precision, sample = CALIBRATION.get(backend, DEFAULT_CALIBRATION)
    if backend in CALIBRATION:
        print("  backend: %s   floor %.2f (measured %.1f%% on n=%d)"
              % (backend, min_sim, 100.0 * precision, sample))
    else:
        print("  backend: %s   floor %.2f — NOT CALIBRATED for this backend, using the"
              % (backend, min_sim))
        print("           conservative default. Run --measure to calibrate it.")

    derived, declined = {}, 0
    by_area = Counter()

    # Chunked so a 10k x 271 product never materialises more than a few MB at once.
    CHUNK = 512
    unl_arr = np.array(unl_rows)
    for start in range(0, len(unl_arr), CHUNK):
        block = matrix[unl_arr[start:start + CHUNK]]
        sims  = block @ lab_mat.T                      # cosine, both normalised
        top   = np.argsort(sims, axis=1)[:, ::-1][:, :K]
        for r in range(block.shape[0]):
            name  = unl_name[start + r]
            cand  = top[r]
            areas = [lab_area[c] for c in cand]
            best  = float(sims[r][cand[0]])
            votes = Counter(areas)
            area, n = votes.most_common(1)[0]
            if best < min_sim:
                declined += 1
                continue
            if UNANIMOUS and n != K:
                declined += 1
                continue
            derived[name] = {"area": area,
                             "confidence": round(best, 4),
                             "agreement": "%d/%d" % (n, K),
                             "nearest": nodes_name_at(docs, lab_rows[cand[0]])}
            by_area[area] += 1

    # Parallel to `areas`, never merged into it. `areas` is what get_area_map has always
    # returned and what the coverage stat counts as curated; mixing derived values in
    # would make a guess indistinguishable from a human's decision at every call site.
    graph["areas_derived"] = derived
    stats = graph.setdefault("stats", {})
    # save_graph sets this on every rebuild, because a rebuild drops the block below.
    # Clearing it here is what makes "no derived areas" distinguishable from "the
    # derivation has not run since the graph was last rebuilt".
    stats.pop("areas_derivation_pending", None)
    stats["areas_derived"]        = len(derived)
    stats["areas_curated_objects"] = len(lab_rows)
    stats["areas_declined"]       = declined
    # measured_precision is None on an uncalibrated backend, and get_area_map then
    # refuses to quote a figure rather than borrowing one from a machine that measured
    # a different embedding model.
    stats["areas_derive_params"]  = {"k": K, "min_similarity": min_sim,
                                     "unanimous": UNANIMOUS,
                                     "backend": backend,
                                     "measured_precision": precision,
                                     "measured_sample": sample}

    with open(GRAPH_PATH, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, ensure_ascii=False, separators=(",", ":"))

    total = len(nodes)
    cov_before = 100.0 * len(lab_rows) / total
    cov_after  = 100.0 * (len(lab_rows) + len(derived)) / total
    print("  derived: %d    declined: %d (below %.2f similarity, or neighbours disagreed)"
          % (len(derived), declined, min_sim))
    print("  coverage: %.1f%% -> %.1f%%" % (cov_before, cov_after))
    print("\n  Top derived areas:")
    for area, n in by_area.most_common(10):
        print("    %-40s %5d" % (area, n))
    print("\nWritten -> %s" % GRAPH_PATH)
    print("NOTE: build_graph.py rewrites graph.json — re-run this after any rebuild.")


def nodes_name_at(docs, row):
    d = docs[row] if 0 <= row < len(docs) else {}
    return d.get("id") or ""


if __name__ == "__main__":
    main()
