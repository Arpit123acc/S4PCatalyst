// mcp-server.js — Fulcrum MCP server
// Exposes KDD tools to Claude CLI interactive sessions (skills, direct queries)
// Protocol: JSON-RPC 2.0 over stdio (MCP stdio transport)
//
// Tools exposed:
//   get_process_context       → SAP catalog data for a scope item
//   search_past_decisions     → Query the decisions.json learning store
//   save_approved_decision    → Write an approved decision to the store
//   get_cached_kdds           → Pre-built KDD questions from kdd-cache.json
//   list_decisions_summary    → Stats: how many decisions per scope item / fit-gap
//
// Usage: Claude Code reads .claude/settings.json and starts this as a subprocess.
"use strict";

const fs       = require("fs");
const path     = require("path");
const readline = require("readline");

const ROOT           = __dirname;
const CATALOG_PATH   = path.join(ROOT, "scope-catalog.json");
const CACHE_PATH     = path.join(ROOT, "kdd-cache.json");
const DECISIONS_PATH = path.join(ROOT, "decisions.json");

// ── Load static data at startup ───────────────────────────────────────────────

let catalog  = { processes: [] };
let kddCache = { items: {} };

try { catalog  = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8")); }
catch (e) { process.stderr.write(`[mcp] WARNING: catalog not found at ${CATALOG_PATH}\n`); }

try { kddCache = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8")); }
catch (e) { process.stderr.write(`[mcp] WARNING: kdd-cache not found at ${CACHE_PATH}\n`); }

function readDecisions() {
  try { return JSON.parse(fs.readFileSync(DECISIONS_PATH, "utf8")); }
  catch { return []; }
}
function writeDecisions(arr) {
  fs.writeFileSync(DECISIONS_PATH, JSON.stringify(arr, null, 2), "utf8");
}

// ── Tool schemas ──────────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: "get_process_context",
    description: "Get SAP process context (overview, key process flow, business benefits) for a scope item. Call this before generating KDDs so you understand the specific SAP process.",
    inputSchema: {
      type: "object",
      properties: {
        scopeItemId: { type: "string", description: "SAP scope item ID — e.g. BD6, J59, BEI, 1GA" }
      },
      required: ["scopeItemId"]
    }
  },
  {
    name: "search_past_decisions",
    description: "Search approved KDD decisions from past projects stored in decisions.json. Returns real decisions made by previous project teams for the same scope items — use these to ground suggestions in actual outcomes and show institutional learning.",
    inputSchema: {
      type: "object",
      properties: {
        scopeItemId: { type: "string", description: "Filter by scope item ID (optional — omit to search all)" },
        query:       { type: "string", description: "Keyword to search in questions and decisions (optional)" },
        fitGap:      { type: "string", description: "Filter: Fit | Partial Fit | Gap (optional)" }
      }
    }
  },
  {
    name: "save_approved_decision",
    description: "Save an approved KDD decision to the shared learning store (decisions.json). Call this when a consultant confirms a decision so future project teams benefit from this knowledge.",
    inputSchema: {
      type: "object",
      properties: {
        kddId:       { type: "string", description: "KDD identifier e.g. BD6-KDD-001" },
        scopeItemId: { type: "string", description: "SAP scope item ID" },
        scopeName:   { type: "string", description: "Full scope item name" },
        question:    { type: "string", description: "The design question" },
        decision:    { type: "string", description: "The approved decision text" },
        rationale:   { type: "string", description: "Why this decision was made" },
        fitGap:      { type: "string", description: "Fit | Partial Fit | Gap" },
        complexity:  { type: "string", description: "Low | Medium | High" },
        client:      { type: "string", description: "Client name (can be anonymised e.g. Client A — Manufacturing)" },
        project:     { type: "string", description: "Project name" }
      },
      required: ["scopeItemId", "question", "decision"]
    }
  },
  {
    name: "get_cached_kdds",
    description: "Get pre-built KDD questions from the 9,855-question cache for a scope item. Use as a quality reference or starting point when generating KDDs.",
    inputSchema: {
      type: "object",
      properties: {
        scopeItemId: { type: "string", description: "SAP scope item ID" }
      },
      required: ["scopeItemId"]
    }
  },
  {
    name: "list_decisions_summary",
    description: "Get a summary of all saved decisions — total count, per-scope-item breakdown, fit/gap distribution, and projects covered. Use for gap analysis and reporting.",
    inputSchema: {
      type: "object",
      properties: {}
    }
  }
];

// ── Tool handlers ─────────────────────────────────────────────────────────────

function handleGetProcessContext({ scopeItemId }) {
  const id   = (scopeItemId || "").toUpperCase().trim();
  const item = (catalog.processes || []).find(p => p.id === id);
  if (!item) {
    return { error: `Scope item ${id} not found in catalog (${(catalog.processes||[]).length} items loaded)` };
  }

  // Parse description into named sections
  const lines = (item.description || "").split("\n").map(l => l.trim()).filter(Boolean);
  const sec = { overview: [], flow: [], benefits: [] };
  let cur = null;
  for (const line of lines) {
    if (line === "Overview")          { cur = "overview";  continue; }
    if (line === "Key Process Flow")  { cur = "flow";      continue; }
    if (line === "Business Benefits") { cur = "benefits";  continue; }
    if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
    if (cur) sec[cur].push(line);
  }

  return {
    id:              item.id,
    name:            item.name,
    lob:             item.lob,
    module:          item.module || item.lob,
    overview:        sec.overview.join(" ").slice(0, 400),
    keyProcessFlow:  sec.flow.slice(0, 12).map((s, i) => `${i+1}. ${s}`).join("\n"),
    businessBenefits:sec.benefits.slice(0, 6).join("; ")
  };
}

