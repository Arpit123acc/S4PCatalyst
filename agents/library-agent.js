// agents/library-agent.js — Stage 2 of the KDD pipeline
// Loads past approved decisions for this scope item from decisions.json.
// Always runs (even on cache hit is skipped by orchestrator — but runs in AI mode).
//
// Sets state.pastDecisions — injected into the Generation Agent's Claude prompt
// so decisions are grounded in real outcomes from previous projects.
"use strict";

const fs   = require("fs");
const path = require("path");
const { ROOT } = require("./shared");

const DECISIONS_PATH = path.join(ROOT, "decisions.json");

/**
 * @param {Object} state
 * @param {Object} state.item  - catalog item { id }
 * @returns {Object} updated state with pastDecisions array
 */
async function run(state) {
  const { item } = state;

  let all = [];
  try {
    if (fs.existsSync(DECISIONS_PATH)) {
      all = JSON.parse(fs.readFileSync(DECISIONS_PATH, "utf8"));
    }
  } catch (e) {
    console.warn(`[library-agent] Could not read decisions.json: ${e.message}`);
  }

  const past = all
    .filter(d => d.scopeItemId === item.id)
    .slice(0, 5);   // max 5 to keep prompt size bounded

  console.log(`[library-agent] ${item.id} — ${past.length} past decision(s) found`);

  return {
    ...state,
    pastDecisions: past,
    libraryHit:    past.length >= 3   // signals "enough history" to orchestrator
  };
}

module.exports = { run };
