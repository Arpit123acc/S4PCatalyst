"""Document lifecycle — which corpus documents are current, and which are superseded.

WHY THIS EXISTS
    Measured 2026-09-07, the first time the corpus could be asked: the reverse-edge
    lookup for VBAK reported "104 mentions across 21 documents", and the top TEN were
    ten versions of one EDI mapping spec (_v2.0 through _v11.0), 7-9 mentions each.
    So ~21 documents was really ~11 artifacts. Three separate problems in one:

      * counts overstate precedent -- "21 documents" reads as broad prior usage;
      * retrieval is diluted -- ten near-identical candidates compete for top-k, and
        dedup_source does NOT collapse them because each version is a distinct
        `source` name;
      * only the highest version is current. The rest can actively mislead a design,
        and nothing marked them.

    Separately, the regression set's own output shows a document named "... O NO USE
    THIS.xls" ranking FIRST for "cutover plan and go-live checklist". Someone wrote
    "do not use this" into the filename and retrieval had no way to see it.

VERSION vs DUPLICATE -- NOT THE SAME CLAIM
    `_v3.0`, `_R2`, `_Round2` are ORDERED: a higher one supersedes a lower one.
    ` (1)`, ` (2)` are Windows/browser duplicate-download suffixes and carry NO
    ordering -- "report (1).xlsx" is a copy of "report.xlsx", not a newer revision.
    Treating those as versions would declare the copy current and the original
    superseded, which is backwards. They are flagged as duplicates instead, so a
    caller can collapse them without asserting which came first.

CONSERVATIVE BY DESIGN
    A false "superseded" is worse than a missed one: it hides a document that may be
    the only source for something. So a version must be an explicit, recognised token
    -- never inferred from a date in the name, a trailing number, or ordering in the
    filesystem. Same posture as entity_link: missing one is cheaper than inventing one.
"""

import re

# Ordered version tokens, anchored at the END of the stem (before the extension).
# Each must capture the numeric part in group 1.
_VERSION_PATTERNS = (
    r"[_\-\s]v(?:er(?:sion)?)?[_\-\s.]*(\d+(?:\.\d+)*)$",   # _v3.0  _V0.1  _ver2  -v11
    r"[_\-\s]r(?:ev(?:ision)?)?[_\-\s.]*(\d+(?:\.\d+)*)$",  # _R2  -R2  _rev3
    r"[_\-\s]round[_\-\s.]*(\d+(?:\.\d+)*)$",               # _Round2
    r"[_\-\s]draft[_\-\s.]*(\d+(?:\.\d+)*)$",               # _draft2
)
_VERSION_RE = [re.compile(p, re.IGNORECASE) for p in _VERSION_PATTERNS]

# Unordered duplicate-copy suffix: "name (1).xlsx". Bounded to 1-2 digits so a
# genuine parenthetical like "(2024)" is not mistaken for a copy index.
_DUPLICATE_RE = re.compile(r"\s*\((\d{1,2})\)$")

# Two more copy conventions, both UNORDERED like " (1)": the SharePoint/Drive
# "Copy of ..." prefix, and a hand-made "- BACKUP <date>" snapshot. Each is a copy OF
# something, so it loses to a sibling but stays current when it is the only member --
# a backup can be the sole surviving record.
#
# BACKUP is anchored to the END and must follow a separator, so "Backup Strategy.docx"
# and "SAP Backup and Recovery Plan.docx" are untouched; only a trailing snapshot
# marker like "Run Book - BACKUP 1-26-2024" matches.
_COPY_PREFIX_RE   = re.compile(r"^copy\s+of\s+", re.IGNORECASE)
_BACKUP_SUFFIX_RE = re.compile(r"[-_(\s]+backup\b[\s\-_./\d]*\)?$", re.IGNORECASE)

