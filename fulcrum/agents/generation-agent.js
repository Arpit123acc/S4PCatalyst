// agents/generation-agent.js — Stage 4 of the KDD pipeline
// Split into two shorter Claude calls so user sees progress sooner:
//
//   Call 1 — Question Generation (~25-40s)
//            15 KDD questions with 6 core fields
//            User sees: "[4a/5] Questions generated ✓ — adding detail…"
//
//   Call 2 — Enrichment (~20-35s)
//            Adds rationale, impactActions, notes, decisionOwner, sapActivatePhase
//            User sees: "[4b/5] Detail added ✓ — classifying RICEF…"
//
//   Call 3 — RICEF Classification (~15-30s, Gap/Partial rows only)
//            User sees: "[4c/5] RICEF classified ✓"
//
// Total: ~60-105s vs the previous ~120-150s single-call approach.
// Each call has fewer output tokens → faster, less likely to timeout.
"use strict";

const { spawn, execSync } = require("child_process");
const os   = require("os");
const path = require("path");
const fs   = require("fs");
const {
  CLAUDE_MODEL, CLAUDE_TIMEOUT_MS, KDD_REQUIRED_FIELDS, LOB_MODULE
} = require("./shared");

// ── helpers ──────────────────────────────────────────────────────────────────

function resolveClaudeExe() {
  if (process.platform !== "win32") return "claude";
  if (process.env.CLAUDE_PATH && fs.existsSync(process.env.CLAUDE_PATH)) return process.env.CLAUDE_PATH;
  const home         = process.env.USERPROFILE || os.homedir();
  const appData      = process.env.APPDATA      || path.join(home, "AppData", "Roaming");
  const localAppData = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
  const candidates = [
    path.join(home, ".local", "bin", "claude.exe"),
    path.join(localAppData, "Programs", "claude", "claude.exe"),
    path.join(appData, "npm", "claude.exe"),
    path.join(appData, "npm", "claude.cmd"),   // npm global shim — needs shell:true
    path.join(home, ".local", "bin", "claude.cmd"),
  ];
  for (const p of candidates) { if (fs.existsSync(p)) return p; }
  // Whatever is on PATH — `where` yields the full path with extension (bare
  // "claude" is not spawnable without a shell because Node ignores PATHEXT).
  try {
    const found = execSync("where claude", { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] })
      .split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const pick = found.find(p => p.toLowerCase().endsWith(".exe"))
              || found.find(p => p.toLowerCase().endsWith(".cmd"))
              || found[0];
    if (pick && fs.existsSync(pick)) return pick;
  } catch (_) { /* claude not on PATH */ }
  return "claude";
}

function spawnClaude(prompt, timeoutMs = CLAUDE_TIMEOUT_MS) {
  const claudeExe = resolveClaudeExe();
  const claudeEnv = {
    ...process.env,
    PATH: [
      path.join(os.homedir(), ".local", "bin"),
      path.join(os.homedir(), "AppData", "Local", "Programs", "claude"),
      path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "npm"),
      process.env.PATH || "",
    ].join(process.platform === "win32" ? ";" : ":"),
  };
  return new Promise((resolve, reject) => {
    let timedOut = false;
    const isCmd = process.platform === "win32" && /\.(cmd|bat)$/i.test(claudeExe);
    // Quote .cmd/.bat paths so install dirs with spaces work under shell:true.
    const spawnExe = isCmd ? `"${claudeExe}"` : claudeExe;
    const proc = spawn(
      spawnExe,
      ["--model", CLAUDE_MODEL, "--print", "--dangerously-skip-permissions"],
      { stdio: ["pipe", "pipe", "pipe"], shell: isCmd, cwd: os.tmpdir(), env: claudeEnv }
    );
    proc.stdin.write(prompt, "utf8");
    proc.stdin.end();

    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill();
      reject(new Error(`Claude timed out after ${timeoutMs / 1000}s`));
    }, timeoutMs);

    let stdout = "", stderr = "";
    proc.stdout.on("data", d => { stdout += d; });
    proc.stderr.on("data", d => { stderr += d; });
    proc.on("error", e => {
      clearTimeout(timer);
      const msg = e.code === "ENOENT"
        ? "Claude CLI not found. Install it from claude.ai/code and confirm `claude --version` works in a terminal, then restart START.bat. If it's in a custom location, set the CLAUDE_PATH environment variable to the full path of claude.exe."
        : `Claude CLI error: ${e.message}`;
      reject(new Error(msg));
    });
    proc.on("close", code => {
      clearTimeout(timer);
      if (timedOut) return;
      if (code !== 0) return reject(new Error(`Claude exited ${code}: ${stderr.slice(0, 200)}`));
      resolve(stdout);
    });
  });
}

