// agents/cache-agent.js — Stage 1 of the KDD pipeline
// Checks kdd-cache.json for pre-built KDD rows for this scope item.
//
// If HIT:  state.rows populated, state.cacheHit = true, state.source = "cache"
//          Pipeline skips Stages 3-5 (context, generation, validation)
// If MISS: state unchanged — pipeline continues to generation
"use strict";

/**
 * @param {Object} state
 * @param {Object} state.item       - catalog item { id, name, lob, description }
 * @param {Object} state.cacheData  - parsed kdd-cache.json (may be null)
 * @returns {Object} updated state
 */
async function run(state) {
  const { item, cacheData } = state;
  const id = item.id;

  if (cacheData && cacheData.items && cacheData.items[id]) {
    const cached = cacheData.items[id];

    // Filter out rows flagged as needing Claude regeneration.
    // These were ECC-era questions (SM30, LSMW) that had text substitution applied —
    // the substitution was directionally correct but the question framing is still wrong.
    // Claude must regenerate them from scratch using the SAP process context.
    const cleanRows  = (cached.rows || []).filter(r => !r.needsRegeneration);
    const dirtyCount = (cached.rows || []).length - cleanRows.length;

    if (dirtyCount > 0) {
      console.log(`[cache-agent] HIT ${id} — ${cleanRows.length} clean rows (${dirtyCount} flagged for Claude regeneration)`);
    } else {
      console.log(`[cache-agent] HIT ${id} — ${cleanRows.length} rows`);
    }

    if (cleanRows.length > 0) {
      // Return clean rows even if partial.
      // fast mode: serves what's available (e.g. 13/15) — fast and mostly correct
      // ai mode:   cache agent result is ignored; generation agent always runs
      // Dirty rows are simply excluded — they won't appear in Quick Generate output.
      // AI Generate produces a full clean 15 from scratch via Claude.
      return {
        ...state,
        cacheHit:      true,
        source:        "cache",
        rows:          cleanRows,
        dirtySkipped:  dirtyCount,
        overview:      cached.overview || null,
        inputTokens:   0,
        outputTokens:  0
      };
    }
  }

  console.log(`[cache-agent] MISS ${id} — proceeding to generation`);
  return { ...state, cacheHit: false };
}

module.exports = { run };