# MEASURED AND DELIBERATELY NOT IMPLEMENTED (2026-09-07, over 2,731 distinct sources).
# 135 names carry a word that LOOKS like a revision marker. Counting them before
# writing a parser is what stopped a bad one shipping:
#
#   final    38  -- overwhelmingly NOT versioning. It is data-migration LOAD
#                  terminology: "Final Load Material Classification", "Customer
#                  Material Info Record - Final load", "Master recipe Final Load
#                  Mock2", "DDA Export - Final Phase 1". Stripping it would delete
#                  business meaning from the family name and merge a final load with
#                  a mock load.
#   dates     7  -- content identity, not revision: "Finance Workshop - Discovery #2
#                  APRIL 24 2023" is when the workshop happened; "CFIN Plants
#                  (11 SEPT 2023)" is when the data was pulled. Neither orders
#                  anything.
#   revised   8  -- the case that prompted the search, and too rare to justify a
#   updated   4     date-parsing rule whose failure mode is merging distinct
#   latest    4     documents. Revisit only if these counts grow.
#   new      14  -- almost always a real word ("New Plants", "new GL").
#
# The lesson generalises: a marker is only usable when it carries ORDERING. "Final"
# and a bare date do not, so no amount of parsing makes them a version.

# Explicit human "this is dead" markers. Deliberately a short, unambiguous list:
# "old" and "draft" alone are NOT here, because plenty of live documents carry them.
# The NO/NOT alternation is not pedantry -- the real corpus contains
# "... O NO USE THIS.xls", where the leading D is missing.
_OBSOLETE_RE = re.compile(
    r"\b(?:do\s+)?no[t]?\s+use\b"
    r"|\bobsolete\b|\bsuperseded\b|\bsupersedes\b|\bdeprecated\b"
    r"|\bdo\s+not\s+refer\b|\bnot\s+in\s+use\b|\bno\s+longer\s+(?:used|valid)\b",
    re.IGNORECASE)

_EXT_RE = re.compile(
    r"\.(?:xlsx?|xlsm|docx?|pptx?|pdf|msg|txt|md|csv|zip|vsdx?)$", re.IGNORECASE)


def _strip_ext(name):
    return _EXT_RE.sub("", name or "")


def parse(source):
    """Decompose a document name into {family, version, version_key, duplicate, obsolete}.

    `version_key` is a TUPLE of ints for correct ordering -- v10.0 must outrank v9.0,
    which string comparison gets backwards. None when no version token is present.
    `family` is the name with the version/duplicate token removed, lowercased, so all
    revisions of one artifact group together.
    """
    raw = (source or "").strip()
    stem = _strip_ext(raw)
    obsolete = bool(_OBSOLETE_RE.search(raw))

    duplicate = None
    # Copy conventions first, so a name carrying both a copy marker and a version
    # ("Foo_v2.0 - BACKUP 2024") still yields version 2.0 from the remaining stem.
    if _COPY_PREFIX_RE.search(stem):
        duplicate = 1
        stem = _COPY_PREFIX_RE.sub("", stem)
    m = _BACKUP_SUFFIX_RE.search(stem)
    if m:
        duplicate = duplicate or 1
        stem = stem[:m.start()]
    m = _DUPLICATE_RE.search(stem)
    if m:
        duplicate = int(m.group(1))
        stem = stem[:m.start()]

    version, version_key = None, None
    for rx in _VERSION_RE:
        m = rx.search(stem)
        if m:
            version = m.group(1)
            version_key = tuple(int(p) for p in version.split("."))
            stem = stem[:m.start()]
            break

    family = re.sub(r"[\s_\-]+", " ", stem).strip().lower()
    return {"family": family or raw.lower(), "version": version,
            "version_key": version_key, "duplicate": duplicate,
            "obsolete_marker": obsolete}


def _rank(parsed):
    """Sort key for 'which of these is current'. Higher is more current.

    An obsolescence marker loses to everything: a human wrote "do not use" on it, and
    that outranks any version number it also carries. Among the rest, a real version
    beats no version, and an unversioned original beats its own duplicate copies --
    the copy is not newer, just a second download.
    """
    return (0 if parsed["obsolete_marker"] else 1,
            parsed["version_key"] or (),
            0 if parsed["duplicate"] else 1)


def pick_current(sources):
    """The most-current name among `sources`, or None.

    Shared by resolve_families and collapse so the two cannot disagree about which
    version wins -- they were computing it independently, which is exactly how a
    "current" flag and a collapsed list drift apart. The name is a deterministic
    tie-break, so a rebuild cannot silently pick differently between equal candidates.
    """
    cands = [s for s in (sources or []) if s]
    if not cands:
        return None
    return max(cands, key=lambda s: (_rank(parse(s)), s))