function handleSearchPastDecisions({ scopeItemId, query, fitGap }) {
  const decisions = readDecisions();
  const q = (query || "").toLowerCase();

  const results = decisions.filter(d => {
    const matchScope  = !scopeItemId || d.scopeItemId === scopeItemId.toUpperCase();
    const matchQuery  = !q ||
      (d.question  || "").toLowerCase().includes(q) ||
      (d.decision  || "").toLowerCase().includes(q) ||
      (d.rationale || "").toLowerCase().includes(q);
    const matchFitGap = !fitGap || d.fitGap === fitGap;
    return (matchScope || matchQuery) && matchFitGap;
  });

  return {
    found:        results.length,
    totalInStore: decisions.length,
    decisions:    results.slice(0, 10).map(d => ({
      kddId:       d.kddId,
      scopeItemId: d.scopeItemId,
      scopeName:   d.scopeName,
      question:    d.question,
      decision:    d.decision,
      rationale:   d.rationale,
      fitGap:      d.fitGap,
      complexity:  d.complexity,
      client:      d.client || "Anonymous"
    }))
  };
}

function handleSaveApprovedDecision(params) {
  const { scopeItemId, question, decision } = params;
  if (!scopeItemId || !question || !decision) {
    return { error: "scopeItemId, question, and decision are required" };
  }

  const decisions = readDecisions();
  const kddId = params.kddId;
  const existingIdx = kddId ? decisions.findIndex(d => d.kddId === kddId) : -1;

  const entry = {
    ...params,
    scopeItemId: (scopeItemId || "").toUpperCase(),
    savedAt: new Date().toISOString()
  };

  if (existingIdx >= 0) {
    decisions[existingIdx] = { ...decisions[existingIdx], ...entry, updatedAt: new Date().toISOString() };
  } else {
    decisions.push(entry);
  }

  writeDecisions(decisions);
  return { saved: true, kddId: entry.kddId || `${entry.scopeItemId}-${Date.now()}`, totalDecisions: decisions.length };
}

function handleGetCachedKDDs({ scopeItemId }) {
  const id    = (scopeItemId || "").toUpperCase().trim();
  const entry = kddCache.items && kddCache.items[id];
  if (!entry) return { found: 0, note: `No cached KDDs for ${id}`, kdds: [] };
  return {
    found: entry.rows.length,
    kdds:  entry.rows.slice(0, 15).map(r => ({
      kddId:          r.kddId,
      designQuestion: r.designQuestion,
      fitGap:         r.fitGap,
      complexity:     r.complexity,
      rationale:      r.rationale,
      notes:          r.notes
    }))
  };
}

function handleListDecisionsSummary() {
  const decisions = readDecisions();
  if (!decisions.length) return { total: 0, byScope: {}, fitGapBreakdown: {}, projects: [] };

  const byScope  = {};
  const byFitGap = { "Fit": 0, "Partial Fit": 0, "Gap": 0 };

  decisions.forEach(d => {
    byScope[d.scopeItemId] = (byScope[d.scopeItemId] || 0) + 1;
    if (d.fitGap && d.fitGap in byFitGap) byFitGap[d.fitGap]++;
  });

  return {
    total:              decisions.length,
    scopeItemsCovered:  Object.keys(byScope).length,
    byScope,
    fitGapBreakdown:    byFitGap,
    projects:           [...new Set(decisions.map(d => d.project).filter(Boolean))]
  };
}

// ── JSON-RPC handler ──────────────────────────────────────────────────────────

function handleRequest(req) {
  const { id, method, params } = req;

  if (method === "initialize") {
    return {
      jsonrpc: "2.0", id,
      result: {
        protocolVersion: "2024-11-05",
        capabilities:    { tools: {} },
        serverInfo:      { name: "explore-accelerator", version: "1.0.0",
                           description: "SAP Cloud PE KDD tools — process context + decisions learning store" }
      }
    };
  }

  // Notifications have no response
  if (method && method.startsWith("notifications/")) return null;

  if (method === "tools/list") {
    return { jsonrpc: "2.0", id, result: { tools: TOOLS } };
  }

  if (method === "tools/call") {
    const toolName = params && params.name;
    const toolArgs = (params && params.arguments) || {};

    let result;
    try {
      switch (toolName) {
        case "get_process_context":    result = handleGetProcessContext(toolArgs);    break;
        case "search_past_decisions":  result = handleSearchPastDecisions(toolArgs);  break;
        case "save_approved_decision": result = handleSaveApprovedDecision(toolArgs); break;
        case "get_cached_kdds":        result = handleGetCachedKDDs(toolArgs);        break;
        case "list_decisions_summary": result = handleListDecisionsSummary();         break;
        default:
          return { jsonrpc: "2.0", id, error: { code: -32601, message: `Unknown tool: ${toolName}` } };
      }
    } catch (e) {
      return { jsonrpc: "2.0", id, error: { code: -32000, message: e.message } };
    }

    return {
      jsonrpc: "2.0", id,
      result: { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] }
    };
  }

  return { jsonrpc: "2.0", id: id || null, error: { code: -32601, message: `Unknown method: ${method}` } };
}

// ── Stdio transport ───────────────────────────────────────────────────────────

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

rl.on("line", line => {
  const trimmed = line.trim();
  if (!trimmed) return;

  let req;
  try { req = JSON.parse(trimmed); }
  catch {
    process.stdout.write(JSON.stringify({
      jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" }
    }) + "\n");
    return;
  }

  const resp = handleRequest(req);
  if (resp) process.stdout.write(JSON.stringify(resp) + "\n");
});

rl.on("close", () => process.exit(0));

process.stderr.write(`[mcp] explore-accelerator MCP server started · catalog: ${(catalog.processes||[]).length} items\n`);