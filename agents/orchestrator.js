// agents/orchestrator.js — KDD pipeline coordinator
// Routes each scope item through 5 agents in sequence.
// Emits SSE progress events so the browser renders live updates.
//
// Pipeline per item:
//   1. Cache Agent      → instant hit from kdd-cache.json?
//   2. Library Agent    → load past decisions (enriches generation prompt)
//   3. Context Agent    → parse SAP catalog description into sections
//   4. Generation Agent → Claude spawn: 15 KDDs + RICEF classification
//   5. Validation Agent → Cloud PE compliance check + auto-fix
//      └─ if validation fails badly → retry Generation once
//
// On any agent error → cache fallback → error event (never silently drops an item)
"use strict";

const cacheAgent      = require("./cache-agent");
const libraryAgent    = require("./library-agent");
const contextAgent    = require("./context-agent");
const generationAgent = require("./generation-agent");
const validationAgent = require("./validation-agent");

/**
 * Run the KDD pipeline for a list of scope items.
 *
 * @param {Object}   options
 * @param {Object[]} options.items        - catalog items { id, name, lob, description }
 * @param {Object}   options.cacheData    - parsed kdd-cache.json (may be null)
 * @param {Function} options.onEvent      - SSE callback: fn(eventObject) => void
 * @param {number}   [options.concurrency=3] - parallel items
 * @param {string}   [options.mode="ai"]  - "fast" = cache-only, "ai" = full pipeline
 * @returns {Promise<{ rows: Object[], overviews: Object[], srcCounts: Object }>}
 */
async function run({ items, cacheData, onEvent, concurrency = 3, mode = "ai" }) {
  const allRows   = [];
  const overviews = [];
  const srcCounts = { claude: 0, cache: 0, error: 0 };

  // ── per-item pipeline ───────────────────────────────────────────────────────

  const processItem = async (item) => {
    const itemStart = Date.now();
    const id        = item.id;

    // Initial pipeline state — passed through each agent
    // onEvent threaded through so generation-agent can emit sub-progress (4a/4b/4c)
    let state = {
      item,
      cacheData,
      onEvent,
      cacheHit:            false,
      libraryHit:          false,
      context:             null,
      pastDecisions:       [],
      rows:                [],
      source:              null,
      validationPassed:    true,
      validationErrors:    [],
      validationAutoFixed: false,
      forbiddenFound:      0,
      inputTokens:         0,
      outputTokens:        0,
      retryCount:          0,
      generationMs:        0
    };

    try {

      // ── Stage 1: Cache Agent ───────────────────────────────────────────────
      // fast mode: cache hit → done, cache miss → error (no Claude)
      // ai mode:   cache hit → skip ahead to done only in fast; in ai, cache is fallback only
      onEvent({ type: "progress", item: id, name: item.name,
                message: `[1/5] Cache check…`, stage: 1 });
      state = await cacheAgent.run(state);

      if (mode === "fast") {
        if (state.cacheHit) {
          // Stage 2: Library Agent — runs even in fast mode (zero tokens, just reads decisions.json)
          // Overlays past approved decisions onto cached rows so the Excel is pre-populated
          onEvent({ type: "progress", item: id, name: item.name,
                    message: `[2/5] Checking decision library…`, stage: 2 });
          state = await libraryAgent.run(state);

          // Overlay past decisions onto matching cached rows
          if (state.pastDecisions.length > 0) {
            state.rows = overlayDecisions(state.rows, state.pastDecisions);
          }

          allRows.push(...state.rows);
          if (state.overview) overviews.push(state.overview);
          srcCounts.cache++;
          onEvent({
            type: "item_done", item: id, name: item.name,
            count: state.rows.length, source: "cache",
            durationMs: Date.now() - itemStart,
            inputTokens: 0, outputTokens: 0,
            pastDecisionsUsed: state.pastDecisions.length,
            pipeline: [
              "cache✓",
              `library${state.pastDecisions.length > 0 ? `✓(${state.pastDecisions.length})` : "—"}`,
              "context—", "generation—", "validation—"
            ]
          });
        } else {
          srcCounts.error++;
          onEvent({ type: "error", item: id,
                    message: `${id} not in cache — switch to AI Generate mode` });
        }
        return;  // fast mode always exits after stage 1+2
      }

      // AI mode — cache result saved for fallback only; pipeline continues to Claude
      // state.cacheHit remains set so catch block can use it as fallback

      // ── Stage 2: Library Agent ─────────────────────────────────────────────
      onEvent({ type: "progress", item: id, name: item.name,
                message: `[2/5] Checking decision library…`, stage: 2 });
      state = await libraryAgent.run(state);

      // ── Stage 3: Context Agent ─────────────────────────────────────────────
      onEvent({ type: "progress", item: id, name: item.name,
                message: `[3/5] Parsing SAP process context…`, stage: 3 });
      state = await contextAgent.run(state);

      // ── Stage 4: Generation Agent ──────────────────────────────────────────
      // Agent emits its own sub-progress: [4a] questions → [4b] enrichment → [4c] RICEF
      // so the user sees activity every ~30s instead of one long wait
      state = await generationAgent.run(state);

      // ── Stage 5: Validation Agent ──────────────────────────────────────────
      onEvent({ type: "progress", item: id, name: item.name,
                message: `[5/5] Validating Cloud PE compliance…`, stage: 5 });
      state = await validationAgent.run(state);

      // If validation found critical errors (row count wrong etc.) → retry generation once
      if (!state.validationPassed && state.retryCount === 0 && state.rows.length === 0) {
        console.warn(`[orchestrator] ${id} — validation failed, retrying generation`);
        state = { ...state, retryCount: 1 };
        onEvent({ type: "progress", item: id, name: item.name,
                  message: `[4/5] Retrying generation (first attempt invalid)…`, stage: 4 });
        state = await generationAgent.run(state);
        state = await validationAgent.run(state);
      }

      // ── Final: collect results ─────────────────────────────────────────────
      if (!state.rows.length) {
        throw new Error("Generation produced no rows after retry");
      }

      const ricefCount = state.rows.filter(r => r.ricefType && r.ricefType !== "None").length;
      const durationMs = Date.now() - itemStart;

      // Build overview for Excel sheet 3
      overviews.push(buildOverview(item, cacheData ? cacheData.version : ""));
      allRows.push(...state.rows);
      srcCounts.claude++;

      onEvent({
        type:                "item_done",
        item:                id,
        name:                item.name,
        count:               state.rows.length,
        source:              state.source,
        ricef:               ricefCount,
        durationMs,
        inputTokens:         state.inputTokens,
        outputTokens:        state.outputTokens,
        validationPassed:    state.validationPassed,
        validationErrors:    state.validationErrors,
        validationAutoFixed: state.validationAutoFixed,
        forbiddenFound:      state.forbiddenFound,
        pastDecisionsUsed:   state.pastDecisions.length,
        retries:             state.retryCount,
        pipeline: [
          "cache—",
          `library${state.pastDecisions.length > 0 ? "✓" : "—"}`,
          "context✓",
          `generation${state.source === "claude" ? "✓" : "—"}`,
          `validation${state.validationPassed ? "✓" : state.validationAutoFixed ? "~(auto-fixed)" : "✗"}`
        ]
      });

    } catch (err) {
      // Live-only mode — no cache fallback. Show the real error.
      const durationMs = Date.now() - itemStart;
      console.error(`[orchestrator] ${id} pipeline error: ${err.message}`);
      srcCounts.error++;
      onEvent({ type: "error", item: id, message: err.message.slice(0, 200) });
    }
  };

  // ── Run in parallel batches ─────────────────────────────────────────────────
  for (let i = 0; i < items.length; i += concurrency) {
    await Promise.all(items.slice(i, i + concurrency).map(item => processItem(item)));
  }

  return { rows: allRows, overviews, srcCounts };
}

