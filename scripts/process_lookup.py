#!/usr/bin/env python3
"""L1 lookup over the SAP Best Practices process index — what a scope item DOES.

THE QUESTION THIS ANSWERS
    "What does scope item 2LH actually involve?" — its Fiori applications, its
    process steps, the roles that perform them. That is a structured fact about
    657 rows, not a passage of prose, and the brain answers it badly for the same
    reason it answered R-020 badly: the authoritative record is short and loses on
    cosine to any document that discusses the topic at length.

    It also answers the reverse, which nothing else here can: "which scope items
    use the Change Purchase Order app?" That edge only exists because
    applications.json maps process to application, and it is the question asked
    whenever a Fiori app is being extended or replaced.

HOW IT RELATES TO THE OTHER TWO LOOKUPS
    lookup_scope_item   which scope item covers a topic      (679-row catalog)
    lookup_process      what that scope item does            (this file)
    lookup_accelerator  which files document it, and the URL (6,935 rows)

    Three questions, three exact answers, no embeddings. Each can say "no match",
    which a top-k retriever structurally cannot.

WHAT IS AND IS NOT IN THE INDEX
    Built by sapbp_build_process_index.py, which records its own coverage in
    _meta.counts — read it rather than assuming completeness. As of 2026-09-22:
    537 of 657 scope items carry applications and 574 carry steps, so an empty
    `applications` list means SAP published none for that process, not that the
    lookup failed. Capabilities are absent entirely: SAP had published no
    SolutionCapabilityHierarchy rows for the 2608 release.
"""

import re
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX    = BASE_DIR / "brain" / "sapbp" / "raw" / "process_index.json"
BUILD_CMD = "python3.11 scripts/sapbp_build_process_index.py"

_CACHE = None


def load():
    """(rows, meta, problems). Cached: the index is ~1 MB and static per fetch."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not INDEX.exists():
        _CACHE = ([], {}, ["process index not built (%s missing) — run: %s"
                          % (INDEX.name, BUILD_CMD)])
        return _CACHE
    try:
        d = json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception as exc:
        _CACHE = ([], {}, ["process index unreadable: %s" % exc])
        return _CACHE
    _CACHE = (d.get("scope_items") or [], d.get("_meta") or {}, [])
    return _CACHE


def _rank(row, needle):
    """Lower is better. An exact scope item always wins; app/step text is last.

    Text inside `steps` is matched, but ranked below name and application
    matches: a step mentioning "invoice" is weaker evidence that the scope item
    is ABOUT invoicing than its title being "Automated Invoice Settlement".
    """
    if not needle:
        return 6
    if (row.get("scope_item") or "").casefold() == needle:
        return 0
    name = (row.get("name") or "").casefold()
    if name == needle:
        return 1
    if name.startswith(needle):
        return 2
    if re.search(r"\b%s" % re.escape(needle), name):
        return 3
    if any(needle in (a or "").casefold() for a in row.get("applications") or []):
        return 4
    if any(needle in (s or "").casefold() for s in row.get("steps") or []):
        return 5
    return 99


def lookup(query=None, scope_item=None, lob=None, application=None,
           with_steps=True, limit=10):
    """Scope items by id, name, application or step text, plus facet filters.

    `application` is the REVERSE edge: pass a Fiori app name and get every scope
    item that uses it. Matched as a substring so "Purchase Order" finds "Create
    Purchase Order - Advanced", because the caller rarely has the exact label.
    """
    rows, meta, problems = load()
    needle = (query or "").strip().casefold()
    app_q  = (application or "").strip().casefold()

    out = []
    for r in rows:
        if scope_item and (r.get("scope_item") or "").upper() != scope_item.strip().upper():
            continue
        if lob and lob.strip().casefold() not in (r.get("lob") or "").casefold():
            continue
        if app_q and not any(app_q in (a or "").casefold()
                             for a in r.get("applications") or []):
            continue
        score = _rank(r, needle)
        if score == 99:
            continue
        out.append((score, (r.get("scope_item") or ""), r))

    out.sort(key=lambda t: (t[0], t[1]))
    results = []
    for _, _, r in out[:max(1, int(limit or 10))]:
        item = {k: r.get(k) for k in
                ("scope_item", "name", "lob", "change_category",
                 "license_required", "target_release", "applications")}
        item["step_count"] = len(r.get("steps") or [])
        item["role_count"] = len(r.get("roles") or [])
        if with_steps:
            item["steps"] = r.get("steps") or []
            item["roles"] = r.get("roles") or []
        results.append(item)

    return {"total_matches": len(out), "results": results,
            "index_built": meta.get("built_at"),
            "target_release": meta.get("target_release"),
            "coverage": meta.get("counts"), "problems": problems}


def cli():
    import argparse
    ap = argparse.ArgumentParser(description="L1 lookup over the process index")
    ap.add_argument("query", nargs="?", help="Scope item, process name, app or step text")
    ap.add_argument("--scope-item", dest="scope_item")
    ap.add_argument("--lob")
    ap.add_argument("--application", help="Reverse edge: which scope items use this app")
    ap.add_argument("--no-steps", action="store_true")
    ap.add_argument("-k", type=int, default=10)
    a = ap.parse_args()
    res = lookup(a.query, scope_item=a.scope_item, lob=a.lob, application=a.application,
                 with_steps=not a.no_steps, limit=a.k)
    for p in res["problems"]:
        print("!! %s" % p)
    print("%d match(es)" % res["total_matches"])
    for r in res["results"]:
        print("\n  %s  %s" % (r["scope_item"], r["name"]))
        bits = [b for b in (r["lob"], r["change_category"],
                            "release %s" % r["target_release"] if r["target_release"] else None)
                if b]
        if bits:
            print("    %s" % " · ".join(bits))
        if r["applications"]:
            print("    apps  (%d): %s" % (len(r["applications"]),
                                          ", ".join(r["applications"][:6])))
        if r.get("roles"):
            print("    roles (%d): %s" % (r["role_count"], ", ".join(r["roles"][:5])))
        if r.get("steps"):
            print("    steps (%d): %s" % (r["step_count"], " / ".join(r["steps"][:6])))


if __name__ == "__main__":
    cli()
