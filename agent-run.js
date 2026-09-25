"use strict";
/* Run identity, and the one-way road from a run to the shared experience store.
 *
 * TWO SEPARATE THINGS, BOTH OPTIONAL BY DESIGN
 *
 * 1. newRunId() gives a generation the same kind of name S4PC Catalyst gives a
 *    pipeline run: a slug derived from the inputs, versioned on collision --
 *    SMART-SEARCH-FD, SMART-SEARCH-FD-R2. Here that is
 *    KDD-<CLIENT>-<PROJECT>-<YYYYMMDD>. runs.log already records what happened;
 *    it had no name to record it under, so nothing could refer to a run
 *    afterwards.
 *
 * 2. recordExperience() writes a distilled lesson to the brain's L3 store via
 *    the MCP endpoint, tagged with this agent and this run id.
 *
 * WHY IT GOES THROUGH MCP AND NOT STRAIGHT TO POSTGRES
 *    server.py owns that table -- its schema, its id allocation, its
 *    client-identifier guard, its backfill. A direct INSERT from here would be
 *    a second writer with its own copy of those rules, and the copy is always
 *    what drifts. One writer, called over HTTP.
 *
 * WHY A FAILED WRITE IS NEVER FATAL
 *    Losing a lesson is bad. Losing a workbook because a lesson could not be
 *    stored is worse, and that is the trade record_experience itself already
 *    makes when it refuses to treat provenance as a precondition. Every path
 *    here swallows its own errors and logs one line.
 *
 * WHY IT IS OFF UNLESS CONFIGURED
 *    With S4PC_MCP_URL unset this module writes nothing and the only visible
 *    change anywhere is an extra runId field in runs.log. That is deliberate:
 *    a host that has not been told where the brain lives should behave exactly
 *    as it did before this file existed.
 */

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");

const AGENT_ID = process.env.FULCRUM_AGENT_ID || "kdd-generator";
const MCP_URL = process.env.S4PC_MCP_URL || "";        // e.g. http://127.0.0.1:3002/mcp
const MCP_KEY = process.env.S4PC_MCP_KEY || "";
const TIMEOUT_MS = Number(process.env.S4PC_MCP_TIMEOUT_MS || 5000);

/* Slug rules match S4PC's: upper case, non-alphanumerics to hyphens, collapsed,
   trimmed. Short enough to read in a log line. */
function slug(s, max = 24) {
  return String(s || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, max)
    .replace(/-+$/, "");
}

function today() {
  const d = new Date();
  return `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, "0")}${String(
    d.getUTCDate()).padStart(2, "0")}`;
}

/* Every run id already used, read from runs.log. Cheap: the file is one JSON
   object per line and is only appended to. */
function usedIds(outputDir) {
  const p = path.join(outputDir, "runs.log");
  const seen = new Set();
  try {
    for (const line of fs.readFileSync(p, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const id = JSON.parse(line).runId;
        if (id) seen.add(id);
      } catch { /* a truncated last line is not a reason to fail */ }
    }
  } catch { /* no log yet */ }
  return seen;
}

/* KDD-SMOKE-TEST-20260925, then -R2, -R3 ... on the same day.
   Suffixing rather than appending a timestamp keeps the id readable and makes
   "the second run of this today" obvious, which is what S4PC's -R2 is for. */
function newRunId(kind, client, project, outputDir) {
  const base = [slug(kind, 8), slug(client), slug(project), today()]
    .filter(Boolean).join("-");
  const seen = usedIds(outputDir);
  if (!seen.has(base)) return base;
  for (let n = 2; n < 1000; n++) {
    const candidate = `${base}-R${n}`;
    if (!seen.has(candidate)) return candidate;
  }
  // 1000 runs of the same client/project/day. Return something unique rather
  // than loop forever or collide silently.
  return `${base}-R${Date.now()}`;
}

function post(body) {
  return new Promise((resolve, reject) => {
    let u;
    try { u = new URL(MCP_URL); } catch (e) { return reject(e); }
    const lib = u.protocol === "https:" ? https : http;
    const payload = Buffer.from(JSON.stringify(body), "utf8");
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        method: "POST",
        timeout: TIMEOUT_MS,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
          ...(MCP_KEY ? { "x-api-key": MCP_KEY } : {}),
        },
      },
      (res) => {
        let out = "";
        res.on("data", (d) => { out += d; });
        res.on("end", () => resolve({ status: res.statusCode, body: out.slice(0, 400) }));
      }
    );
    req.on("timeout", () => { req.destroy(new Error(`timeout after ${TIMEOUT_MS}ms`)); });
    req.on("error", reject);
    req.end(payload);
  });
}

/* Write one lesson to L3. Resolves to a short status string; never rejects.
 *
 * NOT CALLED FOR EVERY RUN. record_experience asks for a distilled lesson from
 * a run that taught something non-obvious. A row per generation would turn L3
 * into run telemetry and bury the lessons it exists to hold, so the caller
 * decides what is worth keeping -- see the note at the bottom of this file.
 */
async function recordExperience({ topic, lesson, impact, tags, runId, category }) {
  if (!MCP_URL) return "skipped: S4PC_MCP_URL unset";
  if (!topic || !lesson) return "skipped: needs topic and lesson";
  try {
    const r = await post({
      jsonrpc: "2.0", id: Date.now(), method: "tools/call",
      params: {
        name: "record_experience",
        arguments: {
          topic: String(topic).slice(0, 160),
          lesson: String(lesson).slice(0, 1200),
          impact: impact ? String(impact).slice(0, 200) : undefined,
          category: category || "general",
          tags: (tags || []).slice(0, 8),
          run_id: runId,
          agent: AGENT_ID,
        },
      },
    });
    if (r.status !== 200) return `failed: HTTP ${r.status} ${r.body}`;
    if (/"error"/.test(r.body)) return `failed: ${r.body}`;
    return "recorded";
  } catch (err) {
    // The brain being unreachable must never surface to the caller. It is a
    // lesson we did not keep, not a workbook we did not produce.
    return `failed: ${err.message}`;
  }
}

/* NOTHING IS RECORDED AUTOMATICALLY, and that is the design.
 *
 * The obvious move is to write a lesson at the end of every generation. It was
 * rejected: the only facts available there are counts -- how many items, how
 * many errored -- and "3 of 15 errored" is telemetry, not a lesson. Storing it
 * 679 times would bury the distilled lessons query_experience exists to
 * surface, which is how every log that became a knowledge base by accident
 * stopped being useful.
 *
 * runs.log already holds the telemetry, now with a run id to join on. L3 holds
 * what somebody concluded. Those are different, and the second needs a human
 * or an agent to write the sentence -- hence POST /api/experience rather than
 * an automatic hook.
 */

module.exports = { newRunId, recordExperience, AGENT_ID, slug };