function extractJSON(raw) {
  // Strip markdown code fences Claude sometimes wraps around JSON
  let cleaned = raw.replace(/^```(?:json)?\s*/gm, "").replace(/^```\s*$/gm, "").trim();

  const s = cleaned.indexOf("["), e = cleaned.lastIndexOf("]");
  if (s < 0 || e < 0) throw new Error("No JSON array in response");

  let jsonStr = cleaned.slice(s, e + 1);

  // Attempt 1: direct parse
  try { return JSON.parse(jsonStr); } catch (_) {}

  // Attempt 2: fix common Claude output issues
  try {
    const repaired = jsonStr
      // Fix unescaped literal newlines/tabs inside JSON string values
      .replace(/"(?:[^"\\]|\\.)*"/g, m =>
        m.replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t")
      )
      // Remove trailing commas before ] or }
      .replace(/,(\s*[}\]])/g, "$1");
    return JSON.parse(repaired);
  } catch (_) {}

  // Attempt 3: log the raw output for diagnosis then throw
  const preview = jsonStr.slice(0, 300).replace(/\n/g, "↵");
  throw new Error(`JSON parse failed. Preview: ${preview}`);
}

function fillDefaults(rows, item, mod) {
  return rows.map((r, i) => ({
    kddId:            r.kddId            || `${item.id}-KDD-${String(i + 1).padStart(3, "0")}`,
    scopeItemId:      item.id,
    scopeItemName:    item.name,
    designQuestion:   r.designQuestion   || "",
    fitGap:           r.fitGap           || "Fit",
    complexity:       r.complexity       || "Low",
    status:           "Open",
    module:           mod,
    l1:               item.lob           || r.l1 || "",
    l2:               r.l2               || "",
    l3:               "",
    l4:               "",
    decisionOwner:    r.decisionOwner    || "",
    sapActivatePhase: r.sapActivatePhase || "Explore",
    rationale:        r.rationale        || "",
    impactActions:    r.impactActions    || "",
    notes:            r.notes            || "",
    decisionMade:     "",
    sapConsultant:    "",
    ricefTitle:       "",
    ricefType:        "None",
    extensibilityType:"None",
    btpRequired:      "No",
    effortEstimate:   "None",
    source:           "SAP for Me - Process Navigator",
    dataSource:       "SAP for Me"
  }));
}

// ── Call 1: Question Generation ───────────────────────────────────────────────
// Asks for only 6 fields — keeps output tokens low → faster response
// ~25-40s for Haiku

