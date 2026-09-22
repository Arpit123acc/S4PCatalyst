#!/usr/bin/env python3
"""
L1 lookup over the SAP accelerator catalogs — exact, instant, no embeddings.

WHY THIS IS NOT search_brain
    "Which accelerator covers X, and can I open it?" is a lookup against a few
    thousand titled rows, not a semantic search over 179,482 prose chunks. The
    brain answers it badly for a structural reason: a catalog entry is one short
    string, and a short string cannot out-score paragraphs of prose on cosine, so
    it loses to any document that merely discusses the topic at length. Measured
    on regression case R-020, the scope-catalog entry that WAS the answer sat at
    rank 19 of 20, below an onboarding kit and a RACI matrix, and no ranking
    constant could lift it past rank 10 because the nine hits above it were
    legitimately-scoring prose.

    Retrieval was the wrong instrument, not a badly-tuned one. Rows with a name
    and an id belong in L1, where the match is exact and the answer is the row.

WHAT IT ANSWERS THAT THE BRAIN CANNOT
    The URL and its reachability. Every row carries where the artifact actually
    lives and whether opening it needs a session: `needs_auth` is false for
    help.sap.com and api.sap.com, true for support.sap.com behind SAML. An agent
    that must tell a human "here is the accelerator, and you will need to be
    logged in to SAP for Me" can only do that from here — the brain indexes the
    TEXT of documents and holds no link back to the artifact it came from.

WHY IT SEARCHES THE SCOPE ITEM AND NOT THE TITLE
    Best Practices accelerator titles are almost entirely generic. Across 6,204
    rows there are 55 distinct titles, and 6,127 of them read "Test script",
    "Test script (SAP Cloud ALM)" or "Test script (SAP Help Portal)". Searching
    titles for "supplier invoice" therefore returns nothing at all, which is not
    a ranking weakness but an empty haystack — the business topic is simply not
    in the field. `process_name` is populated for only a minority of rows.

    What every row does carry is its scope item id, and the 679-row scope catalog
    in mcp-server/catalog/scope_items.json maps that id to a description:
    2LH -> "Automated Invoice Settlement". Joining the two is what makes the
    catalog searchable by business topic at all, and it is the same join that
    answers "which scope item covers X" — the question R-020 asks.

TWO CATALOGS, ONE SHAPE
    They disagree, so lookup() normalises them rather than making every caller
    learn both:
      * bom_manifest.json   (~6.2k, Signavio Process Navigator) has access_level
                            and scope_item, but no needs_auth or phase.
      * accelerators.json   (731, SAP Activate Roadmap Viewer) has needs_auth and
                            phase, but no access_level or scope item.
    A field a source genuinely does not publish stays None. It is not inferred:
    a guessed access_level is worse than an absent one, because a caller cannot
    tell the two apart.
"""

import re
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN    = BASE_DIR / "brain"

CATALOGS = {
    "sap_best_practices": BRAIN / "sapbp"       / "raw" / "bom_manifest.json",
    "sap_activate":       BRAIN / "sapactivate" / "raw" / "accelerators.json",
}
BUILD_CMD = {
    "sap_best_practices": "python3.11 scripts/sapbp_catalog.py",
    "sap_activate":       "python3.11 scripts/sapact_catalog.py",
}
SCOPE_CATALOG = BASE_DIR / "mcp-server" / "catalog" / "scope_items.json"

# Imported, never restated. sapact_catalog already decides which hosts serve
# anonymously, and it is the module that wrote needs_auth into accelerators.json
# in the first place — a second copy here would drift from it silently and the
# two catalogs would then disagree about the same host. On ImportError the flag
# becomes None (unknown) rather than defaulting: reporting "needs a login" for
# help.sap.com would send someone hunting for credentials they do not need.
try:
    from sapact_catalog import PUBLIC_HOSTS as _PUBLIC_HOSTS
except Exception:                                          # pragma: no cover
    _PUBLIC_HOSTS = None

_CACHE = {}
_SCOPE = None


