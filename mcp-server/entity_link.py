"""Entity linking — find SAP object names in prose and resolve them against the catalog.

Why: `search_brain` returns delivery documents, and those documents name SAP objects.
A 2024 FD citing `API_X` tells you nothing about whether `API_X` is released *today* —
the reader has to notice the name and check it separately, which is exactly the step
that gets skipped. This closes that loop: every brain hit arrives with its object names
already carrying a current release verdict.

Extraction is deliberately CONSERVATIVE. A false positive costs a pointless catalog
lookup and a confusing annotation, so the patterns require a real SAP shape (a known
prefix, or an underscore plus length) and a stoplist removes the words that still slip
through. Missing an object is cheaper than inventing one.

Resolution is done by the caller (server.py owns the catalog), so this module stays
import-free and testable on its own.
"""

import re

# Released / conventional SAP naming shapes. Anchored with word boundaries.
_PATTERNS = (
    r"\bAPI_[A-Z][A-Z0-9_]{2,}\b",           # API_BUSINESS_PARTNER
    r"\b[A-Z][A-Z0-9_]{2,}_SRV\b",           # API_PURCHASEORDER_PROCESS_SRV, ZFOO_SRV
    r"\bCE_[A-Z][A-Z0-9_]{2,}\b",            # CE_PROJDEMANDSOURCEOFSUPPLY_0001
    r"\b[IACRPE]_[A-Z][A-Za-z0-9]{3,}\b",    # I_MaterialStock, C_PurchaseOrderValue
    r"\bBADI_[A-Z][A-Z0-9_]{2,}\b",          # BADI_ATP_R4D_REQUIREMNT_IMPACT
    r"\b[A-Z][A-Z0-9_]{2,}_BADI\b",
    r"\bBAPI_[A-Z][A-Z0-9_]{2,}\b",          # forbidden on Public Cloud — worth flagging
    # Custom namespace, split by case convention: key-user fields are CamelCase
    # (YY1_CustomField) while ABAP objects are upper-case (ZCL_MY_HANDLER). Allowing
    # mixed case after a bare Z/Y would match ordinary words -- "Yesterday" -- so the
    # Z/Y form requires an underscore and all-caps.
    r"\bYY1_[A-Za-z][A-Za-z0-9_]{2,}\b",
    r"\b[ZY][A-Z0-9]*_[A-Z0-9_]{2,}\b",
)
_RE = re.compile("|".join(_PATTERNS))

# Classical tables have no distinguishing shape, so they are listed. Not exhaustive —
# just the ones that actually turn up in delivery documents, where seeing one is a
# clean-core signal worth surfacing.
_CLASSICAL_TABLES = {
    "EKKO", "EKPO", "EKBE", "LFA1", "LFB1", "KNA1", "KNB1", "MARA", "MARC", "MARD",
    "MBEW", "MSEG", "MKPF", "BKPF", "BSEG", "VBAK", "VBAP", "LIKP", "LIPS", "VBRK",
    "VBRP", "AUFK", "COEP", "PRPS", "PROJ", "T001", "T001W", "USR02", "AGR_USERS",
}

# Shapes the regexes match but which are not objects: doc scaffolding, common prose.
_STOP = {
    "A_LOT", "I_THINK", "E_MAIL", "C_LEVEL", "P_VALUE", "R_SQUARED",
    "API_KEY", "API_KEYS", "API_URL", "API_CALL", "API_CALLS", "API_NAME",
    "API_VERSION", "API_ENDPOINT", "API_ACCESS", "API_DOCS", "API_HUB",
}


def extract(text, limit=12):
    """Return distinct candidate SAP object names found in `text`, order preserved.

    `limit` bounds downstream catalog lookups: a long document can name dozens of
    objects and annotating all of them would cost more than it informs.
    """
    if not text:
        return []
    found, seen = [], set()
    for match in _RE.finditer(text):
        name = match.group(0)
        upper = name.upper()
        if upper in _STOP or upper in seen:
            continue
        # Single-token all-caps with no underscore and no released prefix is prose,
        # not an object (the I_/C_/A_ patterns already carry their own prefix).
        if "_" not in name and not re.match(r"^[IACRPE]_", name):
            continue
        seen.add(upper)
        found.append(name)
        if len(found) >= limit:
            break
    for table in _CLASSICAL_TABLES:                 # exact-word classical tables
        if len(found) >= limit:
            break
        if table not in seen and re.search(r"\b%s\b" % table, text):
            seen.add(table)
            found.append(table)
    return found


def annotate(text, resolver, limit=12):
    """Extract names from `text` and resolve each via `resolver(name) -> dict`.

    Returns [{"name", "verdict", "evidence"}], most concerning first, so a
    NOT_AVAILABLE object cannot be buried under a list of healthy ones.
    """
    order = {"NOT_AVAILABLE": 0, "NOT_VERIFIED": 1, "LIKELY_RELEASED": 2}
    out = []
    for name in extract(text, limit=limit):
        try:
            res = resolver(name) or {}
        except Exception:
            continue                                 # never let annotation break a search
        out.append({"name": name,
                    "verdict": res.get("verdict"),
                    "evidence": res.get("evidence")})
    out.sort(key=lambda d: (order.get(d.get("verdict"), 3),
                            0 if d.get("evidence") == "naming_heuristic_only" else 1))
    return out
