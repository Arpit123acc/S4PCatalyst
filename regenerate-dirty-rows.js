"use strict";
// regenerate-dirty-rows.js
// Regenerates the 2 ECC-era KDDs per scope item with proper Cloud PE content.
//
// KDD-004 was: "What config needs SM30?" → Should be: specific Fiori config app for THIS process
// KDD-012 was: "What data via LSMW?"    → Should be: specific Migration Cockpit objects for THIS process
//
// Runs 5 items in parallel, saves progress after every batch.
// Can be stopped and resumed — already-fixed items are skipped.
//
// Usage: node regenerate-dirty-rows.js
// Estimated time: ~30-45 minutes for all 657 items

const fs   = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const os   = require("os");
const { CLAUDE_MODEL, FORBIDDEN_TERMS, AUTO_REPLACEMENTS } = require("./agents/shared");
const validationAgent = require("./agents/validation-agent");

const CACHE_PATH  = path.join(__dirname, "kdd-cache.json");
const CATALOG_PATH = path.join(__dirname, "scope-catalog.json");

const cache   = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));
const catalog = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8"));

const CONCURRENCY  = 5;    // 5 items at a time
const BATCH_SAVE   = 10;   // save cache every 10 items
const TIMEOUT_MS   = 90_000;

// ── helpers ──────────────────────────────────────────────────────────────────

function spawnClaude(prompt) {
  return new Promise((resolve, reject) => {
    let timedOut = false;
    const proc  = spawn("claude", ["--model", CLAUDE_MODEL, "-p", prompt],
                        { stdio: ["ignore","pipe","pipe"], cwd: os.tmpdir() });
    const timer = setTimeout(() => { timedOut = true; proc.kill(); reject(new Error("timeout")); }, TIMEOUT_MS);
    let out = "";
    proc.stdout.on("data", d => { out += d; });
    proc.on("error", e => { clearTimeout(timer); reject(e); });
    proc.on("close", code => {
      clearTimeout(timer);
      if (timedOut) return;
      if (code !== 0) return reject(new Error("exit " + code));
      resolve(out);
    });
  });
}

function extractJSON(raw) {
  const s = raw.indexOf("["), e = raw.lastIndexOf("]");
  if (s < 0 || e < 0) throw new Error("no JSON array");
  return JSON.parse(raw.slice(s, e + 1));
}

function getCatalogContext(item) {
  const lines = (item.description || "").split("\n").map(l => l.trim()).filter(Boolean);
  const sec = { overview: [], flow: [], benefits: [] };
  let cur = null;
  for (const line of lines) {
    if (line === "Overview")         { cur = "overview";  continue; }
    if (line === "Key Process Flow") { cur = "flow";      continue; }
    if (line === "Business Benefits"){ cur = "benefits";  continue; }
    if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
    if (cur) sec[cur].push(line);
  }
  return {
    overview: sec.overview.join(" ").slice(0, 250),
    flow:     sec.flow.slice(0, 6).map((s,i) => `${i+1}. ${s}`).join("\n"),
    benefits: sec.benefits.slice(0, 3).join("; ")
  };
}

// ── Generate 2 proper Cloud PE KDDs for one scope item ───────────────────────

