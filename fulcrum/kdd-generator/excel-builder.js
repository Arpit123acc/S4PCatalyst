// excel-builder.js — Fulcrum · KDD Generator
// Ported from SAPKDDAgent_VS.jsx buildExcel() — no browser dependencies
//
// Usage:
//   node kdd-generator/excel-builder.js --input output/kdd-data.json
//   node kdd-generator/excel-builder.js --input output/kdd-data.json --output output/MyFile.xlsx

"use strict";

const fs   = require("fs");
const path = require("path");
const XLSX = require("xlsx");

const {
  detectOnPrem,
  getDecisionOwner,
  getSAPActivatePhase,
  getRAGStatus,
  getIntegrationFlags,
  getConfigEffort,
  dedup
} = require("./helpers");

// ── CLI argument parsing ────────────────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const get  = flag => { const i = args.indexOf(flag); return i >= 0 ? args[i+1] : null; };
  const inputFile  = get("--input")  || "output/kdd-data.json";
  const outputFile = get("--output") || null;
  return { inputFile, outputFile };
}

// ── Build the Excel workbook ─────────────────────────────────────────────────
function buildExcel(allRows, meta) {
  const wb = XLSX.utils.book_new();

  const HEADERS = [
    "Industry","Module","L1","L2","L3","L4",
    "KDD ID","Scope Item ID","Scope Item Name","Design Question",
    "Decision Made","Rationale","Impact / Actions",
    "Fit/Gap","Complexity","RAG","SAP Activate Phase","Config Effort",
    "Integration Flags","Decision Owner","SAP Consultant","Status",
    "Notes","Source","Data Source",
    // ── Workshop Outcome ──
    "Workshop Date","Decided By","Sign-off Status","Action Items","BPD Reference",
    // ── RICEF / Extensibility ──
    "RICEF Title","RICEF Type","Extensibility Path","BTP Required","Effort Estimate","Complexity"
  ];

  const COL_WIDTHS = [
    18,10,20,25,25,25,
    15,15,35,65,
    30,30,30,
    13,13,10,16,18,
    25,22,22,13,
    30,35,20,
    // Workshop columns
    16,22,16,45,22,
    // RICEF columns
    35,13,22,13,14,13
  ];

  const DISCLAIMER = [
    "WARNING","","","","","","","","",
    "AI-generated. SAP S/4HANA Cloud Public Edition only. Validate with a Cloud PE consultant before use.",
    "","","","","","","","","","","","","","",""
  ];

  // ── KDD Master Log sheet ──────────────────────────────────────────────────
  function makeKDDSheet(rows) {
    const data = rows.map(r => [
      "Cross Industry",
      r.module          || "",
      r.l1              || "",
      r.l2              || "",
      r.l3              || "",
      r.l4              || "",
      r.kddId           || "",
      r.scopeItemId     || "",
      r.scopeItemName   || "",
      r.designQuestion  || "",
      r.decisionMade    || "",
      r.rationale       || "",
      r.impactActions   || "",
      r.fitGap          || "Fit",
      r.complexity      || "Low",
      getRAGStatus(r),
      getSAPActivatePhase(r),
      getConfigEffort(r),
      getIntegrationFlags(r),
      r.decisionOwner   || getDecisionOwner(r),
      r.sapConsultant   || "",
      r.status          || "Open",
      r.notes           || "",
      r.source          || "",
      r.dataSource      || "",
      // Workshop Outcome fields
      r.workshopDate    || "",
      r.decidedBy       || "",
      r.signOffStatus   || "Open",
      r.actionItems     || "",
      r.bpdRef          || "",
      // RICEF / Extensibility fields
      r.ricefTitle          || "",
      r.ricefType           || "None",
      r.extensibilityType   || "None",
      r.btpRequired         || "No",
      r.effortEstimate      || "None",
      r.complexity          || "Low"
    ]);

    const ws = XLSX.utils.aoa_to_sheet([HEADERS, DISCLAIMER, ...data]);
    ws["!cols"]   = COL_WIDTHS.map(w => ({ wch: w }));
    ws["!freeze"] = { xSplit: 0, ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft", state: "frozen" };
    return ws;
  }

  // ── Scope Overview sheet ──────────────────────────────────────────────────
  function makeOverviewSheet(overviews) {
    if (!overviews || overviews.length === 0) return null;
    const OV_HEADERS = [
      "Scope Item ID","Scope Item Name","Module","Line of Business",
      "SAP Version","Overview","Key Process Flow","Business Benefits","Source"
    ];
    const OV_WIDTHS = [15,40,10,28,12,100,100,80,15];
    const rows = overviews.map(o => [
      o.id, o.name, o.module, o.lob, o.version,
      o.overview, o.keyProcessFlow, o.businessBenefits, o.source
    ]);
    const ws = XLSX.utils.aoa_to_sheet([OV_HEADERS, ...rows]);
    ws["!cols"]   = OV_WIDTHS.map(w => ({ wch: w }));
    ws["!freeze"] = { xSplit: 0, ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft", state: "frozen" };
    return ws;
  }

  // ── Instructions sheet ────────────────────────────────────────────────────
  function makeInstructions() {
    const d = [
      ["KDD Master Log", ""],
      ["", ""],
      ["Client",          meta.client          || ""],
      ["Project",         meta.project         || ""],
      ["Generated",       new Date().toLocaleDateString("en-GB")],
      ["Catalog version", meta.catalogVersion  || ""],
      ["", ""],
      ["Column",    "Guidance"],
      ["Source",     "SAP for Me = real extracted content. Claude Knowledge = AI-generated."],
      ["Data Source","Indicates quality level. SAP for Me rows are higher quality."],
      ["Fit/Gap",    "Fit / Partial Fit / Gap"],
      ["Complexity", "Low / Medium / High"],
      ["Status",     "Open / In Progress / Closed"],
      ["", ""],
      ["IMPORTANT",  "SAP S/4HANA Cloud PUBLIC EDITION only — not ECC, not Private Cloud"],
      ["Validate",   "All Fiori app IDs and SPRO paths must be validated by a Cloud PE consultant"],
      ["SAP Help",   "https://help.sap.com/docs/SAP_S4HANA_CLOUD"]
    ];
    const ws = XLSX.utils.aoa_to_sheet(d);
    ws["!cols"] = [{ wch: 20 }, { wch: 80 }];
    return ws;
  }

  // ── Cover sheet ───────────────────────────────────────────────────────────
  function makeCover(dd) {
    const data = [
      ["", ""], ["", ""],
      ["    ACCENTURE", ""], ["", ""],
      ["    KEY DESIGN DECISIONS (KDD) LOG", ""], ["", ""],
      ["    SAP S/4HANA Cloud Public Edition", ""], ["", ""],
      ["    Client:",           meta.client          || "[Client Name]"],
      ["    Project:",          meta.project         || "[Project Name]"],
      ["    Generated:",        new Date().toLocaleDateString("en-GB")],
      ["    Catalog Version:",  meta.catalogVersion  || ""],
      ["    Scope Items:",      meta.scopeCount      || ""],
      ["    Total KDD Items:",  dd.length],
      ["", ""],
      ["    DISCLAIMER", ""],
      ["    This document contains AI-generated KDD questions.", ""],
      ["    SAP S/4HANA Cloud Public Edition only.", ""],
      ["    All items must be validated by a certified Cloud PE consultant.", ""]
    ];
    const ws = XLSX.utils.aoa_to_sheet(data);
    ws["!cols"] = [{ wch: 35 }, { wch: 50 }];
    return ws;
  }

  // ── Summary sheet ─────────────────────────────────────────────────────────
  function makeSummary(dd) {
    const byProcess = dd.reduce((acc, r) => {
      const k = r.scopeItemId || "Unknown";
      acc[k] = (acc[k] || []); acc[k].push(r); return acc;
    }, {});
    const data = [
      ["KDD Summary", ""],
      ["Client",   meta.client  || ""],
      ["Project",  meta.project || ""],
      ["Generated", new Date().toLocaleDateString("en-GB")],
      ["", ""],
      ["Scope Items",     Object.keys(byProcess).length],
      ["Total KDD Items", dd.length],
      ["SAP for Me rows", dd.filter(r => r.dataSource === "SAP for Me").length],
      ["AI Knowledge rows", dd.filter(r => r.dataSource !== "SAP for Me").length],
      ["", ""],
      ["Fit",          dd.filter(r => r.fitGap === "Fit").length],
      ["Partial Fit",  dd.filter(r => r.fitGap === "Partial Fit").length],
      ["Gap",          dd.filter(r => r.fitGap === "Gap").length],
      ["", ""],
      ["Low",    dd.filter(r => r.complexity === "Low").length],
      ["Medium", dd.filter(r => r.complexity === "Medium").length],
      ["High",   dd.filter(r => r.complexity === "High").length],
      ["", ""],
      ["Red",   dd.filter(r => getRAGStatus(r) === "Red").length],
      ["Amber", dd.filter(r => getRAGStatus(r) === "Amber").length],
      ["Green", dd.filter(r => getRAGStatus(r) === "Green").length],
      ["", ""],
      ["── Workshop Outcomes ──", ""],
      ["Approved",  dd.filter(r => r.signOffStatus === "Approved").length],
      ["Deferred",  dd.filter(r => r.signOffStatus === "Deferred").length],
      ["Rejected",  dd.filter(r => r.signOffStatus === "Rejected").length],
      ["Open",      dd.filter(r => !r.signOffStatus || r.signOffStatus === "Open").length],
      ["With BPD Ref", dd.filter(r => r.bpdRef && r.bpdRef.trim()).length],
      ["Action Items", dd.filter(r => r.actionItems && r.actionItems.trim()).length],
      ["", ""],
      ["── RICEF / Extensibility ──", ""],
      ["RICEF Items (Gap rows)",  dd.filter(r => r.ricefType && r.ricefType !== "None").length],
      ["  R — Reports",           dd.filter(r => r.ricefType === "R").length],
      ["  I — Interfaces",        dd.filter(r => r.ricefType === "I").length],
      ["  C — Conversions",       dd.filter(r => r.ricefType === "C").length],
      ["  E — Enhancements",      dd.filter(r => r.ricefType === "E").length],
      ["  F — Forms",             dd.filter(r => r.ricefType === "F").length],
      ["  W — Workflows",         dd.filter(r => r.ricefType === "W").length],
      ["Key User In-App",         dd.filter(r => r.extensibilityType === "Key User In-App").length],
      ["Developer ABAP Cloud",    dd.filter(r => r.extensibilityType === "Developer ABAP Cloud").length],
      ["Side-by-Side BTP",        dd.filter(r => r.extensibilityType === "Side-by-Side BTP").length],
      ["BTP Required",            dd.filter(r => r.btpRequired === "Yes").length],
      ["Effort S (<1 day)",       dd.filter(r => r.effortEstimate === "S").length],
      ["Effort M (1–3 days)",     dd.filter(r => r.effortEstimate === "M").length],
      ["Effort L (1–2 weeks)",    dd.filter(r => r.effortEstimate === "L").length],
      ["Effort XL (>2 weeks)",    dd.filter(r => r.effortEstimate === "XL").length]
    ];
    const ws = XLSX.utils.aoa_to_sheet(data);
    ws["!cols"] = [{ wch: 25 }, { wch: 50 }];
    return ws;
  }

  // ── Assemble workbook ─────────────────────────────────────────────────────
  const dd = dedup(allRows);

  XLSX.utils.book_append_sheet(wb, makeCover(dd),        "Cover");
  XLSX.utils.book_append_sheet(wb, makeInstructions(),   "Instructions");

  const wsOv = makeOverviewSheet(meta.overviews);
  if (wsOv) XLSX.utils.book_append_sheet(wb, wsOv, "Scope Overview");

  XLSX.utils.book_append_sheet(wb, makeKDDSheet(dd),    "KDD Master Log");
  XLSX.utils.book_append_sheet(wb, makeSummary(dd),     "Summary");

  return XLSX.write(wb, { bookType: "xlsx", type: "buffer" });
}

// ── Main ─────────────────────────────────────────────────────────────────────
function main() {
  const { inputFile, outputFile } = parseArgs();

  // Read input JSON
  if (!fs.existsSync(inputFile)) {
    console.error(`❌  Input file not found: ${inputFile}`);
    process.exit(1);
  }

  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(inputFile, "utf8"));
  } catch (e) {
    console.error(`❌  Failed to parse ${inputFile}: ${e.message}`);
    process.exit(1);
  }

  const { rows, meta } = payload;
  if (!rows || !Array.isArray(rows) || rows.length === 0) {
    console.error("❌  No KDD rows found in input file.");
    process.exit(1);
  }

  // Build output path
  const client  = (meta.client  || "").replace(/[^a-zA-Z0-9]/g, "_").substring(0, 20);
  const project = (meta.project || "").replace(/[^a-zA-Z0-9]/g, "_").substring(0, 20);
  const date    = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const defaultName = `Accenture_KDD${client ? "_" + client : ""}${project ? "_" + project : ""}_${date}.xlsx`;
  const outPath = outputFile || path.join("output", defaultName);

  // Ensure output directory exists
  const outDir = path.dirname(outPath);
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  // Build and write
  const buffer = buildExcel(rows, meta);
  fs.writeFileSync(outPath, buffer);

  console.log(`✅  Excel written: ${outPath}`);
  console.log(`    Rows: ${dedup(rows).length}  |  Sheets: Cover · Instructions · Scope Overview · KDD Master Log · Summary`);
}

main();
