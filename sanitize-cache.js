"use strict";
// sanitize-cache.js — one-time script to clean kdd-cache.json
// Runs validation agent across all 9,855 rows, auto-fixes forbidden terms,
// reports what was fixed and what still needs attention, saves clean cache.
//
// Run: node sanitize-cache.js

const fs   = require("fs");
const path = require("path");
const { FORBIDDEN_TERMS, AUTO_REPLACEMENTS } = require("./agents/shared");

const CACHE_PATH  = path.join(__dirname, "kdd-cache.json");
const FIELDS      = ["designQuestion","rationale","impactActions","notes","l1","l2","ricefTitle","extensibilityType"];

const cache = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));

let totalRows    = 0;
let fixedRows    = 0;
let unfixedRows  = 0;
let fixedTerms   = 0;
let unfixedTerms = [];

function autoFix(val) {
  let fixed = val;
  for (const [term, replacement] of Object.entries(AUTO_REPLACEMENTS)) {
    const re = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    fixed = fixed.replace(re, replacement);
  }
  return fixed;
}

const ALLOWED_PATTERNS = ["abap cloud", "developer abap cloud"];

function isForbiddenInContext(val, term) {
  const lower = (val || "").toLowerCase();
  const termLower = term.toLowerCase();
  if (!lower.includes(termLower)) return false;
  let idx = 0;
  while ((idx = lower.indexOf(termLower, idx)) !== -1) {
    const context = lower.slice(Math.max(0, idx - 5), idx + termLower.length + 10);
    if (!ALLOWED_PATTERNS.some(p => context.includes(p))) return true;
    idx += termLower.length;
  }
  return false;
}

function hasForbidden(val) {
  return FORBIDDEN_TERMS.some(t => isForbiddenInContext(val, t));
}

for (const [scopeId, item] of Object.entries(cache.items)) {
  const rows = item.rows || [];
  rows.forEach((row, i) => {
    totalRows++;
    let rowChanged = false;
    let rowHadForbidden = false;
    let rowStillDirty = false;

    FIELDS.forEach(field => {
      if (!row[field]) return;
      if (!hasForbidden(row[field])) return;

      rowHadForbidden = true;
      const original = row[field];
      const fixed    = autoFix(original);

      if (!hasForbidden(fixed)) {
        row[field] = fixed;
        rowChanged = true;
        fixedTerms++;
      } else {
        // Still has forbidden term after auto-fix
        const remaining = FORBIDDEN_TERMS.filter(t =>
          fixed.toLowerCase().includes(t.toLowerCase())
        );
        rowStillDirty = true;
        if (unfixedTerms.length < 20) {
          unfixedTerms.push({ kddId: row.kddId, field, term: remaining[0], value: fixed.slice(0,80) });
        }
      }
    });

    if (rowHadForbidden && !rowStillDirty) fixedRows++;
    if (rowStillDirty) unfixedRows++;
  });
}

// Save cleaned cache
fs.writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2), "utf8");

// ── Report ────────────────────────────────────────────────────────────────────
console.log("\n╔══════════════════════════════════════════════════╗");
console.log("║       KDD CACHE SANITIZATION COMPLETE            ║");
console.log("╚══════════════════════════════════════════════════╝\n");
console.log("Total rows scanned:  " + totalRows);
console.log("Rows auto-fixed:     " + fixedRows + " (" + ((fixedRows/totalRows)*100).toFixed(1) + "%)");
console.log("Replacements made:   " + fixedTerms);
console.log("Rows still dirty:    " + unfixedRows + " (need manual review)");
console.log("");

if (unfixedTerms.length) {
  console.log("⚠️  Rows that could not be auto-fixed (first 20):");
  unfixedTerms.forEach(u =>
    console.log("  " + u.kddId + "." + u.field + " — '" + u.term + "': " + u.value)
  );
  console.log("");
}

const cleanPct = (((totalRows - unfixedRows) / totalRows) * 100).toFixed(1);
console.log("Cache cleanliness:   " + cleanPct + "% Cloud PE compliant");
console.log("Cache saved to:      kdd-cache.json");
console.log("");
if (unfixedRows === 0) {
  console.log("✅ Cache is 100% Cloud PE compliant — fallback is safe.");
} else {
  console.log("⚠️  " + unfixedRows + " rows still need attention.");
}
