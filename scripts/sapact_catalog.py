#!/usr/bin/env python3
"""
Fetch the SAP Activate roadmap accelerator catalogue from SAP for Me's Roadmap Viewer.

WHY THIS EXISTS -- AND WHY IT MATTERS MORE THAN ITS SIZE SUGGESTS
    731 curated methodology documents, and every one arrives already tagged by
    PHASE, WORKSTREAM, ROLE, TOPIC and PRODUCT. That tagging is the point. A brain
    serving several agents needs facets an agent can filter on exactly -- "Explore
    material for a Testing Expert" -- rather than hoping a similarity search
    surfaces the right thing. SAP has done that classification for us.

    Compare the Process Navigator accelerators (scripts/sapbp_catalog.py): 6,204
    items of which 98.8% are the same test scripts in two file formats, uniformly
    SAPCUSTOMER, 92% behind SAML. This source is a fifth of the size, almost all
    distinct, and ~64% sits on public hosts needing no session at all.

THE FACET CONTRACT (what downstream agents filter on)
    phase        Discover | Prepare | Explore | Realize | Deploy | Run   (multi-valued)
    workstream   from T_WKS tags, e.g. Testing, Operations and Support
    role         from T_ROLE tags, e.g. Configuration Expert, Technology Expert
    topic        from T_TAG  tags, e.g. SAP Build, Hybrid Deployment
    product      from T_PDS  tags, e.g. SAP Business Technology Platform
    audience     Public | SAP Customer | SAP Partner   (parsed from the title)
    needs_auth   true when the URL host requires an SAP session

    An accelerator belongs to SEVERAL phases (`pc` is ";"-separated), so phase is a
    list. Flattening it to one value would silently hide material from the agents
    that need it most -- Prepare/Explore overlap heavily.

LAYERS
    This writes a CATALOGUE, which belongs in L1: "which accelerators exist for
    Explore and a Testing Expert" is an exact filter, not a semantic question, and
    L1 cannot hallucinate an answer the way a nearest-neighbour search can. The
    DOCUMENT TEXT behind each url is L4, fetched separately, carrying these facets
    as chunk metadata so the two layers cross-reference.

ENDPOINTS (found by reading a HAR of the Roadmap Viewer UI, 2026-09-19)
    /api/v1/roadmap/accelerators/{id}   the 731 rows        -- one call, no paging
    /api/v1/roadmap/phases/{id}         phase code -> name
    /api/v1/roadmap/panel/{id}          the tag dictionary  -- without this the
                                        tags are opaque codes like "w8;t20;r49"

USAGE
    export SAPME_COOKIE='<Cookie header from a pr.alm.me.sap.com request>'
    python3.11 scripts/sapact_catalog.py --dry-run
    python3.11 scripts/sapact_catalog.py
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "brain" / "sapactivate"
RAW_DIR = OUT_DIR / "raw"
MANIFEST = OUT_DIR / "manifest.json"

SERVICE = "https://pr.alm.me.sap.com/ui/act-rmv-service-ui/v1/api/v1"
# "SAP Activate for SAP S/4HANA Cloud Public Edition"
DEFAULT_ROADMAP = "82b2db84548d41209cda972f0fac428b"

# Hosts that serve without an SAP session. Measured, not assumed: support.sap.com
# /content/dam returns HTTP 200 with a SAML auto-submit form, which is why this is
# an allowlist of known-public hosts rather than a denylist of known-private ones.
# An unrecognised host is treated as needing auth -- the safe direction.
PUBLIC_HOSTS = {
    "help.sap.com", "learning.sap.com", "www.sap.com", "sap.com",
    "blogs.sap.com", "community.sap.com", "pages.community.sap.com",
    "s4hanacloud.community.sap", "discovery-center.cloud.sap",
    "dam.sap.com", "d.dam.sap.com", "api.sap.com", "cap.cloud.sap", "ui5.sap.com",
}

TAG_TYPE = {"T_WKS": "workstream", "T_ROLE": "role",
            "T_TAG": "topic", "T_PDS": "product"}

SAML_MARKERS = ("samlform", "accounts.sap.com/saml2", "login.support.html")
UA = "S4PC-Catalyst-brain-ingest/1.0 (internal delivery accelerator)"

# See sapbp_catalog.py for why: corporate TLS interception breaks verification on a
# managed laptop, and disabling verification is never the answer when the request
# carries a session cookie.
try:
    import truststore                      # noqa: PLC0415

    truststore.inject_into_ssl()
except ImportError:
    pass


class NotAuthenticated(RuntimeError):
    pass


def _get(path):
    cookie = os.environ.get("SAPME_COOKIE", "").strip()
    if not cookie:
        sys.exit("SAPME_COOKIE is unset. Copy the Cookie header from a "
                 "pr.alm.me.sap.com request in DevTools and export it.")
    req = urllib.request.Request(f"{SERVICE}/{path}")
    req.add_header("Cookie", cookie)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    req.add_header("X-Requested-With", "XMLHttpRequest")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NotAuthenticated(f"HTTP {e.code} — session rejected") from e
        raise
    low = body.lstrip()[:2000].lower()
    if any(m in low for m in SAML_MARKERS) or low.startswith("<"):
        raise NotAuthenticated("got an HTML login page instead of JSON — the "
                               "cookie is missing, expired, or from the wrong host")
    return json.loads(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roadmap", default=DEFAULT_ROADMAP)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    print(f"== service  {SERVICE}")
    print(f"== roadmap  {a.roadmap}")

    summary = _get(f"roadmap/summary/{a.roadmap}")
    phases = _get(f"roadmap/phases/{a.roadmap}")
    panel = _get(f"roadmap/panel/{a.roadmap}")
    accel = _get(f"roadmap/accelerators/{a.roadmap}")

    print(f"== name     {summary.get('roadmapName')}  [{summary.get('activeLanguage')}]")
    print(f"== phases   {len(phases)}   tags {len(panel)}   accelerators {len(accel)}")

    phase_by_pos = {str(p["position"]): p["shortText"] for p in phases}
    # shortId -> (facet, label). Without the panel call these stay codes.
    tag_by_short = {t["shortId"]: (TAG_TYPE.get(t.get("type"), "other"), t.get("text"))
                    for t in panel}

    # Audience is only in the title, as a trailing "(Public)" / "(SAP Customer)" /
    # "(SAP Partner)". Kept as a facet rather than a filter: an agent should still
    # be able to CITE a partner-only document, it just needs to say so.
    aud_re = re.compile(r"\((Public|SAP Customer|SAP Partner)\)\s*$", re.I)

    rows, facets = [], {k: Counter() for k in
                        ("phase", "workstream", "role", "topic", "product",
                         "audience", "utype", "host")}
    for r in accel:
        url = r.get("url") or ""
        host = urllib.parse.urlparse(url).netloc
        name = (r.get("name") or "").strip()
        m = aud_re.search(name)
        audience = m.group(1).title().replace("Sap", "SAP") if m else "Unspecified"
        title = aud_re.sub("", name).strip()

        ph = [phase_by_pos.get(p, p) for p in str(r.get("pc") or "").split(";") if p]
        buckets = {"workstream": [], "role": [], "topic": [], "product": [], "other": []}
        for code in str(r.get("tags") or "").split(";"):
            if not code:
                continue
            facet, label = tag_by_short.get(code, ("other", code))
            buckets[facet].append(label)

        rows.append({
            "id": r.get("id"),
            "title": title,
            "audience": audience,
            "url": url,
            "host": host,
            "needs_auth": bool(url) and host not in PUBLIC_HOSTS,
            "kind": r.get("utype"),                 # WEB_PAGE | FILE
            "ext": os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or None,
            "phase": ph,
            "workstream": buckets["workstream"],
            "role": buckets["role"],
            "topic": buckets["topic"],
            "product": buckets["product"],
            "roadmap_id": a.roadmap,
            "roadmap_name": summary.get("roadmapName"),
        })
        for p in ph:
            facets["phase"][p] += 1
        for k in ("workstream", "role", "topic", "product"):
            for v in buckets[k]:
                facets[k][v] += 1
        facets["audience"][audience] += 1
        facets["utype"][r.get("utype")] += 1
        facets["host"][host or "(none)"] += 1

    need = sum(1 for r in rows if r["needs_auth"])
    print(f"\n== {len(rows)} accelerators: {len(rows) - need} public, {need} need a session")
    for k in ("phase", "role", "workstream", "audience", "utype"):
        print(f"\n== {k}")
        for v, n in facets[k].most_common(10):
            print(f"   {n:>5}  {v}")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "accelerators.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (RAW_DIR / "tag_dictionary.json").write_text(
        json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "service": SERVICE,
        "roadmap_id": a.roadmap,
        "roadmap_name": summary.get("roadmapName"),
        "language": summary.get("activeLanguage"),
        "counts": {"accelerators": len(rows), "public": len(rows) - need,
                   "needs_auth": need, "phases": len(phases), "tags": len(panel)},
        "facets": {k: dict(v) for k, v in facets.items()},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {RAW_DIR}/accelerators.json, {RAW_DIR}/tag_dictionary.json")
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotAuthenticated as exc:
        sys.exit(f"\nNOT AUTHENTICATED: {exc}")