def _scope_index():
    """{scope_item_id: {name, lob, business_area, retired}} from the catalog.

    Retired items are indexed too, flagged rather than dropped. An accelerator
    for a retired scope item still exists and may still be what someone is
    holding; saying so is useful, and silently returning nothing is not.
    """
    global _SCOPE
    if _SCOPE is not None:
        return _SCOPE
    _SCOPE = {}
    try:
        d = json.loads(SCOPE_CATALOG.read_text(encoding="utf-8"))
    except Exception:                                      # pragma: no cover
        return _SCOPE
    for key, retired in (("scope_items", False), ("retired_scope_items", True)):
        for it in (d.get(key) or []):
            sid = (it.get("scope_item_id") or "").strip().upper()
            if not sid:
                continue
            _SCOPE[sid] = {
                "name":          it.get("description"),
                "lob":           it.get("lob"),
                "business_area": it.get("business_area"),
                "retired":       bool(it.get("retired")) or retired,
            }
    return _SCOPE


_RELEASE_RE = re.compile(r"S4CLD(\d{4})|S4HANA(\d{4})", re.I)


def _release(url):
    """SAP release encoded in the artifact filename, e.g. 2LH_S4CLD2608_... -> 2608.

    The catalog carries several releases of the same accelerator side by side:
    2LH alone exists as S4CLD2602 and S4CLD2608. Without this they rank in
    arbitrary order and an agent may hand someone the older one, which is the
    exact failure the sap_bpd supersession exists to prevent one layer up.
    Newest sorts first; rows whose filename encodes no release sort after those
    that do, rather than being dropped.
    """
    m = _RELEASE_RE.search(url or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


def _needs_auth(host, url):
    if not url or not _PUBLIC_HOSTS:
        return None
    return (host or "") not in _PUBLIC_HOSTS


def _norm_bom(r):
    host  = r.get("host") or ""
    sid   = (r.get("scope_item") or "").strip().upper() or None
    scope = _scope_index().get(sid or "", {})
    return {
        "source":            "sap_best_practices",
        "id":                r.get("id"),
        "title":             r.get("name"),
        "url":               r.get("url") or None,
        "host":              host or None,
        "access_level":      r.get("access_level"),
        "needs_auth":        _needs_auth(host, r.get("url")),
        "scope_item":        sid,
        # From the scope catalog, because the manifest does not carry it and the
        # title is generic — see the module docstring.
        "scope_item_name":   scope.get("name"),
        "scope_item_retired": scope.get("retired"),
        "lob":               r.get("lob") or scope.get("lob"),
        "process_name":      r.get("process_name"),
        "phase":             [],
        "kind":              r.get("bom_type"),
        "ext":               r.get("ext"),
        "release":           _release(r.get("url")),
    }


def _norm_act(r):
    return {
        "source":            "sap_activate",
        "id":                r.get("id"),
        "title":             r.get("title"),
        "url":               r.get("url") or None,
        "host":              r.get("host") or None,
        "access_level":      None,          # the Roadmap Viewer publishes none
        "needs_auth":        r.get("needs_auth"),
        "scope_item":        None,
        "scope_item_name":   None,
        "scope_item_retired": None,
        "lob":               None,
        "process_name":      None,
        "phase":             r.get("phase") or [],
        "kind":              r.get("kind"),
        "ext":               r.get("ext"),
        "release":           _release(r.get("url")),
    }


def load(source=None):
    """Normalised rows from both catalogs. Returns (rows, problems)."""
    rows, problems = [], []
    for name, path in CATALOGS.items():
        if source and source != name:
            continue
        if not path.exists():
            problems.append("%s catalog not built (%s missing) — run: %s"
                            % (name, path.relative_to(BASE_DIR), BUILD_CMD[name]))
            continue
        if name not in _CACHE:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                problems.append("%s catalog unreadable: %s" % (name, exc))
                continue
            norm = _norm_bom if name == "sap_best_practices" else _norm_act
            _CACHE[name] = [norm(r) for r in raw]
        rows.extend(_CACHE[name])
    if not SCOPE_CATALOG.exists():
        problems.append("scope catalog missing (%s) — Best Practices rows will "
                        "have no business topic to match on."
                        % SCOPE_CATALOG.relative_to(BASE_DIR))
    return rows, problems


def _rank(row, needle):
    """Lower is better. Searches every field that can carry a business topic.

    Ordered so that an exact identifier always beats a text match: asking for
    "2LH" must return 2LH's artifacts first, never a document whose description
    happens to contain the string.
    """
    if not needle:
        return 5
    ident = [(row.get("id") or "").casefold(), (row.get("scope_item") or "").casefold()]
    if needle in ident:
        return 0
    fields = [(row.get("scope_item_name") or "").casefold(),
              (row.get("title") or "").casefold(),
              (row.get("process_name") or "").casefold()]
    if any(f == needle for f in fields):
        return 1
    if any(f.startswith(needle) for f in fields):
        return 2
    if any(re.search(r"\b%s" % re.escape(needle), f) for f in fields):
        return 3
    return 4 if any(needle in f for f in fields) else 99


def lookup(query=None, scope_item=None, phase=None, lob=None, source=None,
           needs_auth=None, limit=20):
    """Find accelerators by topic/id plus exact facet filters."""
    rows, problems = load(source=source)
    needle = (query or "").strip().casefold()

    out = []
    for r in rows:
        if scope_item and (r.get("scope_item") or "").upper() != scope_item.strip().upper():
            continue
        if lob and lob.strip().casefold() not in (r.get("lob") or "").casefold():
            continue
        if phase and not any(phase.strip().casefold() == p.strip().casefold()
                             for p in (r.get("phase") or [])):
            continue
        if needs_auth is not None and r.get("needs_auth") is not needs_auth:
            continue
        score = _rank(r, needle)
        if score == 99:
            continue
        rel = r.get("release")
        out.append((score,
                    (r.get("scope_item_name") or r.get("title") or "").casefold(),
                    -int(rel) if rel and rel.isdigit() else 0,   # newest release first
                    (r.get("title") or ""), r))

    out.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    return {
        "total_matches": len(out),
        "results": [t[4] for t in out[:max(1, int(limit or 20))]],
        "problems": problems,
    }


def scope_items(query=None, limit=20):
    """Scope items whose id or description matches — the R-020 question.

    Separate from lookup() because the answer is a different KIND of thing: a
    scope item is a capability you can activate, an accelerator is a file you
    can open. An agent asking "which scope item covers supplier invoicing" wants
    the former and would have to de-duplicate dozens of test scripts to find it.
    """
    needle = (query or "").strip().casefold()
    out = []
    for sid, rec in _scope_index().items():
        name = (rec.get("name") or "")
        low  = name.casefold()
        if not needle:
            score = 5
        elif needle == sid.casefold():
            score = 0
        elif low == needle:
            score = 1
        elif low.startswith(needle):
            score = 2
        elif re.search(r"\b%s" % re.escape(needle), low):
            score = 3
        elif needle in low:
            score = 4
        else:
            continue
        out.append((score, low, {"scope_item_id": sid, "name": name,
                                 "lob": rec.get("lob"),
                                 "business_area": rec.get("business_area"),
                                 "retired": rec.get("retired")}))
    out.sort(key=lambda t: (t[0], t[1]))
    return {"total_matches": len(out),
            "results": [t[2] for t in out[:max(1, int(limit or 20))]]}


def cli():
    import argparse
    ap = argparse.ArgumentParser(description="L1 lookup over the SAP accelerator catalogs")
    ap.add_argument("query", nargs="?", help="Business topic, title, scope item or id")
    ap.add_argument("--scope-item", dest="scope_item")
    ap.add_argument("--phase")
    ap.add_argument("--lob")
    ap.add_argument("--source", choices=sorted(CATALOGS))
    ap.add_argument("--needs-auth", dest="needs_auth", choices=["yes", "no"])
    ap.add_argument("--scope-items", action="store_true",
                    help="List matching SCOPE ITEMS instead of accelerators")
    ap.add_argument("-k", type=int, default=20)
    a = ap.parse_args()

    if a.scope_items:
        res = scope_items(a.query, limit=a.k)
        print("%d scope item(s)" % res["total_matches"])
        for r in res["results"]:
            flag = "  [RETIRED]" if r["retired"] else ""
            print("  %-5s %s%s" % (r["scope_item_id"], r["name"], flag))
            if r["lob"]:
                print("        %s" % r["lob"])
        return

    res = lookup(a.query, scope_item=a.scope_item, phase=a.phase, lob=a.lob,
                 source=a.source, limit=a.k,
                 needs_auth=None if a.needs_auth is None else a.needs_auth == "yes")
    for p in res["problems"]:
        print("!! %s" % p)
    print("%d match(es)" % res["total_matches"])
    for r in res["results"]:
        auth = {True: "auth", False: "public", None: "?"}[r["needs_auth"]]
        head = r["scope_item_name"] or r["title"]
        print("\n  %s  [%s]" % (head, auth))
        if r["scope_item_name"]:
            print("    %s (%s)" % (r["title"], r["scope_item"]))
        print("    %s" % (r["url"] or "(no url)"))


if __name__ == "__main__":
    cli()
