"use strict";
// build-full-cache-excel.js
// Generates a single Excel file from the entire kdd-cache.json (all 657 scope items, 9,855 rows).
// Run: node build-full-cache-excel.js

const fs   = require("fs");
const path = require("path");

const CACHE_PATH   = path.join(__dirname, "kdd-cache.json");
const CATALOG_PATH = path.join(__dirname, "scope-catalog.json");
const OUTPUT_DIR   = path.join(__dirname, "output");

const cache   = JSON.parse(fs.readFileSync(CACHE_PATH,   "utf8"));
const catalog = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8"));

// Build a lookup for catalog context
const catalogMap = {};
catalog.processes.forEach(p => { catalogMap[p.id] = p; });

// Collect all rows
const allRows = [];
const overviews = [];

for (const [id, item] of Object.entries(cache.items)) {
  const rows = (item.rows || []).filter(r => !r.needsRegeneration);
  allRows.push(...rows);

  // Build overview entry
  const cat = catalogMap[id];
  if (cat) {
    const lines = (cat.description || "").split("\n").map(l => l.trim()).filter(Boolean);
    let overview = "", flow = "", benefits = "", cur = null;
    for (const line of lines) {
      if (line === "Overview")          { cur = "ov";  continue; }
      if (line === "Key Process Flow")  { cur = "fl";  continue; }
      if (line === "Business Benefits") { cur = "bn";  continue; }
      if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
      if (cur === "ov") overview  += (overview  ? "\n" : "") + line;
      if (cur === "fl") flow      += (flow      ? "\n" : "") + line;
      if (cur === "bn") benefits  += (benefits  ? "\n" : "") + line;
    }
    overviews.push({
      id,
      name:            cat.name,
      module:          item.rows?.[0]?.module || "",
      lob:             cat.lob,
      version:         cache.version || "2608",
      overview:        overview.slice(0, 500),
      keyProcessFlow:  flow.slice(0, 500),
      businessBenefits: benefits.slice(0, 300),
      source:          "SAP for Me"
    });
  }
}

const today = new Date().toISOString().slice(0,10).replace(/-/g,"");

const kddData = {
  rows: allRows,
  meta: {
    client:         "All Scope Items",
    project:        "Full Cache",
    catalogVersion: cache.version || "2608",
    generatedAt:    new Date().toISOString(),
    scopeCount:     Object.keys(cache.items).length,
    overviews,
    agentPipeline:  true
  }
};

// Write kdd-data.json for excel-builder
const jsonPath = path.join(OUTPUT_DIR, "kdd-data-full.json");
fs.writeFileSync(jsonPath, JSON.stringify(kddData, null, 2), "utf8");
console.log(`Wrote ${allRows.length} rows to kdd-data-full.json`);

// Run excel-builder
const xlsxOut = path.join(OUTPUT_DIR, `Accenture_KDD_FullCache_${today}.xlsx`);
const { execSync } = require("child_process");
try {
  execSync(
    `node kdd-generator/excel-builder.js --input output/kdd-data-full.json --output "${xlsxOut}"`,
    { cwd: __dirname, stdio: "inherit" }
  );
  console.log(`\n✅ Excel written: output/Accenture_KDD_FullCache_${today}.xlsx`);
  console.log(`   Rows: ${allRows.length} | Scope items: ${Object.keys(cache.items).length}`);
} catch (e) {
  console.error("Excel build failed:", e.message);
}