async function generateQuestions(item, context, pastDecisions, retryCount = 0) {
  const mod = LOB_MODULE[item.lob] || "Cross";

  const descBlock = [
    context.overview  && `Overview: ${context.overview}`,
    context.flow      && `Key Process Flow:\n${context.flow}`,
    context.benefits  && `Business Benefits: ${context.benefits}`
  ].filter(Boolean).join("\n\n");

  const pastBlock = (pastDecisions || []).length > 0
    ? `\nPast decisions for ${item.id}:\n` +
      pastDecisions.map(d => `• ${d.question} [${d.fitGap || "?"}]`).join("\n")
    : "";

  const prompt = `Generate exactly 15 KDD questions for SAP S/4HANA Cloud Public Edition.

Scope: ${item.id} — ${item.name} | LOB: ${item.lob} | Module: ${mod}

${descBlock}
${pastBlock}

Rules: Cloud PE only. Each question must name a specific Fiori app or BTP service. No ABAP/ECC/SAP GUI/on-premise.

Cover: [2] Fiori Config  [2] Master Data  [2] Approval/Workflow  [1] Outputs/Forms
       [2] Integration  [1] Reporting  [1] Authorisation  [2] Data Migration
       [1] Org Structure  [1] Number Ranges

Return ONLY a JSON array of exactly 15 objects with these 6 fields:
[{
  "kddId": "${item.id}-KDD-001",
  "designQuestion": "specific question naming Fiori app (max 150 chars)",
  "fitGap": "Fit" | "Partial Fit" | "Gap",
  "complexity": "Low" | "Medium" | "High",
  "l1": "top-level area e.g. Finance",
  "l2": "sub-area e.g. Accounts Payable"
}]
JSON only. No markdown.`;

  const out  = await spawnClaude(prompt, CLAUDE_TIMEOUT_MS);
  let   rows = extractJSON(out);

  if (!Array.isArray(rows) || rows.length !== 15) {
    if (retryCount === 0) {
      console.warn(`[generation-agent] ${item.id} call-1 wrong count — retrying`);
      return generateQuestions(item, context, pastDecisions, 1);
    }
    throw new Error(`Call 1: expected 15 rows, got ${Array.isArray(rows) ? rows.length : typeof rows}`);
  }

  console.log(`[generation-agent] ${item.id} — call-1 done (${rows.length} questions)`);
  return rows;
}

// ── Call 2: Enrichment ────────────────────────────────────────────────────────
// Takes the 15 questions from Call 1 and adds the 5 detail fields
// Shorter output per row → ~20-35s

async function enrichQuestions(item, questions, retryCount = 0) {
  const prompt = `You are an SAP S/4HANA Cloud Public Edition consultant.

Add detail fields to these ${questions.length} KDD questions for scope item ${item.id} — ${item.name}.

Questions:
${questions.map((q, i) => `${i + 1}. [${q.kddId}] [${q.fitGap}] ${q.designQuestion}`).join("\n")}

Return ONLY a JSON array of exactly ${questions.length} objects:
[{
  "kddId": "...",
  "decisionOwner": "role e.g. Finance Lead / IT Security Lead / Plant Manager",
  "sapActivatePhase": "Explore" | "Realize" | "Deploy",
  "rationale": "1 sentence why this matters for Cloud PE (max 100 chars)",
  "impactActions": "1 sentence consequence if not decided before go-live (max 100 chars)",
  "notes": "exact Fiori app name or BTP service (max 80 chars)"
}]
Cloud PE only. No ABAP/ECC/SAP GUI. JSON only.`;

  const out    = await spawnClaude(prompt, CLAUDE_TIMEOUT_MS);
  let   detail = extractJSON(out);

  if (!Array.isArray(detail) || detail.length !== questions.length) {
    if (retryCount === 0) {
      console.warn(`[generation-agent] ${item.id} call-2 wrong count — retrying`);
      return enrichQuestions(item, questions, 1);
    }
    // Non-fatal: return questions with empty enrichment rather than failing
    console.warn(`[generation-agent] ${item.id} call-2 failed after retry — using empty enrichment`);
    return questions.map(q => ({ ...q, decisionOwner: "", sapActivatePhase: "Explore",
                                        rationale: "", impactActions: "", notes: "" }));
  }

  // Merge enrichment into questions by position
  const byId = {};
  detail.forEach(d => { byId[d.kddId] = d; });

  return questions.map((q, i) => {
    const d = byId[q.kddId] || detail[i] || {};
    return {
      ...q,
      decisionOwner:    d.decisionOwner    || "",
      sapActivatePhase: d.sapActivatePhase || "Explore",
      rationale:        d.rationale        || "",
      impactActions:    d.impactActions    || "",
      notes:            d.notes            || ""
    };
  });
}

// ── Call 3: RICEF classification ──────────────────────────────────────────────
// Second pass, Gap/Partial rows only — already separate, unchanged

