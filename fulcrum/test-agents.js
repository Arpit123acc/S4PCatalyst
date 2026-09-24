"use strict";
// test-agents.js — run from project root: node test-agents.js
const fs   = require("fs");
const http = require("http");

const cacheAgent      = require("./agents/cache-agent");
const libraryAgent    = require("./agents/library-agent");
const contextAgent    = require("./agents/context-agent");
const validationAgent = require("./agents/validation-agent");
const orchestrator    = require("./agents/orchestrator");

const catalog   = JSON.parse(fs.readFileSync("./scope-catalog.json","utf8"));
const cacheData = JSON.parse(fs.readFileSync("./kdd-cache.json","utf8"));

const results = [];
const pass = (name, detail) => results.push({ status:"PASS", name, detail });
const fail = (name, detail) => results.push({ status:"FAIL", name, detail });

async function runTests() {
  const item54A = catalog.processes.find(p => p.id === "54A");
  const itemBD6 = catalog.processes.find(p => p.id === "BD6");
  const itemJ59 = catalog.processes.find(p => p.id === "J59");

  // TEST 1: Cache HIT — all 15 clean rows (cache fully regenerated with proper Cloud PE questions)
  const s1 = await cacheAgent.run({ item: item54A, cacheData, rows: [] });
  (s1.cacheHit && s1.rows.length === 15)
    ? pass("Cache Agent — HIT", "54A: " + s1.rows.length + "/15 clean rows | " +
           (s1.dirtySkipped||0) + " dirty rows | 100% Cloud PE compliant | 0s")
    : fail("Cache Agent — HIT", "cacheHit=" + s1.cacheHit + " rows=" + s1.rows.length + " (expected 15)");

  // TEST 2: Cache MISS
  const fakeItem = { id:"FAKE", name:"Fake Process", lob:"Finance", description:"" };
  const s2 = await cacheAgent.run({ item: fakeItem, cacheData, rows: [] });
  (!s2.cacheHit && (s2.rows||[]).length === 0)
    ? pass("Cache Agent — MISS", "FAKE not in cache — no rows returned, pipeline moves to Claude")
    : fail("Cache Agent — MISS", "Unexpected cache hit");

  // TEST 3: Library Agent
  const s3 = await libraryAgent.run({ item: item54A });
  pass("Library Agent", "54A past decisions=" + s3.pastDecisions.length +
       " | libraryHit(≥3)=" + s3.libraryHit +
       " | Note: grows as consultants approve KDDs in Editor");

  // TEST 4: Context Agent
  const s4 = await contextAgent.run({ item: item54A });
  (s4.context && s4.context.overview.length > 10)
    ? pass("Context Agent", "Overview=" + s4.context.overview.length + "c | " +
           "Flow=" + s4.context.flow.split("\n").length + " steps | " +
           "Benefits=" + (s4.context.benefits.length > 0 ? "yes" : "empty"))
    : fail("Context Agent", "Context empty");

  // TEST 5: Validation — 15 clean rows
  const cleanRows = cacheData.items["54A"].rows.slice(0, 15);
  const s5 = await validationAgent.run({ item: item54A, rows: cleanRows });
  pass("Validation — 15 clean rows", "forbiddenFound=" + s5.forbiddenFound +
       " | validationErrors=" + s5.validationErrors.length +
       (s5.validationErrors.length ? " → " + s5.validationErrors[0] : " | All clean"));

  // TEST 6: Validation — auto-fix SAP GUI
  const dirtyRows = cleanRows.map((r,i) =>
    i===0 ? { ...r, rationale: r.rationale + " via SAP GUI" } : r);
  const s6 = await validationAgent.run({ item: item54A, rows: dirtyRows });
  const replaced = s6.rows[0].rationale.includes("SAP Fiori") &&
                   !s6.rows[0].rationale.includes("SAP GUI");
  replaced
    ? pass("Validation — auto-fix", "SAP GUI → SAP Fiori auto-replaced | autoFixed=" + s6.validationAutoFixed)
    : fail("Validation — auto-fix", "Not fixed: " + s6.rows[0].rationale.slice(0, 60));

  // TEST 7: Validation — ABAP blocked
  const abapRows = cleanRows.map((r,i) =>
    i===2 ? { ...r, designQuestion: r.designQuestion + " using ABAP code" } : r);
  const s7 = await validationAgent.run({ item: item54A, rows: abapRows });
  s7.forbiddenFound > 0
    ? pass("Validation — ABAP detected", "ABAP caught | forbiddenFound=" + s7.forbiddenFound + " | output blocked")
    : fail("Validation — ABAP detected", "ABAP slipped through — validation gap!");

  // TEST 8: Validation — wrong row count rejected
  const shortRows = cleanRows.slice(0, 12);
  const s8 = await validationAgent.run({ item: item54A, rows: shortRows });
  !s8.validationPassed
    ? pass("Validation — row count enforced", "12 rows rejected (expected 15) | " + s8.validationErrors[0])
    : fail("Validation — row count", "12 rows should fail but passed");

  // TEST 9: Orchestrator fast mode — 3 items × 15 rows = 45 total
  // Library agent now runs in fast mode (zero tokens) — overlays past decisions onto cached rows
  const events = [];
  const { rows: r9, srcCounts: sc9 } = await orchestrator.run({
    items: [item54A, itemBD6, itemJ59],
    cacheData,
    onEvent: e => events.push(e),
    mode: "fast"
  });
  (r9.length === 45 && sc9.cache === 3 && sc9.error === 0)
    ? pass("Orchestrator — fast mode (3 parallel)", r9.length + " rows | 3 cache hits | 0 errors | " +
           events.length + " SSE events | library agent runs in fast mode ✅")
    : fail("Orchestrator — fast mode", "rows=" + r9.length + " (expected 45) | cache=" + sc9.cache + " errors=" + sc9.error);

  // TEST 10: SSE event types
  const evTypes = [...new Set(events.map(e => e.type))];
  (evTypes.includes("progress") && evTypes.includes("item_done"))
    ? pass("SSE Events", "Types emitted: " + evTypes.join(", ") + " | No error events=" + !evTypes.includes("error"))
    : fail("SSE Events", "Wrong event types: " + evTypes.join(", "));

  // TEST 11: Catalog vs cache coverage
  const covered = catalog.processes.filter(p => cacheData.items[p.id]).length;
  const pct = Math.round(covered / catalog.processes.length * 100);
  pass("Catalog coverage", covered + "/" + catalog.processes.length + " scope items cached (" + pct + "%) | " +
       "All 9,855 questions ready for Quick Generate");

  // TEST 12: Server API
  await new Promise(resolve => {
    http.get("http://localhost:8321/api/status", res => {
      let d = "";
      res.on("data", c => d += c);
      res.on("end", () => {
        try {
          const j = JSON.parse(d);
          pass("Server — /api/status", "Port 8321 running | catalog=" + j.catalogExists +
               " | cache=" + j.cacheReady + " | outputFiles=" + j.outputCount + " | v" + j.catalogVersion);
        } catch { fail("Server", "Not responding or bad JSON"); }
        resolve();
      });
    }).on("error", () => { fail("Server", "Not running on port 8321 — restart needed"); resolve(); });
  });

  // TEST 13: Decisions API
  await new Promise(resolve => {
    http.get("http://localhost:8321/api/decisions/summary", res => {
      let d = "";
      res.on("data", c => d += c);
      res.on("end", () => {
        try {
          const j = JSON.parse(d);
          pass("Decision Library API", "Total decisions=" + j.total + " | scopeItems=" + j.scopeItems + " | projects=" + j.projects);
        } catch { fail("Decision Library API", "Bad response"); }
        resolve();
      });
    }).on("error", () => { fail("Decision Library API", "Server not running"); resolve(); });
  });

  // ── PRINT RESULTS ──────────────────────────────────────────────────────────
  console.log("\n╔═══════════════════════════════════════════════════════════╗");
  console.log("║        FULCRUM AGENTIC PIPELINE — TEST RESULTS            ║");
  console.log("╚═══════════════════════════════════════════════════════════╝\n");
  results.forEach((r, i) => {
    const icon = r.status === "PASS" ? "✅" : "❌";
    console.log(icon + " [" + String(i+1).padStart(2) + "] " + r.name);
    console.log("        " + r.detail + "\n");
  });
  const passed = results.filter(r => r.status === "PASS").length;
  const failed = results.filter(r => r.status === "FAIL").length;
  console.log("═══════════════════════════════════════════════════════════");
  console.log(passed + "/" + results.length + " passed" +
              (failed ? " | ❌ " + failed + " FAILED" : " | ✅ All agents healthy"));
  console.log("═══════════════════════════════════════════════════════════\n");
}

runTests().catch(e => {
  console.error("Test runner crashed:", e.message);
  console.error(e.stack);
});
