"use strict";
// regen-j60.js — Regenerates J60 (Accounts Payable) cache with enriched SAP Cloud PE context
// Runs: node regen-j60.js

const fs     = require("fs");
const path   = require("path");
const { spawnClaude } = require("./agents/generation-agent");
const { FORBIDDEN_TERMS, AUTO_REPLACEMENTS } = require("./agents/shared");

const CACHE_PATH   = path.join(__dirname, "kdd-cache.json");
const CATALOG_PATH = path.join(__dirname, "scope-catalog.json");

// ── SAP Cloud PE AP standard capabilities — used to ground Claude's fit/gap ──
// This is the enrichment layer: explicit knowledge of what S/4HANA Cloud PE
// delivers as standard for Accounts Payable so Claude doesn't guess conservatively.
const AP_STANDARD_KNOWLEDGE = `
SAP S/4HANA Cloud Public Edition — Accounts Payable Standard Capabilities (J60):

STANDARD FIT (no custom development, configuration only):
- Supplier master data: "Manage Business Partners" app (F1602) — create, edit, block suppliers
- Supplier master data completion: "Monitor Supplier Master Data" for completeness checks
- Logistic Invoice Verification (LIV): "Create Supplier Invoice" (F0774) — 3-way match standard
- Invoice parking and posting: "Park Incoming Invoices", "Post Incoming Invoices" apps
- Automatic payment program: "Schedule Payment Runs" app — replaces F110, fully standard
- Payment medium creation: standard payment medium formats (DME, SEPA Credit Transfer, etc.)
- Check printing: standard for applicable countries via output management
- Open item management: "Manage Supplier Line Items" — filter, sort, clear open items
- Accounts payable analytics: "Accounts Payable Aging Report", "Days Payable Outstanding" apps
- Cash discount management: handled within automatic payment program — standard
- GR/IR clearing: "Clear GR/IR Clearing Accounts" — standard Fiori app
- Dunning (supplier credits): standard process
- Down payments / advance payments: standard SAP process
- Foreign currency revaluation: standard via period-end closing
- Withholding tax: standard configuration for applicable countries

PARTIAL FIT (standard capability exists, but client configuration decisions required):
- Payment approval workflow: SAP Flexible Workflow is standard, BUT client must decide
  approval levels, amount thresholds, approver roles, and delegation rules
- SAP Multi-Bank Connectivity (MBC): standard connectivity framework exists, BUT each bank
  requires onboarding/setup; client must decide: MBC vs. file download per bank
- Output management (payment advice, correspondence): BRF+ output control is standard,
  BUT client must configure output conditions, channels (email/print), and form templates
- Supplier invoice tolerance groups: standard config, BUT client defines tolerance %
- Payment terms: standard, BUT client must define their specific payment term codes
- House banks and bank accounts: standard app exists, BUT client must set up each bank

GAP (requires BTP extension or third-party):
- Integration with non-SAP procurement/ERP for invoice receipt (EDI, IDOC to external)
- Automated invoice capture / OCR (requires SAP Document Information Extraction on BTP)
- Supplier portal for invoice submission (requires SAP Business Network or similar)
- Complex multi-entity netting / payment factory (beyond standard MBC scope)
`;

function extractJSON(raw) {
  let cleaned = raw.replace(/^```(?:json)?\s*/gm, "").replace(/^```\s*$/gm, "").trim();
  const s = cleaned.indexOf("["), e = cleaned.lastIndexOf("]");
  if (s < 0 || e < 0) throw new Error("No JSON array in response");
  let jsonStr = cleaned.slice(s, e + 1);
  try { return JSON.parse(jsonStr); } catch (_) {}
  try {
    const repaired = jsonStr
      .replace(/"(?:[^"\\]|\\.)*"/g, m =>
        m.replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t"))
      .replace(/,(\s*[}\]])/g, "$1");
    return JSON.parse(repaired);
  } catch (_) {}
  throw new Error(`JSON parse failed. Preview: ${jsonStr.slice(0, 200)}`);
}

function applyReplacements(text) {
  let t = text;
  Object.entries(AUTO_REPLACEMENTS).forEach(([bad, good]) => {
    t = t.replace(new RegExp(bad, "gi"), good);
  });
  return t;
}

function hasForbidden(text) {
  return FORBIDDEN_TERMS.some(term =>
    new RegExp(`\\b${term}\\b`, "i").test(text)
  );
}

