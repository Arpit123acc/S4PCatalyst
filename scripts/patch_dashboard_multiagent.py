#!/usr/bin/env python3
"""Let the GrowActivAIte dashboard proxy MORE THAN ONE agent.

THE PROBLEM
    server/start.js calls startAgentProxy() exactly once, inside the listen
    callback, with a single AGENT_TARGET_PORT. One dashboard process therefore
    fronts one agent. Adding a second agent meant running a second copy of the
    whole dashboard purely to get a second proxy -- which is how a redundant
    dashboard appeared on 8412 while nobody wanted one.

    agent-proxy.js was never the limitation. startAgentProxy already takes its
    target as an argument and returns its own server, so it was always
    instantiable per agent. Only this one call site was singular.

WHAT THIS CHANGES
    One file, one call site. agent-proxy.js is untouched.

      8410  dashboard        (one, as now)
      8411  -> 8321  S4PC Catalyst
      8413  -> 8330  Fulcrum BDCQ + KDD

    Backward compatible: AGENT_PROXY_PORT / AGENT_TARGET_PORT / AGENT_LABEL
    still define the first agent, so an existing deployment with AGENTS unset
    behaves exactly as before. Extra agents come from an AGENTS JSON array.

WHY THE VALIDATION IS NOISY
    A misconfigured agent that is silently not proxied looks, from the
    dashboard, precisely like an agent that is down -- and nothing
    distinguishes the two. So bad JSON, a missing port and a duplicate port
    each print a specific reason rather than being skipped.

This is a PATCH SCRIPT for a repo that lives outside S4PC, so it is
idempotent and refuses to run twice: the assertion fails if the original block
is gone.

Usage:
    python3.11 scripts/patch_dashboard_multiagent.py
    python3.11 scripts/patch_dashboard_multiagent.py --dry-run
    python3.11 scripts/patch_dashboard_multiagent.py --start-js /path/to/start.js
"""

import os
import sys
import shutil
import argparse
import subprocess
from pathlib import Path

DEFAULT = Path(os.path.expanduser("~/growactivaite-dashboard/server/start.js"))

OLD = '''  if (process.env.AGENT_PROXY_PORT) {
    startAgentProxy({
      port: Number(process.env.AGENT_PROXY_PORT),
      host: HOST,
      targetHost: process.env.AGENT_TARGET_HOST || "127.0.0.1",
      targetPort: Number(process.env.AGENT_TARGET_PORT || 8321),
      dashboardUrl: process.env.DASHBOARD_URL || `http://${HOST}:${port}`,
      label: process.env.AGENT_LABEL || "S4PC Catalyst",
    });
  }'''

NEW = '''  for (const a of agentList()) {
    startAgentProxy({
      ...a,
      host: HOST,
      dashboardUrl: process.env.DASHBOARD_URL || `http://${HOST}:${port}`,
    });
  }'''

ANCHOR = "app.listen(port, HOST, () => {"

HELPER = '''/* ONE DASHBOARD, N AGENTS.
   startAgentProxy was always written to be instantiated per agent -- it takes
   its target in the argument and returns its own server. Only this file
   limited it to a single call, so a second agent meant a second copy of the
   whole dashboard just to get a second proxy.

   Two sources, both honoured:
     AGENT_PROXY_PORT / AGENT_TARGET_PORT / AGENT_LABEL   the original single
       agent, unchanged, so an existing deployment keeps working untouched.
     AGENTS   a JSON array for the rest:
       AGENTS='[{"port":8413,"targetPort":8330,"label":"Fulcrum - BDCQ + KDD"}]'

   Malformed input is reported, never skipped quietly: an agent that fails to
   be proxied looks exactly like an agent that is down, and the dashboard
   gives no clue which. */
function agentList() {
  const out = [];
  const seen = new Set();

  const add = (a, where) => {
    const port = Number(a.port);
    const targetPort = Number(a.targetPort);
    if (!port || !targetPort) {
      console.error(`  SKIPPED (${where}): need numeric port and targetPort, got`,
                    JSON.stringify(a));
      return;
    }
    if (seen.has(port)) {
      // Two agents on one port is EADDRINUSE at best and a silent shadow at
      // worst; say so rather than letting the second die in the log.
      console.error(`  SKIPPED (${where}): port ${port} already taken by another agent`);
      return;
    }
    seen.add(port);
    out.push({
      port,
      targetPort,
      targetHost: a.targetHost || "127.0.0.1",
      label: a.label || `agent on ${targetPort}`,
    });
  };

  if (process.env.AGENT_PROXY_PORT) {
    add({
      port: process.env.AGENT_PROXY_PORT,
      targetPort: process.env.AGENT_TARGET_PORT || 8321,
      targetHost: process.env.AGENT_TARGET_HOST,
      label: process.env.AGENT_LABEL || "S4PC Catalyst",
    }, "AGENT_PROXY_PORT");
  }

  if (process.env.AGENTS) {
    let parsed;
    try {
      parsed = JSON.parse(process.env.AGENTS);
    } catch (err) {
      console.error("  AGENTS is not valid JSON, no extra agents proxied:", err.message);
      return out;
    }
    if (!Array.isArray(parsed)) {
      console.error("  AGENTS must be a JSON array, got", typeof parsed);
      return out;
    }
    parsed.forEach((a, i) => add(a, `AGENTS[${i}]`));
  }

  if (!out.length) console.log("  no agents proxied (set AGENT_PROXY_PORT or AGENTS)");
  return out;
}

'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-js", default=str(DEFAULT))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    p = Path(a.start_js)
    if not p.exists():
        return "not found: %s\n   pass --start-js if the dashboard lives elsewhere." % p

    s = p.read_text(encoding="utf-8")
    if "agentList" in s:
        print("already patched: agentList() is present in %s" % p)
        print("nothing to do.")
        return None
    if s.count(OLD) != 1:
        return ("the single-agent block is not in the expected form, so this cannot\n"
                "   patch safely. Send me `sed -n '45,70p' %s` and I will adjust." % p)
    if s.count(ANCHOR) != 1:
        return "expected exactly one `%s` in %s" % (ANCHOR, p)

    out = s.replace(OLD, NEW, 1).replace(ANCHOR, HELPER + ANCHOR, 1)

    if a.dry_run:
        print("--dry-run: would rewrite %s (%d -> %d bytes)" % (p, len(s), len(out)))
        return None

    backup = p.with_suffix(".js.bak")
    shutil.copy2(p, backup)
    p.write_text(out, encoding="utf-8")
    print("patched  %s" % p)
    print("backup   %s" % backup)

    # Syntax-check before anyone restarts a live dashboard on it.
    try:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        if r.returncode == 0:
            print("node --check: OK")
        else:
            print("node --check FAILED:\n%s" % (r.stderr or r.stdout)[:400])
            print("\nrestore with:  cp %s %s" % (backup, p))
            return "syntax error after patch — restored copy is at %s" % backup
    except FileNotFoundError:
        print("node not on PATH; skipped the syntax check")

    print("""
next:
  AGENTS='[{"port":8413,"targetPort":8330,"label":"Fulcrum - BDCQ + KDD"}]' \\
    pm2 restart growactivaite --update-env
  pm2 save
  pm2 logs growactivaite --out --lines 12 --nostream | grep -i proxied

Expect TWO `proxied on` lines. One means the restart did not pick up AGENTS.""")
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\n%s" % err)
