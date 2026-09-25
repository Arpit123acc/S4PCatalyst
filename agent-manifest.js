"use strict";
/* Self-description for the agents this service hosts.
 *
 * WHY THIS EXISTS
 *   The GrowActivAIte dashboard shows a card per agent. Without an endpoint to
 *   ask, every card's name, status, inputs and outputs are typed into the
 *   dashboard by hand -- a copy of facts that live here, which drifts the first
 *   time anything changes and gives no signal that it has. The BDCQ card
 *   sitting at "Ideation" long after the agent shipped is that drift already.
 *
 *   S4PC Catalyst solved this by serving /api/agent-manifest and letting the
 *   registry point at it rather than duplicate it. This is the same contract,
 *   so both services describe themselves the same way and the dashboard needs
 *   no special case for either.
 *
 * ONE MANIFEST PER AGENT, NOT PER SERVICE
 *   S4PC is one agent in one process. This process hosts several -- BDCQ, KDD,
 *   and Fit-to-Standard when it lands -- because they share a server, not
 *   because they are one thing. A card is a job someone wants done, not a
 *   deployment, so each gets its own manifest and the service lists them.
 *
 * DERIVED, NOT DECLARED, WHEREVER POSSIBLE
 *   counts come from the data files at request time. A manifest that hardcodes
 *   "679 scope items" is a third copy of a number that already exists twice,
 *   and it is the copy nobody updates.
 */

const express = require("express");
const fs = require("fs");
const path = require("path");

const router = express.Router();
const ROOT = path.join(__dirname);

/* Read a JSON file and report a count without throwing. A manifest that 500s
   because a data file is mid-write is worse than one that says "unknown": the
   dashboard would show the agent as down. */
function count(file, pick) {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(ROOT, file), "utf8"));
    const v = pick(d);
    return typeof v === "number" ? v : null;
  } catch {
    return null;
  }
}

function catalogSource() {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(ROOT, "scope-catalog.json"), "utf8"));
    // _source is written by the brain exporter. Absent means the file came
    // from somewhere else (the old Chrome extension), which is worth saying
    // out loud rather than implying it is current.
    return d._source || { origin: "unknown — no _source block", replaces: null };
  } catch {
    return null;
  }
}

const AGENTS = [
  {
    id: "bdcq-agent",
    name: "BDCQ Agent",
    phase: "Explore",
    group: "Solution Confirmation",
    summary: "Automates the business-driven configuration questionnaire and consolidates answers.",
    description:
      "Reads SAP's Business Driven Configuration Questionnaire workbooks, filters them to the " +
      "scope items in play, enriches them with country localisation and industry context, and " +
      "exports a consolidated workbook for the Explore-phase workshop.",
    status: "ready",
    runtime: { kind: "service", engine: "fulcrum", claudeCli: true },
    inputs: [
      { id: "scopeIds", label: "Scope items", type: "string[]", required: false,
        help: "Empty includes every question." },
      { id: "domains", label: "Modules", type: "string[]", required: false },
    ],
    artifacts: [{ pattern: "\\.xlsx$", label: "BDCQ workbook" }],
    ui: "/#bdcq",
  },
  {
    id: "kdd-generator",
    name: "KDD Creation",
    phase: "Explore",
    group: "Solution Confirmation",
    summary: "Captures key design decisions with options, rationale and approver trail.",
    description:
      "Generates 15 Cloud PE-compliant key design decisions per scope item from SAP process " +
      "context, either from the pre-built cache or freshly via the Claude CLI, and exports a " +
      "five-sheet Excel log.",
    status: "ready",
    runtime: { kind: "service", engine: "fulcrum", claudeCli: true },
    inputs: [
      { id: "client", label: "Client", type: "string", required: true },
      { id: "project", label: "Project", type: "string", required: true },
      { id: "scopeIds", label: "Scope items", type: "string[]", required: true },
      { id: "mode", label: "Mode", type: "enum", values: ["fast", "ai"], required: false,
        help: "fast reads the pre-built cache; ai generates via the Claude CLI." },
    ],
    artifacts: [{ pattern: "_KDD_.*\\.xlsx$", label: "KDD log" }],
    ui: "/#kdd-generator",
  },
];

/* Facts about the running service, shared by every agent it hosts. Computed per
   request so a catalogue refresh shows up without a restart. */
function service() {
  return {
    name: "Fulcrum",
    version: require(path.join(ROOT, "package.json")).version,
    data: {
      scopeItems: count("scope-catalog.json", (d) => (d.processes || []).length),
      kddCached: count("kdd-cache.json", (d) => (d.items || []).length),
      bdcqDomains: count("bdcq/bdcq-questions.json", (d) => (d.domains || []).length),
      bdcqQuestions: count("bdcq/bdcq-questions.json", (d) =>
        (d.domains || []).reduce(
          (n, dom) => n + (dom.sheets || []).reduce((m, s) => m + (s.rows || []).length, 0), 0)),
      decisions: count("decisions.json", (d) => (Array.isArray(d) ? d : d.decisions || []).length),
    },
    catalogSource: catalogSource(),
  };
}

/* GET /api/agent-manifest        -> every agent this service hosts
   GET /api/agent-manifest/:id    -> one of them

   The list exists so the dashboard can discover agents rather than being told
   which ids to ask for; the per-agent route exists so a card can point at
   exactly one thing. */
router.get("/", (_req, res) => {
  res.json({ version: 1, service: service(), agents: AGENTS });
});

router.get("/:id", (req, res) => {
  const agent = AGENTS.find((a) => a.id === req.params.id);
  if (!agent) {
    // Name what IS available: a bare 404 leaves the caller guessing whether the
    // id is wrong or the service is the wrong one.
    return res.status(404).json({
      error: `no agent '${req.params.id}' in this service`,
      available: AGENTS.map((a) => a.id),
    });
  }
  res.json({ version: 1, service: service(), agent });
});

module.exports = router;
