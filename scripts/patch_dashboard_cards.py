#!/usr/bin/env python3
"""Point the BDCQ and KDD dashboard cards at the Fulcrum proxy.

WHAT IT CHANGES
    Two agent entries in the GrowActivAIte registry, by id:

      solution-bdcq-agent-0    live: false -> true, url -> ":8413/"
      solution-kdd-creation-2  url -> ":8413/"

    Nothing else. Not Fit-to-Standard, not Business Process Design, not the
    45 other agents -- they are matched by id, so a rename upstream makes this
    script report a miss rather than edit the wrong card.

WHY ":8413/" AND NOT THE LOAD BALANCER NAME
    RICEFW Builder already uses ":8411/", a port-only URL the UI resolves
    against whatever host it was served from. Hardcoding
    digitbrain-....elb.amazonaws.com would break the moment anyone opened the
    dashboard through a tunnel, a different listener, or localhost -- and it
    would break silently, as a card that goes nowhere. Following the
    convention that is already there costs nothing and survives all three.

WHY FIT-TO-STANDARD IS LEFT ALONE
    It is served by the Chrome extension's f2s catalogue, which no server
    route reads. Pointing its card at 8413 would give a live-looking link to
    an agent that is not there.

BOTH FILES
    data.json and catalogue.json hold the same 47 agents. Which one the server
    reads was not obvious from the source, so both are updated: leaving one
    behind would mean the change appears to work until something reloads from
    the other.

Usage:
    python3.11 scripts/patch_dashboard_cards.py
    python3.11 scripts/patch_dashboard_cards.py --dry-run
    python3.11 scripts/patch_dashboard_cards.py --url ':8413/' --dir ~/growactivaite-dashboard/server
"""

import io
import os
import sys
import json
import shutil
import argparse
from pathlib import Path

DEFAULT_DIR = Path(os.path.expanduser("~/growactivaite-dashboard/server"))
FILES = ("data.json", "catalogue.json")

# id -> what to set. Keyed by id, not name: the ids are stable and the names
# are what someone edits in the admin panel.
TARGETS = {
    "solution-bdcq-agent-0":   {"live": True, "url": ":8413/"},
    "solution-kdd-creation-2": {"live": True, "url": ":8413/"},
}


def sniff_indent(text):
    """Keep the file's existing formatting.

    json.dump rewrites the whole file, so guessing the indent turns a two-line
    change into a whole-file diff that nobody can review. Read it off the
    second line instead.
    """
    for line in text.split("\n")[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return 2


def agents(doc):
    for ph in doc.get("phases", []):
        for g in ph.get("groups", []):
            for a in g.get("agents", []):
                yield ph.get("id"), g.get("id"), a


def patch_file(path, targets, dry):
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    indent = sniff_indent(text)

    seen, changed = set(), []
    for phase, group, a in agents(doc):
        want = targets.get(a.get("id"))
        if not want:
            continue
        seen.add(a["id"])
        before = {k: a.get(k) for k in want}
        if before == want:
            continue
        a.update(want)
        changed.append((phase, group, a.get("name"), before, want))

    missing = set(targets) - seen
    if not dry and changed:
        shutil.copy2(path, path.with_suffix(".json.bak"))
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=indent, ensure_ascii=False)
            fh.write("\n")
    return changed, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--url", default=":8413/")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    targets = {k: dict(v, url=a.url) for k, v in TARGETS.items()}
    d = Path(os.path.expanduser(a.dir))
    if not d.is_dir():
        return "not a directory: %s  (pass --dir)" % d

    any_missing = False
    for name in FILES:
        p = d / name
        if not p.exists():
            print("%-16s not present, skipped" % name)
            continue
        try:
            changed, missing = patch_file(p, targets, a.dry_run)
        except Exception as exc:                                  # noqa: BLE001
            return "%s: %s" % (name, exc)

        if changed:
            for phase, group, nm, before, after in changed:
                print("%-16s %s/%s  %s" % (name, phase, group, nm))
                print("%-16s   %s  ->  %s" % ("", before, after))
        else:
            print("%-16s already correct, nothing to change" % name)
        if missing:
            any_missing = True
            # Name them: a card id that no longer exists means the registry was
            # edited upstream, and silently doing nothing would look identical
            # to success.
            print("%-16s NOT FOUND: %s" % (name, ", ".join(sorted(missing))))

    if a.dry_run:
        print("\n--dry-run: nothing written")
    elif not any_missing:
        print("\nBacked up alongside as *.json.bak.")
        print("Restart the dashboard so it re-reads the registry:")
        print("  pm2 restart growactivaite --update-env")
    if any_missing:
        return ("one or more cards were not found by id. Check the current ids with:\n"
                "   python3.11 -c \"import json;d=json.load(open('data.json'));"
                "print([a['id'] for p in d['phases'] for g in p.get('groups',[]) "
                "for a in g.get('agents',[]) if 'bdcq' in a['id'] or 'kdd' in a['id']])\"")
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\n%s" % err)
