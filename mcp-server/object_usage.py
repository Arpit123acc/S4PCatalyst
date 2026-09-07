"""Reverse edges — given an SAP object, where has this team actually used it?

WHY THIS EXISTS
    Entity linking already runs FORWARD: a retrieved document hands back the object
    names in its text, each with a current release verdict. The opposite direction was
    missing entirely, and it is the one a delivery team asks out loud:

        "Have we used API_PURCHASEORDER_PROCESS_SRV before, and where?"
        "Does anything in our delivery history still SELECT from EKKO?"

    No amount of query-time extraction can answer that, because it requires having
    looked at every chunk rather than at the handful a query happened to return. So
    keyword_index.py records object mentions at INGEST time, and this module reads
    that index backwards, joining two stores:

        L4  brain/index/keyword.db     which documents mention the object
        L3  catalog/experience_db      which recorded lessons mention it

RELEASE STATE IS A SEPARATE QUESTION -- AND THIS IS THE DANGEROUS PART
    "We used it in 2024" is evidence about US, not about SAP's release contract. An
    object can be widely used across past deliveries and still be unreleased, or
    deprecated since. Every payload here therefore carries `not_a_release_contract`,
    and callers must not let prior usage soften a verdict from
    check_object_release_state. Prior usage is a LEAD -- it tells you where to look
    for context, not whether the object is legal to use today.

"NOT INDEXED" IS NOT "NEVER USED"
    An older keyword.db has no mention table. Reporting that as zero usage would be a
    confident false negative, so `indexed: false` is surfaced explicitly and the
    summary says so in words.
"""

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
_SCRIPTS = os.path.join(REPO_DIR, "scripts")

_NOT_A_CONTRACT = (
    "Prior usage is evidence about this TEAM's history, not about SAP's release "
    "contract. An object can be used across many past deliveries and still be "
    "unreleased or since deprecated. Take the verdict from "
    "check_object_release_state; treat this only as a lead to prior context."
)


def _keyword_search():
    """keyword_search owns keyword.db. Imported lazily: it lives in scripts/ and is
    absent on hosts without the brain, where the L3 half below still works."""
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import keyword_search
    return keyword_search


def lessons_mentioning(object_name, entries, limit=10):
    """L1 -> L3: recorded lessons whose text names this object.

    Word-boundary match on the requested name rather than running the extractor over
    each lesson: the caller has already named the object, and a direct match also
    finds objects whose shape entity_link's patterns would not recognise.
    """
    name = (object_name or "").strip()
    if not name:
        return []
    rx = re.compile(r"\b%s\b" % re.escape(name), re.IGNORECASE)
    out = []
    for e in entries or []:
        hay = " ".join([e.get("topic") or "", e.get("lesson") or "",
                        e.get("impact") or "",
                        " ".join(str(t) for t in (e.get("tags") or []))])
        if rx.search(hay):
            out.append({"id": e.get("id"), "category": e.get("category"),
                        "topic": e.get("topic"), "lesson": e.get("lesson"),
                        "added": e.get("added")})
        if len(out) >= limit:
            break
    return out


def find_usage(object_name, entries=None, limit=10, source_system=None):
    """Combined reverse lookup across the corpus (L4) and recorded lessons (L3)."""
    name = (object_name or "").strip()
    if not name:
        return {"error": "object_name is required"}

    docs = {"indexed": False, "documents": [], "total_mentions": 0,
            "total_documents": 0}
    doc_error = None
    try:
        ks = _keyword_search()
        # Checked separately from has_mentions() because the two failures need
        # different fixes and would otherwise both surface as "rebuild the mention
        # index": no keyword.db at all means this host simply has no brain (normal off
        # the delivery server), whereas a keyword.db WITHOUT the mention table means a
        # rebuild is genuinely due. Reporting the first as the second sends the reader
        # to run an ingest that cannot work here.
        if not ks.available():
            doc_error = "no brain index on this host (expected %s)" % ks.DB_PATH
        else:
            docs = ks.documents_for_object(
                name, limit=limit, source_system=source_system)
    except ImportError as exc:
        doc_error = "keyword_search unavailable (%s)" % exc
    except Exception as exc:
        doc_error = "mention lookup failed: %s" % exc

    lessons = lessons_mentioning(name, entries or [], limit=limit)

    if doc_error:
        corpus = "corpus not searchable here — %s" % doc_error
    elif not docs.get("indexed"):
        corpus = ("corpus NOT INDEXED for object mentions (this is not the same as "
                  "'never used') — rebuild: python3.11 scripts/keyword_index.py")
    elif docs.get("total_documents"):
        raw = docs.get("total_documents", 0)
        arts = docs.get("total_artifacts") or raw
        corpus = "%d mention(s) across %d distinct artifact(s)" % (
            docs.get("total_mentions", 0), arts)
        # Say BOTH numbers when they differ. The raw filename count is what made the
        # first real VBAK lookup read "21 documents" when ten of them were revisions
        # of a single EDI spec -- which overstates precedent to anyone skimming it.
        if raw > arts:
            corpus += (" — %d filenames in total, so %d are superseded revisions or "
                       "duplicate copies of the same documents" % (raw, raw - arts))
    else:
        corpus = "no mentions found in the indexed corpus"

    return {
        "object_name": name.upper(),
        "delivery_documents": docs.get("documents", []),
        "corpus_mentions": {"indexed": docs.get("indexed", False),
                            "total_mentions": docs.get("total_mentions", 0),
                            "total_documents": docs.get("total_documents", 0),
                            "total_artifacts": docs.get("total_artifacts",
                                                        docs.get("total_documents", 0)),
                            "collapsed_by_version": docs.get("collapsed", False),
                            "error": doc_error},
        "lessons": lessons,
        "summary": "%s; %d recorded lesson(s) name it" % (corpus, len(lessons)),
        "not_a_release_contract": _NOT_A_CONTRACT,
    }


def usage_brief(object_name, entries=None):
    """A compact form for embedding in another tool's payload.

    Kept small on purpose: check_object_release_state and get_object_graph attach this
    for context, and a full document list there would bury the verdict that is the
    actual answer.
    """
    full = find_usage(object_name, entries=entries, limit=3)
    if "error" in full:
        return None
    cm = full["corpus_mentions"]
    if not cm["indexed"] and not full["lessons"]:
        return {"indexed": False,
                "note": "object mentions are not indexed on this host — this is NOT "
                        "evidence that the object was never used"}
    return {
        "indexed": cm["indexed"],
        # Distinct artifacts, not filenames -- a caller reading this as "how much
        # precedent exists" must not have ten revisions counted as ten documents.
        "documents": cm.get("total_artifacts", cm["total_documents"]),
        "filenames": cm["total_documents"],
        "mentions": cm["total_mentions"],
        "lesson_ids": [l["id"] for l in full["lessons"]],
        "top_documents": [d["source"] for d in full["delivery_documents"][:3]],
        "not_a_release_contract": _NOT_A_CONTRACT,
    }
