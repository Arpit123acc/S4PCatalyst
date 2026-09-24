"use strict";
// mark-dirty-rows.js
// Marks rows that had forbidden terms as needsRegeneration: true
// The cache agent will treat these as MISS → Claude regenerates them properly
// This is better than fake text replacement — Claude writes the right Cloud PE question
// from scratch using the SAP process context.
//
// Run: node mark-dirty-rows.js

const fs   = require("fs");
const path = require("path");

// These are the ECC-era KDD IDs baked into every scope item in the original cache.
// KDD-004 = SM30 (table maintenance) → should be "which Fiori config app?"
// KDD-012 = LSMW (data migration)    → should be "which migration objects via Migration Cockpit?"
// We mark these for regeneration so Claude writes proper Cloud PE questions.
const DIRTY_KDD_SUFFIXES = ["KDD-004", "KDD-012"];

// Also mark any row where designQuestion still has ECC-era phrasing
const ECC_PHRASES = [
  "SAP Fiori customizing app",   // our fake replacement for SM30 — not a real app name
  "SAP S/4HANA Migration Cockpit" // OK as a term but the question framing is still ECC
];

const CACHE_PATH = path.join(__dirname, "kdd-cache.json");
const cache = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));

let marked = 0;
let total  = 0;

for (const [scopeId, item] of Object.entries(cache.items)) {
  for (const row of (item.rows || [])) {
    total++;
    const isSuffix   = DIRTY_KDD_SUFFIXES.some(s => (row.kddId || "").endsWith(s));
    const hasEccPhrase = ECC_PHRASES.some(p =>
      Object.values(row).some(v => typeof v === "string" && v.includes(p))
    );
    if (isSuffix || hasEccPhrase) {
      row.needsRegeneration = true;
      marked++;
    }
  }
}

fs.writeFileSync(CACHE_PATH, JSON.stringify(cache, null, 2), "utf8");

console.log("\n╔══════════════════════════════════════════════╗");
console.log("║        DIRTY ROW MARKING COMPLETE            ║");
console.log("╚══════════════════════════════════════════════╝\n");
console.log("Total rows:          " + total);
console.log("Marked for regen:    " + marked + " (" + ((marked/total)*100).toFixed(1) + "%)");
console.log("Served from cache:   " + (total - marked) + " (" + (((total-marked)/total)*100).toFixed(1) + "% — clean)");
console.log("\nThese rows will be skipped by the cache agent.");
console.log("→ Quick Generate: skips these KDDs (scope item shows partial count)");
console.log("→ AI Generate:    Claude writes proper Cloud PE versions from scratch");
console.log("\nRows marked as dirty will NOT appear in Excel unless Claude generates them.");
