#!/usr/bin/env python3
"""
Catalog freshness check for the S4PC governance seeds (#4).

Reports how old each released-object catalog is (from _meta.last_curated) and
warns when it exceeds the staleness threshold, pointing at the authoritative
sources to re-sync against.

Run:  python tools/check_catalog_freshness.py
"""
import datetime
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_DIR = os.path.join(REPO, "mcp-server", "catalog")
sys.path.insert(0, CATALOG_DIR)
import db as _catalog_db  # noqa: E402

STALE_DAYS = 90

TABLES = [
    ("cds_views", "_meta_cds_views",
     "https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/5418de55938d1d22e10000000a44147b.html"),
    ("badis",     "_meta_badis",
     "https://help.sap.com/docs/SAP_S4HANA_CLOUD/a630d57fc5004c6383e7a81efee7a8bb/7364d84e76e745df91f1413339a7e293.html"),
    ("apis",      "_meta_apis",
     "https://api.sap.com/products/SAPS4HANACloud/apis/all"),
]


def main():
    today = datetime.date.today()
    stale = []
    con = _catalog_db.get_conn()
    try:
        print("S4PC catalog freshness  (threshold %d days, today %s)" % (STALE_DAYS, today))
        print("  Storage: %s" % _catalog_db.DB_PATH)
        print("-" * 70)
        for table, meta_key, source_url in TABLES:
            count = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            meta = _catalog_db._get_meta(con, meta_key) or {}
            curated = meta.get("last_curated") or meta.get("last_reviewed")
            label = "%-12s (%5d rows)" % (table, count)
            if not curated:
                print("[NO DATE ] %-30s  add last_curated via sync_hub.py" % label)
                stale.append((table, source_url))
                continue
            try:
                age = (today - datetime.date.fromisoformat(curated)).days
            except ValueError:
                print("[BAD DATE] %-30s  %s" % (label, curated))
                stale.append((table, source_url))
                continue
            is_stale = age > STALE_DAYS
            if is_stale:
                stale.append((table, source_url))
            print("[%s] %-30s  curated %s  (%d days old)"
                  % ("STALE  " if is_stale else "fresh  ", label, curated, age))
    finally:
        con.close()

    print("-" * 70)
    if stale:
        print("%d catalog(s) need re-sync — run:" % len(stale))
        print("  python mcp-server/catalog/sync_hub.py --rebuild")
        print()
        print("Authoritative sources:")
        for table, url in stale:
            print("  %-12s  %s" % (table, url))
    else:
        print("All catalogs fresh.")
    sys.exit(1 if stale else 0)


if __name__ == "__main__":
    main()