// ── overlayDecisions — pre-fills cached rows with past approved decisions ─────
// Matches by kddId (exact). No Claude calls — just a file read (done by library-agent).
// Populates: decisionMade, status, ragStatus, and appends source project to notes.
// This is the "tribal knowledge" layer: consultants see what was decided last time.

function overlayDecisions(rows, pastDecisions) {
  // Build a lookup keyed by kddId
  const byKddId = {};
  pastDecisions.forEach(d => {
    if (d.kddId) byKddId[d.kddId] = d;
  });

  if (!Object.keys(byKddId).length) return rows;

  return rows.map(row => {
    const past = byKddId[row.kddId];
    if (!past) return row;

    // Format the source attribution for the notes field
    const when    = past.savedAt ? new Date(past.savedAt).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : "";
    const source  = [past.client || past.project, when].filter(Boolean).join(" · ");
    const noteTag = source ? ` [Previously: ${source}]` : "";

    return {
      ...row,
      decisionMade: past.decision  || row.decisionMade || "",
      rationale:    past.rationale || row.rationale    || "",
      // status and ragStatus stay unchanged — consultant must confirm for their project
      notes:        (row.notes || "") + noteTag
    };
  });
}

// ── buildOverview — used for Excel Sheet 3 (Scope Overview) ──────────────────

const LOB_MODULE = {
  "Finance": "FIN", "Sales": "SD", "Procurement": "MM",
  "Manufacturing": "PP", "Human Resources": "HCM"
};
const STOP_SECTIONS = new Set(["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"]);

function buildOverview(item, version) {
  const lines = (item.description || "").split("\n").map(l => l.trim()).filter(Boolean);
  const sec   = { overview: [], flow: [], benefits: [] };
  let cur = null;

  for (const line of lines) {
    if (line === "Overview")          { cur = "overview";  continue; }
    if (line === "Key Process Flow")  { cur = "flow";      continue; }
    if (line === "Business Benefits") { cur = "benefits";  continue; }
    if (STOP_SECTIONS.has(line))      { cur = null;        continue; }
    if (cur && line.length > 3) sec[cur].push(line);
  }

  return {
    id:               item.id,
    name:             item.name,
    module:           LOB_MODULE[item.lob] || "Cross",
    lob:              item.lob,
    version,
    overview:         sec.overview.join(" "),
    keyProcessFlow:   sec.flow.map((s, i) => `${i + 1}. ${s}`).join("\n"),
    businessBenefits: sec.benefits.map((s, i) => `${i + 1}. ${s}`).join("\n"),
    source:           "SAP for Me"
  };
}

module.exports = { run };