async function classifyRICEF(item, rows) {
  const targets = rows.filter(r => r.fitGap === "Gap" || r.fitGap === "Partial Fit");
  if (!targets.length) return rows;

  const prompt = `SAP S/4HANA Cloud PE extensibility classifier. Scope: ${item.id} — ${item.name}.

${targets.map((r, i) => `${i + 1}. [${r.kddId}] [${r.fitGap}] ${r.designQuestion}`).join("\n")}

Return ONLY JSON array of exactly ${targets.length} objects:
[{
  "kddId": "...",
  "ricefTitle": "short title — ${item.id}",
  "ricefType": "R"|"I"|"C"|"E"|"F"|"W"|"None",
  "extensibilityType": "Key User In-App"|"Developer ABAP Cloud"|"Side-by-Side BTP"|"None",
  "btpRequired": "Yes"|"No"|"Possible",
  "effortEstimate": "S"|"M"|"L"|"XL"|"None"
}]
No on-premise ABAP. JSON only.`;

  try {
    const out        = await spawnClaude(prompt, 60_000);
    const classified = extractJSON(out);
    const byId       = {};
    classified.forEach(c => { byId[c.kddId] = c; });
    return rows.map(r => {
      const c = byId[r.kddId];
      if (!c) return r;
      return { ...r, ricefTitle: c.ricefTitle || "", ricefType: c.ricefType || "None",
               extensibilityType: c.extensibilityType || "None",
               btpRequired: c.btpRequired || "No", effortEstimate: c.effortEstimate || "None" };
    });
  } catch (e) {
    console.warn(`[generation-agent] ${item.id} RICEF skipped: ${e.message}`);
    return rows;   // non-fatal
  }
}

// ── Agent entry point ─────────────────────────────────────────────────────────

/**
 * @param {Object}   state
 * @param {Object}   state.item          - catalog item
 * @param {Object}   state.context       - from context-agent
 * @param {Array}    state.pastDecisions - from library-agent
 * @param {Function} [state.onEvent]     - SSE callback for sub-progress events
 * @returns {Object} updated state with rows, source, inputTokens, outputTokens
 */
async function run(state) {
  const { item, context, pastDecisions, onEvent } = state;
  const mod     = LOB_MODULE[item.lob] || "Cross";
  const startMs = Date.now();

  const emit = (msg) => {
    if (onEvent) onEvent({ type: "progress", item: item.id, name: item.name, message: msg });
  };

  // ── Call 1: Generate questions ────────────────────────────────────────────
  emit(`[4a/5] Generating 15 questions…`);
  const questions = await generateQuestions(item, context, pastDecisions);
  const q1Ms = Date.now() - startMs;
  emit(`[4b/5] Questions ready (${Math.round(q1Ms/1000)}s) — adding rationale & detail…`);

  // ── Call 2: Enrich with detail ────────────────────────────────────────────
  const enriched = await enrichQuestions(item, questions);
  const q2Ms = Date.now() - startMs;
  emit(`[4c/5] Detail added (${Math.round(q2Ms/1000)}s) — classifying RICEF…`);

  // ── Fill defaults & merge ─────────────────────────────────────────────────
  const fullRows = fillDefaults(enriched, item, mod);

  // ── Call 3: RICEF classification ──────────────────────────────────────────
  const withRicef = await classifyRICEF(item, fullRows);

  const totalMs      = Date.now() - startMs;
  const inputTokens  = Math.round((Math.min((item.description || "").length, 800) + 400) / 4);
  const outputTokens = Math.round((6 * 30 + 5 * 25) * 15 / 4);   // 2-call estimate

  console.log(`[generation-agent] ${item.id} — done in ${Math.round(totalMs/1000)}s (3 calls)`);

  return {
    ...state,
    source:       "claude",
    rows:         withRicef,
    inputTokens,
    outputTokens,
    generationMs: totalMs
  };
}

module.exports = { run, spawnClaude, generateQuestions, enrichQuestions, classifyRICEF };