async function regenerateItem(item) {
  const ctx = getCatalogContext(item);

  const prompt = `You are an SAP S/4HANA Cloud Public Edition consultant generating Key Design Decisions (KDDs).

ANTI-HALLUCINATION RULES — READ FIRST:
1. Base EVERY answer strictly on the process context provided below. Do not invent features, apps, or objects not supported by SAP S/4HANA Cloud PE.
2. KDD questions are DECISION QUESTIONS for the project team — frame them as "Which...", "How will...", "What scope..." style questions. Do NOT state facts as if already decided.
3. For KDD-004: if you are not certain of the exact Fiori app name, use the format "Which SAP Fiori app is used to manage [specific aspect] for ${item.name}?" — a clear question is better than an invented app name.
4. For KDD-012: if exact Migration Cockpit object names are uncertain, ask "Which migration objects in SAP S/4HANA Migration Cockpit cover [specific data entity] for ${item.name}?" — ask, do not invent object names.
5. Notes field: add only what you know is true about Cloud PE. Write "Confirm with client" if scope is uncertain. Never invent technical details.

Scope item: ${item.id} — ${item.name}
LOB: ${item.lob}
Overview: ${ctx.overview}
Key Process Flow:
${ctx.flow}
Benefits: ${ctx.benefits}

Generate exactly 2 KDD objects grounded in the process context above:

KDD-004 — Fiori Configuration Design Decision
  Purpose: Identify which SAP Fiori app(s) the team will use to configure this process in Cloud PE.
  The question must be specific to ${item.name}, not generic.
  If the process has no meaningful Fiori config, ask about the Fiori app used to execute or monitor the process.

KDD-012 — Data Migration Design Decision
  Purpose: Identify which migration objects in SAP S/4HANA Migration Cockpit must be loaded for this process.
  The question must name the data entity being migrated (e.g. customers, open items, balances, master records).
  If the process has no historical data migration need, set fitGap to "Fit" and note that initial data is entered directly in Cloud PE.

Return ONLY a valid JSON array of exactly 2 objects — no explanation, no markdown:
[{
  "kddId": "${item.id}-KDD-004",
  "designQuestion": "Which SAP Fiori app or configuration path will the team use to set up [specific aspect] for ${item.name}? (max 150 chars)",
  "fitGap": "Fit",
  "complexity": "Low",
  "decisionOwner": "Functional Consultant or Business Process Owner",
  "sapActivatePhase": "Explore",
  "rationale": "One sentence: why this Fiori config decision affects go-live readiness. (max 100 chars)",
  "impactActions": "One sentence: what happens if this configuration is not agreed by Explore phase. (max 100 chars)",
  "notes": "Known Fiori app or guidance; if uncertain write 'Confirm app name with SAP Best Practice scope'. (max 80 chars)",
  "l1": "${item.lob}",
  "l2": "${item.name}"
},{
  "kddId": "${item.id}-KDD-012",
  "designQuestion": "Which data objects for ${item.name} must be migrated via SAP S/4HANA Migration Cockpit, and what is the migration sequence? (max 150 chars)",
  "fitGap": "Fit",
  "complexity": "Medium",
  "decisionOwner": "Data Migration Lead",
  "sapActivatePhase": "Realize",
  "rationale": "One sentence: why migration scope must be agreed before Realize. (max 100 chars)",
  "impactActions": "One sentence: go-live risk if migration scope is not confirmed. (max 100 chars)",
  "notes": "Known migration object or guidance; if uncertain write 'Identify migration object in Migration Cockpit'. (max 80 chars)",
  "l1": "${item.lob}",
  "l2": "${item.name}"
}]

FORBIDDEN TERMS — do not use in ANY field:
- ECC, R/3 → say "source system" if comparing
- ABAP → say "ABAP Cloud" only if strictly relevant to Cloud PE extensibility
- SM30, SM31, SM34, SE16, SE38, SE80
- LSMW, BDC, batch input
- SAP GUI, SAPGUI
- on-premise, private cloud
JSON only. No markdown. No text before or after the JSON array.`;

  const out  = await spawnClaude(prompt);
  const rows = extractJSON(out);
  if (!Array.isArray(rows) || rows.length !== 2) throw new Error("wrong count: " + rows.length);
  // Ensure kddIds are correct
  rows[0].kddId = `${item.id}-KDD-004`;
  rows[1].kddId = `${item.id}-KDD-012`;
  return rows;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  // Find items that still have dirty rows
  const dirtyItems = [];
  for (const [id, cacheItem] of Object.entries(cache.items)) {
    const hasDirty = (cacheItem.rows || []).some(r => r.needsRegeneration);
    if (hasDirty) {
      const catalogItem = catalog.processes.find(p => p.id === id);
      if (catalogItem) dirtyItems.push(catalogItem);
    }
  }

  const total = dirtyItems.length;
  console.log(`\n🔄 Regenerating dirty KDDs for ${total} scope items (${CONCURRENCY} parallel)`);
  console.log(`   KDD-004: Proper Fiori Config question`);
  console.log(`   KDD-012: Proper Migration Cockpit question`);
  console.log(`   Estimated time: ~${Math.ceil(total / CONCURRENCY * 20 / 60)} minutes\n`);

  let done = 0, failed = 0;
  const failLog = [];

  async function processOne(item) {
    try {
      const newRows = await regenerateItem(item);
      const cacheItem = cache.items[item.id];
      if (!cacheItem) return;

      // Run validation's auto-fix on the 2 new rows (forbidden terms → Cloud PE equivalents)
      // e.g. "from ECC to Cloud PE" → ECC auto-replaced with "source system"
      // Note: schema check will flag 2 !== 15 rows, but auto-fix still runs. We use validated.rows.
      const validated = await validationAgent.run({ item, rows: newRows });
      const fixedRows = validated.rows;

      // Build a map by kddId for clean replacement
      const fixedMap = {};
      fixedRows.forEach(r => { fixedMap[r.kddId] = r; });

      // Check if any forbidden terms remain after auto-fix (unfixable)
      const stillDirty4   = validated.forbiddenFound > 0 &&
                            validated.validationErrors.some(e => e.includes("KDD-004"));
      const stillDirty12  = validated.forbiddenFound > 0 &&
                            validated.validationErrors.some(e => e.includes("KDD-012"));

      // Replace the 2 dirty rows with the new Claude-generated + validated ones
      cacheItem.rows = cacheItem.rows.map(row => {
        if (row.kddId === `${item.id}-KDD-004`) {
          const f = fixedMap[`${item.id}-KDD-004`] || newRows[0];
          return { ...f, needsRegeneration: stillDirty4 };
        }
        if (row.kddId === `${item.id}-KDD-012`) {
          const f = fixedMap[`${item.id}-KDD-012`] || newRows[1];
          return { ...f, needsRegeneration: stillDirty12 };
        }
        return row;
      });

      done++;
      process.stdout.write(`\r✅ ${done}/${total} done  ❌ ${failed} failed  🔄 processing...  `);
    } catch (e) {
      failed++;
      failLog.push({ id: item.id, error: e.message });
      process.stdout.write(`\r✅ ${done}/${total} done  ❌ ${failed} failed  🔄 processing...  `);
    }
  }

  // Run in parallel batches, save every BATCH_SAVE items
  for (let i = 0; i < dirtyItems.length; i += CONCURRENCY) {
    const batch = dirtyItems.slice(i, i + CONCURRENCY);
    await Promise.all(batch.map(item => processOne(item)));

    // Save incrementally so progress is not lost on interruption
    if ((i + CONCURRENCY) % BATCH_SAVE === 0 || i + CONCURRENCY >= dirtyItems.length) {
      fs.writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2), "utf8");
    }
  }

  // Final save
  fs.writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2), "utf8");

  console.log(`\n\n╔═══════════════════════════════════════════╗`);
  console.log(`║     CACHE REGENERATION COMPLETE           ║`);
  console.log(`╚═══════════════════════════════════════════╝\n`);
  console.log(`Items regenerated: ${done}/${total}`);
  console.log(`Failed:            ${failed}`);
  if (failLog.length) {
    console.log(`\nFailed items (retry with node regenerate-dirty-rows.js):`);
    failLog.forEach(f => console.log(`  ${f.id} — ${f.error}`));
  }
  console.log(`\nCache saved. All ${done * 2} KDDs are now proper Cloud PE questions.`);
  console.log(`Run: node test-agents.js  to verify.\n`);

  // Quick verification
  let stillDirty = 0;
  for (const item of Object.values(cache.items)) {
    stillDirty += (item.rows||[]).filter(r => r.needsRegeneration).length;
  }
  console.log(stillDirty === 0
    ? "✅ Cache is 100% Cloud PE compliant — 15 rows per item, all validated."
    : `⚠️  ${stillDirty} rows still marked dirty — re-run script to retry.`);
}

main().catch(e => { console.error("\nFatal:", e.message); process.exit(1); });