def resolve_families(sources):
    """Given all distinct document names in the corpus, decide the current one per family.

    Corpus-GLOBAL on purpose: "is this the newest version" cannot be answered from a
    page of search results, because the newest version may not be in them. Which is
    why this runs at index time rather than at query time.

    Returns {source: {family, version, is_current, superseded_by, duplicate,
                      obsolete_marker, family_size}}.
    """
    parsed = {s: parse(s) for s in set(sources or []) if s}
    families = {}
    for src, p in parsed.items():
        families.setdefault(p["family"], []).append(src)

    out = {}
    for family, members in families.items():
        current = pick_current(members)
        win_rank = _rank(parsed[current])
        for src in members:
            p = parsed[src]
            # A SUPERSESSION CLAIM NEEDS EVIDENCE -- a version token, a copy marker or
            # an obsolescence marker. When two names tie on all three, pick_current
            # falls back to the filename for determinism, and that tie-break is a coin
            # flip: measured on the real corpus it declared "Treasury - Process
            # Review.pdf" superseded by the identically-named .pptx (one artifact in
            # two formats, "pptx" > "pdf"), "SD - Sales order (only open SO).xlsx"
            # superseded by "... (only open SO)_.xlsx" (a stray underscore, "_" > "."),
            # and "OTC- Condition Record for Pricing.xlsx" superseded by
            # "OTC-condition record for pricing.xlsx" (case and a space).
            #
            # Deciding which document a reader should ignore on the strength of ASCII
            # ordering is precisely what this module's header forbids. A tie therefore
            # leaves BOTH members current; collapse() still picks one representative,
            # because choosing a representative and asserting death are different
            # claims and only the second needs proof.
            outranked = _rank(p) < win_rank
            # An explicit "do not use" marker means NOT current, whatever else is true.
            # Without this an obsolete document that happens to be the only member of
            # its family came back is_current=True AND obsolete_marker=True -- a
            # contradiction, and the dangerous way round: a caller checking is_current
            # would treat a file someone labelled "DO NOT USE" as authoritative.
            # An all-obsolete family therefore has no current member, which is correct.
            is_current = not outranked and not p["obsolete_marker"]
            out[src] = {
                "family": family,
                "version": p["version"],
                "is_current": is_current,
                # Only set when a member was genuinely OUTRANKED. An obsolete
                # single-member family is not superseded by anything -- it is just
                # dead, and saying "superseded by itself" would be nonsense.
                "superseded_by": current if outranked else None,
                "duplicate": p["duplicate"],
                "obsolete_marker": p["obsolete_marker"],
                "family_size": len(members),
            }
    return out


def collapse(documents, key="source"):
    """Collapse a list of document dicts to one entry per family, keeping the current.

    For the reverse edge, where "21 documents mention VBAK" should read as the number
    of distinct ARTIFACTS. Sums the per-version mention counts onto the surviving
    entry and records what was folded in, so the detail is reported rather than lost.
    """
    docs = [d for d in (documents or []) if isinstance(d, dict) and d.get(key)]
    if not docs:
        return []
    info = resolve_families([d[key] for d in docs])
    by_family = {}
    for d in docs:
        fam = info.get(d[key], {}).get("family", d[key])
        by_family.setdefault(fam, []).append(d)

    out = []
    for fam, members in by_family.items():
        # Same chooser as resolve_families, over only the names actually present here.
        winner_name = pick_current([d[key] for d in members])
        winner = next(d for d in members if d[key] == winner_name)
        meta = info.get(winner_name, {})
        entry = dict(winner)
        entry["version"] = meta.get("version")
        entry["obsolete_marker"] = meta.get("obsolete_marker", False)
        if len(members) > 1:
            # Mentions are summed across the family: the question "how much prior
            # usage is there" is about the artifact, not about one revision of it.
            total = sum(int(d.get("mentions") or 0) for d in members)
            if total:
                entry["mentions"] = total
            entry["collapsed_versions"] = len(members)
            others = sorted(d[key] for d in members if d[key] != winner_name)
            entry["also_in"] = others[:8]
            # Say so when the list is cut. Otherwise collapsed_versions=10 arrives
            # beside an also_in of 8 and the reader is left to wonder which two
            # revisions were dropped -- a silent truncation reads as a miscount.
            if len(others) > 8:
                entry["also_in_truncated"] = len(others) - 8
        out.append(entry)
    out.sort(key=lambda d: (-(d.get("mentions") or 0), d.get(key) or ""))
    return out
