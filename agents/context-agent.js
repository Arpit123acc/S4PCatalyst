// agents/context-agent.js — Stage 3 of the KDD pipeline
// Parses the SAP for Me catalog description into structured sections.
// Keeps each section within a token budget so the Generation Agent prompt stays lean.
//
// Output: state.context = { overview, flow, benefits }
"use strict";

const STOP_SECTIONS = new Set([
  "Solution Capabilities", "Industry Relevance", "Diagrams", "Accelerators"
]);

/**
 * @param {Object} state
 * @param {Object} state.item  - catalog item with .description string
 * @returns {Object} updated state with context object
 */
async function run(state) {
  const { item } = state;

  const lines = (item.description || "")
    .split("\n")
    .map(l => l.trim())
    .filter(Boolean);

  const sec = { overview: [], flow: [], benefits: [] };
  let cur = null;

  for (const line of lines) {
    if (line === "Overview")          { cur = "overview";  continue; }
    if (line === "Key Process Flow")  { cur = "flow";      continue; }
    if (line === "Business Benefits") { cur = "benefits";  continue; }
    if (STOP_SECTIONS.has(line))      { cur = null;        continue; }
    if (cur) sec[cur].push(line);
  }

  const context = {
    // Token budgets: overview 300 chars, flow 10 steps, benefits 5 items
    overview:  sec.overview.join(" ").slice(0, 300),
    flow:      sec.flow.slice(0, 10).map((s, i) => `${i + 1}. ${s}`).join("\n"),
    benefits:  sec.benefits.slice(0, 5).join("; ")
  };

  console.log(`[context-agent] ${item.id} — overview ${context.overview.length}c, ${sec.flow.length} flow steps`);

  return { ...state, context };
}

module.exports = { run };
