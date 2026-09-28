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

function post(body, timeoutMs) {
  const limit = Number(timeoutMs) > 0 ? Number(timeoutMs) : TIMEOUT_MS;
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
        timeout: limit,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
          ...(MCP_KEY ? { "x-api-key": MCP_KEY } : {}),
        },
      },
      (res) => {
        let out = "";
        res.on("data", (d) => { out += d; });
        // Full body. This used to truncate at 400 chars, which was right when the
        // only caller wanted a status string out of record_experience and wrong the
        // moment a second caller wanted a payload. Truncation belongs at the point
        // an error message is built, not in the transport every caller shares.
        res.on("end", () => resolve({ status: res.statusCode, body: out }));
      }
    );
    req.on("timeout", () => { req.destroy(new Error(`timeout after ${limit}ms`)); });
    req.on("error", reject);
    req.end(payload);
  });
}

/* Call any MCP tool and return its parsed payload. Throws on every failure so
 * the caller decides what a failure means -- recordExperience swallows it,
 * brain-scope surfaces it when the brain is declared required.
 *
 * ONE TRANSPORT. This is the only place that knows the URL, the auth header,
 * the timeout and how an MCP envelope unwraps. A second caller with its own
 * copy of those four things is the copy that drifts, so new brain reads come
 * through here rather than repeating post().
 */
async function callTool(name, args, { timeoutMs } = {}) {
  if (!MCP_URL) throw new Error("S4PC_MCP_URL unset");
  const r = await post({
    jsonrpc: "2.0", id: Date.now(), method: "tools/call",
    params: { name, arguments: args || {} },
  }, timeoutMs);
  if (r.status !== 200) throw new Error(`HTTP ${r.status} ${r.body.slice(0, 400)}`);

  let env;
  try { env = JSON.parse(r.body); }
  catch { throw new Error(`unparseable reply: ${r.body.slice(0, 120)}`); }
  if (env.error) throw new Error(`MCP error: ${JSON.stringify(env.error).slice(0, 200)}`);

  // Tool payloads arrive as a JSON string inside result.content[0].text. A tool
  // that reports its own failure does so in-band, so check for it here rather
  // than letting {"error": ...} reach a caller reading .results.
  const text = env.result && env.result.content && env.result.content[0]
    && env.result.content[0].text;
  if (typeof text !== "string") throw new Error("reply had no content[0].text");
  let payload;
  try { payload = JSON.parse(text); }
  catch { throw new Error(`tool payload not JSON: ${text.slice(0, 120)}`); }
  if (payload && payload.error) throw new Error(`tool error: ${payload.error}`);
  return payload;
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
    await callTool("record_experience", {
      topic: String(topic).slice(0, 160),
      lesson: String(lesson).slice(0, 1200),
      impact: impact ? String(impact).slice(0, 200) : undefined,
      category: category || "general",
      tags: (tags || []).slice(0, 8),
      run_id: runId,
      agent: AGENT_ID,
    });
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

module.exports = { newRunId, recordExperience, callTool, AGENT_ID, slug, MCP_URL };
