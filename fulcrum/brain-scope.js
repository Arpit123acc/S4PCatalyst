"use strict";
/* Per-scope-item process facts from the S4PC brain: the Fiori applications SAP
 * publishes for a scope item, its process steps, and the roles that perform them.
 *
 * WHAT PROBLEM THIS SOLVES
 *     The KDD templates had no per-item application data, so they fell back to a
 *     nine-entry line-of-business table -- three app names per LoB, of which only
 *     the first was ever read. Every Finance scope item therefore cited the same
 *     app. Across 679 items that produced 268 identical citations, and the names
 *     were generic rather than wrong, which is why it read as acceptable output.
 *
 *     The brain has had the real mapping all along: 675 scope items, 537 of them
 *     carrying applications, 1,449 distinct SAP-published app labels, joined to
 *     steps and roles. This module fetches it.
 *
 * ONE BULK CALL, NOT 679
 *     load() asks for every row once and indexes it by scope item. The templates
 *     stay synchronous -- they were a pure string-interpolation loop and there is
 *     no reason to make them async to read a table that fits in memory.
 *
 * THE SOURCE IS ALWAYS ANNOUNCED
 *     source() returns "brain", "local" or "none", and callers are expected to
 *     print it. A silent fall back to generic data is the failure this module
 *     exists to remove; reintroducing it invisibly would be worse than the
 *     original, because the output would look freshly sourced.
 *
 *     Unset S4PC_MCP_URL is a supported configuration, not an error: the project
 *     is developed on laptops that have no brain. Set S4PC_BRAIN_REQUIRED=1 on a
 *     host where the brain is expected -- there, an unreachable brain throws
 *     rather than quietly degrading to the old behaviour.
 *
 * COVERAGE IS REPORTED, NOT ASSUMED
 *     138 of 675 scope items carry no applications and 83 carry no steps, because
 *     SAP published none. An empty list from here means exactly that. It is not a
 *     lookup failure, and a caller that treats the two the same will report the
 *     catalogue as broken when it is merely incomplete.
 */

const fs = require("fs");
const path = require("path");
const { callTool, MCP_URL } = require("./agent-run");

const TOOL = "get_scope_item_process";
const REQUIRED = /^(1|true|yes)$/i.test(process.env.S4PC_BRAIN_REQUIRED || "");
const TTL_MS = Number(process.env.S4PC_BRAIN_TTL_MS || 15 * 60 * 1000);
const FETCH_TIMEOUT_MS = Number(process.env.S4PC_BRAIN_FETCH_TIMEOUT_MS || 30000);
const ROOT = __dirname;

let _state = null;   // { at, source, bySid, coverage, builtAt, note }

function _empty(source, note) {
  return { at: Date.now(), source, bySid: new Map(), coverage: null, builtAt: null, note };
}

/* The local catalogue carries prose but no applications or steps -- the exporter
   that builds it reads the description feed only. Indexing it lets callers keep
   working offline; it cannot make the app names specific. */
function _fromLocalFile() {
  const p = path.join(ROOT, "scope-catalog.json");
  if (!fs.existsSync(p)) return _empty("none", "no brain configured and scope-catalog.json absent");
  let doc;
  try { doc = JSON.parse(fs.readFileSync(p, "utf8")); }
  catch (e) { return _empty("none", `scope-catalog.json unreadable: ${e.message}`); }
  const bySid = new Map();
  for (const it of doc.processes || []) {
    bySid.set(String(it.id).toUpperCase(), {
      scopeItem: it.id, name: it.name, lob: it.lob,
      applications: [],           // never present in this file
      steps: [], roles: [],
    });
  }
  return {
    at: Date.now(), source: "local", bySid, coverage: null,
    builtAt: doc.extractedAt || null,
    note: "local catalogue: no per-item applications or steps (the exporter does not carry them)",
  };
}

async function _fromBrain() {
  // No filters and a limit above the row count returns every scope item. The
  // ranker scores an absent query uniformly, so nothing is excluded by relevance.
  const payload = await callTool(TOOL, { limit: 2000, with_steps: true },
                                 { timeoutMs: FETCH_TIMEOUT_MS });
  const rows = (payload && payload.results) || [];
  if (!rows.length) {
    // A reachable brain returning nothing is not the same as no brain, and it is
    // not a valid catalogue either. Say which it is rather than caching an empty map.
    throw new Error(`${TOOL} returned 0 rows (index may not be built)`);
  }
  const bySid = new Map();
  for (const r of rows) {
    bySid.set(String(r.scope_item).toUpperCase(), {
      scopeItem: r.scope_item,
      name: r.name,
      lob: r.lob,
      applications: r.applications || [],
      steps: r.steps || [],
      roles: r.roles || [],
      targetRelease: r.target_release,
      changeCategory: r.change_category,
    });
  }
  return {
    at: Date.now(), source: "brain", bySid,
    coverage: payload.coverage || null,
    builtAt: payload.index_built || null,
    note: null,
  };
}

/* Resolve the source once per TTL. Never throws unless the brain is declared
   required -- an unconfigured laptop is a supported state, a misconfigured
   server is not. */
async function load({ force = false } = {}) {
  if (_state && !force && Date.now() - _state.at < TTL_MS) return _state;

  if (!MCP_URL) {
    if (REQUIRED) throw new Error("S4PC_BRAIN_REQUIRED=1 but S4PC_MCP_URL is unset");
    _state = _fromLocalFile();
    return _state;
  }
  try {
    _state = await _fromBrain();
  } catch (err) {
    if (REQUIRED) throw new Error(`brain unreachable and S4PC_BRAIN_REQUIRED=1: ${err.message}`);
    _state = _fromLocalFile();
    _state.note = `brain unreachable (${err.message}); using ${_state.source}`;
  }
  return _state;
}

/* Facts for one scope item. `found` false means the index does not know the id;
   `found` true with an empty applications array means SAP published none. Those
   are different answers and callers must be able to tell them apart. */
function forScopeItem(sid) {
  const key = String(sid || "").toUpperCase();
  const hit = _state && _state.bySid.get(key);
  if (!hit) return { found: false, applications: [], steps: [], roles: [] };
  return { found: true, ...hit };
}

/* One line for a console banner or a UI footer. Callers print this; that is the
   whole mechanism by which the source stays visible. */
function describe() {
  if (!_state) return "scope facts: not loaded";
  const bits = [`source=${_state.source}`, `items=${_state.bySid.size}`];
  if (_state.builtAt) bits.push(`built=${_state.builtAt}`);
  if (_state.coverage && _state.coverage.scope_items_with_apps) {
    bits.push(`with_apps=${_state.coverage.scope_items_with_apps}`);
  }
  if (_state.note) bits.push(`note=${_state.note}`);
  return `scope facts: ${bits.join(" ")}`;
}

function source() { return _state ? _state.source : "none"; }
function coverage() { return _state ? _state.coverage : null; }

module.exports = { load, forScopeItem, describe, source, coverage, TOOL };