async function regenerateJ60() {
  const catalog   = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8"));
  const cache     = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));
  const item      = catalog.processes.find(p => p.id === "J60");

  if (!item) { console.error("J60 not found in catalog"); process.exit(1); }

  console.log("Regenerating J60 — Accounts Payable with enriched SAP Cloud PE AP context...\n");

  // ── CALL 1: Generate 15 questions with accurate fit/gap ──────────────────────
  const prompt1 = `You are an SAP S/4HANA Cloud Public Edition Accounts Payable specialist.

Generate exactly 15 KDD (Key Design Decision) questions for scope item J60 — Accounts Payable.

${AP_STANDARD_KNOWLEDGE}

SAP Process Description:
Overview: With Accounts Payable, you manage open payable invoices automatically created from purchasing. Automate processing with analytical tools, plan future payables, analyze payment outcomes (cash discounts, DPO). Optional two-step payment approval. SAP Multi-Bank Connectivity (MBC) for bank payments and statements.

Key Process Flow:
1. Manage and complete supplier master data
2. Create invoice from logistics (3-way match)
3. Analyze outstanding payables
4. Pay invoices (automatic payment program)
5. Approve payments (optional flexible workflow)
6. Forward payments to banks via SAP MBC or download payment file
7. Analyze efficiency of payment processing

CRITICAL FIT/GAP RULES — read carefully before assigning:
- Mark "Fit" ONLY when SAP Cloud PE has a complete standard solution and the KDD question is about HOW to configure it (not whether it exists)
- Mark "Partial Fit" when SAP standard exists BUT the client must make meaningful configuration decisions (thresholds, rules, templates, bank-specific setup)
- Mark "Gap" ONLY when the requirement genuinely cannot be met by SAP standard + configuration — requires BTP extension or third-party
- DO NOT mark "Partial Fit" just because configuration is needed — almost all SAP items need configuration. Partial Fit means the standard solution only partially covers the requirement.
- Refer to the standard capabilities list above before assigning fit/gap

Cover these 15 KDD categories exactly (one question each):
1. Fiori app configuration — Supplier master data (Fit expected: Manage Business Partners is standard)
2. Fiori app configuration — Invoice processing / LIV (Fit expected: Create Supplier Invoice is standard)
3. Master data — Supplier master data fields and classifications
4. Automatic payment program configuration (Fit expected: Schedule Payment Runs is standard)
5. Payment approval workflow (Partial Fit expected: Flexible Workflow standard but client defines rules)
6. Bank connectivity — MBC vs. file download decision (Partial Fit: MBC framework standard, per-bank setup required)
7. Output management — Payment advice and correspondence (Partial Fit: BRF+ standard, forms need config)
8. Downstream integration — Modules consuming AP data (FI-GL, Treasury, Controlling)
9. Third-party integration — Non-SAP systems for invoice receipt or bank statements (assess Gap vs Fit)
10. Reporting and analytics — AP KPIs and dashboards
11. Authorisations — Business roles and Fiori catalogue assignments
12. Data migration — Supplier open items and master data via Migration Cockpit
13. Organisational structure — Company code, payment methods, house banks
14. Number ranges — AP document types
15. Tolerance groups — Invoice and payment tolerances (Partial Fit: standard exists, client defines %)

Return ONLY a JSON array of exactly 15 objects:
[{
  "kddId": "J60-KDD-001",
  "designQuestion": "specific question naming the exact Fiori app or BTP service (max 150 chars)",
  "fitGap": "Fit" | "Partial Fit" | "Gap",
  "complexity": "Low" | "Medium" | "High",
  "l1": "Finance",
  "l2": "Accounts Payable"
}]

Cloud PE only. No ABAP/ECC/SAP GUI/on-premise/SM30/SE16. JSON only. No markdown.`;

  console.log("Call 1: Generating 15 questions...");
  const out1 = await spawnClaude(prompt1, 120_000);
  const questions = extractJSON(out1);

  if (!Array.isArray(questions) || questions.length !== 15) {
    throw new Error(`Expected 15 questions, got ${Array.isArray(questions) ? questions.length : typeof questions}`);
  }

  console.log("✅ Call 1 done — Questions generated:");
  questions.forEach(q => console.log(`  ${q.kddId} | ${q.fitGap.padEnd(12)} | ${q.designQuestion.slice(0, 80)}`));

  // ── CALL 2: Enrich with rationale, impact, notes, owner ──────────────────────
  const prompt2 = `You are an SAP S/4HANA Cloud Public Edition Accounts Payable specialist.

Add detail fields to these 15 KDD questions for J60 — Accounts Payable.

Questions:
${questions.map((q, i) => `${i + 1}. [${q.kddId}] [${q.fitGap}] ${q.designQuestion}`).join("\n")}

Return ONLY a JSON array of exactly 15 objects:
[{
  "kddId": "...",
  "decisionOwner": "specific role e.g. AP Lead / Treasury Lead / IT Security Lead / Finance Director",
  "sapActivatePhase": "Explore" | "Realize" | "Deploy",
  "rationale": "1 sentence why this decision matters for Cloud PE AP (max 100 chars)",
  "impactActions": "1 sentence consequence if not decided before go-live (max 100 chars)",
  "notes": "exact Fiori app name, transaction equivalent, or BTP service (max 80 chars)"
}]
Cloud PE only. No ABAP/ECC/SAP GUI. JSON only.`;

  console.log("\nCall 2: Enriching with rationale and detail...");
  const out2 = await spawnClaude(prompt2, 120_000);
  const detail = extractJSON(out2);

  if (!Array.isArray(detail) || detail.length !== 15) {
    throw new Error(`Enrichment: expected 15, got ${Array.isArray(detail) ? detail.length : typeof detail}`);
  }

  const byId = {};
  detail.forEach(d => { byId[d.kddId] = d; });

  const enriched = questions.map((q, i) => {
    const d = byId[q.kddId] || detail[i] || {};
    return { ...q,
      decisionOwner:    d.decisionOwner    || "Finance Lead",
      sapActivatePhase: d.sapActivatePhase || "Explore",
      rationale:        d.rationale        || "",
      impactActions:    d.impactActions    || "",
      notes:            d.notes            || ""
    };
  });

  // ── CALL 3: RICEF for Partial Fit / Gap rows ──────────────────────────────────
  const targets = enriched.filter(r => r.fitGap === "Gap" || r.fitGap === "Partial Fit");
  let withRicef = enriched.map(r => ({
    ...r,
    kddId:             r.kddId,
    scopeItemId:       "J60",
    scopeItemName:     "Accounts Payable",
    status:            "Open",
    module:            "FIN",
    l1:                r.l1 || "Finance",
    l2:                r.l2 || "Accounts Payable",
    l3:                "",
    l4:                "",
    decisionMade:      "",
    sapConsultant:     "",
    ragStatus:         "",
    configEffort:      "",
    integrationFlags:  [],
    dataSource:        "SAP for Me",
    source:            "Fulcrum AI — SME Enriched",
    ricefTitle:        "",
    ricefType:         "None",
    extensibilityType: "None",
    btpRequired:       "No",
    effortEstimate:    "None"
  }));

  if (targets.length > 0) {
    const prompt3 = `SAP S/4HANA Cloud PE extensibility classifier. Scope: J60 — Accounts Payable.

${targets.map((r, i) => `${i + 1}. [${r.kddId}] [${r.fitGap}] ${r.designQuestion}`).join("\n")}

Return ONLY JSON array of exactly ${targets.length} objects:
[{
  "kddId": "...",
  "ricefTitle": "short title — J60",
  "ricefType": "R"|"I"|"C"|"E"|"F"|"W"|"None",
  "extensibilityType": "Key User In-App"|"Developer ABAP Cloud"|"Side-by-Side BTP"|"None",
  "btpRequired": "Yes"|"No"|"Possible",
  "effortEstimate": "S"|"M"|"L"|"XL"|"None"
}]
No on-premise ABAP. JSON only.`;

    console.log(`\nCall 3: Classifying RICEF for ${targets.length} Partial Fit/Gap rows...`);
    try {
      const out3 = await spawnClaude(prompt3, 60_000);
      const classified = extractJSON(out3);
      const byId3 = {};
      classified.forEach(c => { byId3[c.kddId] = c; });
      withRicef = withRicef.map(r => {
        const c = byId3[r.kddId];
        if (!c) return r;
        return { ...r, ricefTitle: c.ricefTitle || "", ricefType: c.ricefType || "None",
                 extensibilityType: c.extensibilityType || "None",
                 btpRequired: c.btpRequired || "No", effortEstimate: c.effortEstimate || "None" };
      });
      console.log("✅ Call 3 done");
    } catch (e) {
      console.warn("⚠ RICEF skipped:", e.message);
    }
  }

  // ── Validation: check for forbidden terms ────────────────────────────────────
  let forbiddenCount = 0;
  withRicef = withRicef.map(r => {
    const fields = ["designQuestion", "rationale", "impactActions", "notes"];
    let dirty = false;
    const cleaned = { ...r };
    fields.forEach(f => {
      if (hasForbidden(cleaned[f] || "")) {
        cleaned[f] = applyReplacements(cleaned[f]);
        dirty = true;
        forbiddenCount++;
      }
    });
    return cleaned;
  });

  if (forbiddenCount > 0) {
    console.warn(`⚠ Auto-fixed ${forbiddenCount} forbidden term(s)`);
  }

  // ── Save to cache ─────────────────────────────────────────────────────────────
  console.log("\n--- Final fit/gap summary ---");
  const fitCount     = withRicef.filter(r => r.fitGap === "Fit").length;
  const partialCount = withRicef.filter(r => r.fitGap === "Partial Fit").length;
  const gapCount     = withRicef.filter(r => r.fitGap === "Gap").length;
  console.log(`Fit: ${fitCount}  |  Partial Fit: ${partialCount}  |  Gap: ${gapCount}`);
  withRicef.forEach(r =>
    console.log(`  ${r.kddId} | ${r.fitGap.padEnd(12)} | ${r.designQuestion.slice(0, 75)}`));

  cache.items["J60"] = {
    rows:              withRicef,
    generatedAt:       new Date().toISOString(),
    source:            "Fulcrum AI — SME Enriched",
    needsRegeneration: false
  };
  cache.updatedAt = new Date().toISOString();
  fs.writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2), "utf8");

  console.log("\n✅ kdd-cache.json updated for J60");
  console.log("   Run: node server.js and Quick Generate J60 to see enriched questions.");
}

regenerateJ60().catch(e => {
  console.error("❌ Error:", e.message);
  process.exit(1);
});
