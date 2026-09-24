// ppt-builder.js — Fulcrum · KDD Generator
// Generates a professional PPTX status-update deck from kdd-data.json
//
// Usage:
//   node kdd-generator/ppt-builder.js --input output/kdd-data.json
//   node kdd-generator/ppt-builder.js --input output/kdd-data.json --output output/MyFile.pptx

"use strict";

const fs   = require("fs");
const path = require("path");

// pptxgenjs v4 CJS export — handle both direct and .default patterns
const _pptxLib  = require("pptxgenjs");
const PptxGenJS = _pptxLib.default || _pptxLib;

// ── Brand colours (Accenture) ─────────────────────────────────────────────────
const BG_DARK   = "1B1B1B";
const PURPLE    = "A100FF";
const PURPLE_DK = "7B00CC";  // eslint-disable-line no-unused-vars
const WHITE     = "FFFFFF";
const LIGHT_BG  = "F5F5F5";
const TEXT_DARK = "1B1B1B";
const TEXT_MID  = "4B4B4B";
const RED       = "DC2626";
const AMBER     = "D97706";
const GREEN     = "16A34A";
const BORDER    = "E5E7EB";

// ── RAG status — inline (mirrors helpers.js exactly, no require) ──────────────
function getRAGStatus(row) {
  if (row.fitGap === "Gap"         && row.complexity === "High")   return "Red";
  if (row.fitGap === "Gap"         && row.complexity === "Medium") return "Amber";
  if (row.fitGap === "Partial Fit" && row.complexity === "High")   return "Amber";
  if (row.fitGap === "Gap"         && row.complexity === "Low")    return "Amber";
  return "Green";
}

// ── CLI argument parsing (same pattern as excel-builder.js) ───────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const get  = flag => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : null; };
  const inputFile  = get("--input")  || "output/kdd-data.json";
  const outputFile = get("--output") || null;
  return { inputFile, outputFile };
}

// ── Small utilities ───────────────────────────────────────────────────────────
function truncate(str, len) {
  if (!str) return "";
  return str.length > len ? str.substring(0, len - 1) + "…" : str;
}

function ragColor(rag) {
  if (rag === "Red")   return RED;
  if (rag === "Amber") return AMBER;
  return GREEN;
}

function ragLabel(rag) {
  if (rag === "Red")   return "RED";
  if (rag === "Amber") return "AMB";
  return "GRN";
}

function worstRag(ragArray) {
  if (ragArray.includes("Red"))   return "Red";
  if (ragArray.includes("Amber")) return "Amber";
  return "Green";
}

function fmtDate() {
  return new Date().toLocaleDateString("en-GB", {
    day: "2-digit", month: "short", year: "numeric"
  });
}

// ── Footer helper — added to every content slide (2–5) ────────────────────────
function addFooter(slide, meta) {
  const left = [
    "CONFIDENTIAL — SAP S/4HANA Cloud Public Edition",
    meta.client  || "",
    meta.project || ""
  ].filter(Boolean).join(" · ");

  slide.addText(left, {
    x: 0.3, y: 7.15, w: 10.2, h: 0.22,
    fontSize: 8,
    color: TEXT_MID,
    fontFace: "Calibri"
  });

  slide.addText("Accenture", {
    x: 10.6, y: 7.15, w: 2.5, h: 0.22,
    fontSize: 8,
    color: TEXT_MID,
    align: "right",
    fontFace: "Calibri"
  });
}

// ═════════════════════════════════════════════════════════════════════════════
// SLIDE 1 — COVER
// ═════════════════════════════════════════════════════════════════════════════
function addCoverSlide(pptx, meta) {
  const slide = pptx.addSlide();
  slide.background = { color: BG_DARK };

  // Left purple accent bar
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 0.15, h: 7.5,
    fill: { color: PURPLE },
    line: { color: PURPLE, width: 0.5 }
  });

  // Main heading
  slide.addText("KEY DESIGN DECISIONS", {
    x: 0.4, y: 2.2, w: 12.5, h: 0.85,
    fontSize: 36,
    bold: true,
    color: WHITE,
    fontFace: "Calibri"
  });

  // Sub-heading
  slide.addText("SAP S/4HANA Cloud Public Edition", {
    x: 0.4, y: 3.1, w: 12.5, h: 0.42,
    fontSize: 16,
    color: WHITE,
    fontFace: "Calibri"
  });

  // Client · Project (purple highlight)
  const clientProject = [meta.client, meta.project].filter(Boolean).join("  ·  ");
  slide.addText(clientProject || "[Client  ·  Project]", {
    x: 0.4, y: 3.7, w: 12.5, h: 0.38,
    fontSize: 14,
    bold: true,
    color: PURPLE,
    fontFace: "Calibri"
  });

  // Generated date
  slide.addText("Generated: " + fmtDate(), {
    x: 0.4, y: 4.2, w: 12.5, h: 0.32,
    fontSize: 11,
    color: WHITE,
    fontFace: "Calibri"
  });

  // Bottom-right branding
  slide.addText("ACCENTURE", {
    x: 0.4, y: 6.8, w: 12.65, h: 0.38,
    fontSize: 13,
    bold: true,
    color: WHITE,
    align: "right",
    fontFace: "Calibri"
  });
}

// ═════════════════════════════════════════════════════════════════════════════
// SLIDE 2 — HEALTH OVERVIEW
// ═════════════════════════════════════════════════════════════════════════════
function addHealthSlide(pptx, rows, meta) {
  const slide = pptx.addSlide();
  slide.background = { color: LIGHT_BG };

  const total        = rows.length;
  const fitCount     = rows.filter(r => r.fitGap === "Fit").length;
  const gapCount     = rows.filter(r => r.fitGap === "Gap").length;
  const decidedCount = rows.filter(r => r.decisionMade && r.decisionMade.trim()).length;
  const redCount     = rows.filter(r => getRAGStatus(r) === "Red").length;
  const amberCount   = rows.filter(r => getRAGStatus(r) === "Amber").length;
  const greenCount   = rows.filter(r => getRAGStatus(r) === "Green").length;
  const scopeCount   = new Set(rows.map(r => r.scopeItemId).filter(Boolean)).size;
  const healthPct    = total > 0 ? Math.round((decidedCount / total) * 100) : 0;

  // ── Title & subtitle ──
  slide.addText("Project Health Overview", {
    x: 0.4, y: 0.3, w: 12.5, h: 0.48,
    fontSize: 20, bold: true,
    color: TEXT_DARK, fontFace: "Calibri"
  });

  slide.addText(
    `${total} KDD questions  ·  ${scopeCount} scope items  ·  Generated ${fmtDate()}`,
    {
      x: 0.4, y: 0.78, w: 12.5, h: 0.3,
      fontSize: 11, color: TEXT_MID, fontFace: "Calibri"
    }
  );

  // ── 4 stat boxes ─────────────────────────────────────────────────────────
  const BOX_Y = 1.3;
  const BOX_W = 2.6;
  const BOX_H = 1.6;
  const BOX_X = [0.4, 3.5, 6.5, 9.5];

  const statBoxes = [
    { label: "TOTAL KDDs",     value: total,        border: BORDER  },
    { label: "FIT",            value: fitCount,     border: GREEN   },
    { label: "GAP",            value: gapCount,     border: RED     },
    { label: "DECISIONS MADE", value: decidedCount, border: PURPLE  }
  ];

  statBoxes.forEach((box, i) => {
    const x = BOX_X[i];

    // White box with coloured border
    slide.addShape(pptx.ShapeType.rect, {
      x, y: BOX_Y, w: BOX_W, h: BOX_H,
      fill: { color: WHITE },
      line: { color: box.border, width: 2.5 }
    });

    // Label (small, top)
    slide.addText(box.label, {
      x: x + 0.1, y: BOX_Y + 0.18, w: BOX_W - 0.2, h: 0.35,
      fontSize: 9, bold: true,
      color: TEXT_MID, align: "center", fontFace: "Calibri"
    });

    // Value (large number)
    slide.addText(String(box.value), {
      x: x + 0.1, y: BOX_Y + 0.6, w: BOX_W - 0.2, h: 0.75,
      fontSize: 30, bold: true,
      color: TEXT_DARK, align: "center", fontFace: "Calibri"
    });
  });

  // ── RAG summary row ───────────────────────────────────────────────────────
  const RAG_Y = 3.25;
  const RAG_W = 3.9;
  const RAG_H = 0.85;
  const RAG_X = [0.4, 4.7, 9.0];

  const ragBoxes = [
    { label: "RED RISK",   count: redCount,   color: RED   },
    { label: "AMBER RISK", count: amberCount, color: AMBER },
    { label: "GREEN",      count: greenCount, color: GREEN }
  ];

  ragBoxes.forEach((box, i) => {
    const x = RAG_X[i];

    slide.addShape(pptx.ShapeType.rect, {
      x, y: RAG_Y, w: RAG_W, h: RAG_H,
      fill: { color: box.color },
      line: { color: box.color, width: 0.5 }
    });

    slide.addText(`${box.count}  ${box.label}`, {
      x: x + 0.1, y: RAG_Y + 0.14, w: RAG_W - 0.2, h: 0.56,
      fontSize: 18, bold: true,
      color: WHITE, align: "center", fontFace: "Calibri"
    });
  });

  // ── Health score ──────────────────────────────────────────────────────────
  slide.addText(`${healthPct}% decisions captured`, {
    x: 0.4, y: 4.3, w: 7, h: 0.3,
    fontSize: 12,
    color: TEXT_MID, fontFace: "Calibri"
  });

  addFooter(slide, meta);
}

// ═════════════════════════════════════════════════════════════════════════════
// SLIDE 3 — SCOPE STATUS TABLE
// ═════════════════════════════════════════════════════════════════════════════
function addScopeStatusSlide(pptx, rows, meta) {
  const slide = pptx.addSlide();
  slide.background = { color: LIGHT_BG };

  // ── Title ──
  slide.addText("Scope Item Status", {
    x: 0.4, y: 0.3, w: 12.5, h: 0.48,
    fontSize: 20, bold: true,
    color: TEXT_DARK, fontFace: "Calibri"
  });

  // ── Aggregate rows by scope item ─────────────────────────────────────────
  const byItem = {};
  rows.forEach(r => {
    const key = r.scopeItemId || "Unknown";
    if (!byItem[key]) {
      byItem[key] = {
        id:     key,
        name:   r.scopeItemName || key,
        module: r.module || "",
        rows:   []
      };
    }
    byItem[key].rows.push(r);
  });

  const itemStats = Object.values(byItem).map(item => ({
    id:      item.id,
    name:    item.name,
    module:  item.module,
    kdds:    item.rows.length,
    fit:     item.rows.filter(r => r.fitGap === "Fit").length,
    gap:     item.rows.filter(r => r.fitGap === "Gap").length,
    rag:     worstRag(item.rows.map(r => getRAGStatus(r))),
    decided: item.rows.filter(r => r.decisionMade && r.decisionMade.trim()).length
  })).sort((a, b) => b.gap - a.gap);

  const MAX_ROWS    = 15;
  const displayRows = itemStats.slice(0, MAX_ROWS);
  const overflow    = itemStats.length - displayRows.length;

  // ── Build table ───────────────────────────────────────────────────────────
  const hOpt = (align) => ({
    bold: true, color: WHITE, fill: { color: PURPLE },
    fontSize: 9, fontFace: "Calibri",
    align: align || "center", valign: "middle"
  });

  const headerRow = [
    { text: "Scope Item ID",   options: hOpt("left")   },
    { text: "Scope Item Name", options: hOpt("left")   },
    { text: "Module",          options: hOpt("center") },
    { text: "KDDs",            options: hOpt("center") },
    { text: "Fit",             options: hOpt("center") },
    { text: "Gap",             options: hOpt("center") },
    { text: "RAG",             options: hOpt("center") },
    { text: "Decisions Made",  options: hOpt("center") }
  ];

  const dataRows = displayRows.map(item => {
    const rCol = ragColor(item.rag);
    const base = { fontSize: 9, fontFace: "Calibri", valign: "middle", fill: { color: WHITE } };
    return [
      { text: item.id,                   options: { ...base, align: "left",   color: TEXT_DARK             } },
      { text: truncate(item.name, 42),   options: { ...base, align: "left",   color: TEXT_DARK             } },
      { text: item.module,               options: { ...base, align: "center", color: TEXT_DARK             } },
      { text: String(item.kdds),         options: { ...base, align: "center", color: TEXT_DARK             } },
      { text: String(item.fit),          options: { ...base, align: "center", color: GREEN,   bold: true   } },
      { text: String(item.gap),          options: { ...base, align: "center", color: RED,     bold: true   } },
      { text: ragLabel(item.rag),        options: { ...base, align: "center", color: rCol,    bold: true   } },
      { text: String(item.decided),      options: { ...base, align: "center", color: TEXT_DARK             } }
    ];
  });

  slide.addTable([headerRow, ...dataRows], {
    x: 0.4, y: 0.95, w: 12.5,
    rowH: 0.3,
    colW: [1.5, 4.2, 1.0, 0.8, 0.7, 0.7, 0.75, 1.65],
    border: { type: "solid", color: BORDER, pt: 0.5 }
  });

  if (overflow > 0) {
    slide.addText(`… and ${overflow} more items`, {
      x: 0.4, y: 7.0, w: 6, h: 0.22,
      fontSize: 9, italic: true,
      color: TEXT_MID, fontFace: "Calibri"
    });
  }

  addFooter(slide, meta);
}

// ═════════════════════════════════════════════════════════════════════════════
// SLIDE 4 — OPEN DECISIONS
// ═════════════════════════════════════════════════════════════════════════════
function addOpenDecisionsSlide(pptx, rows, meta) {
  const slide = pptx.addSlide();
  slide.background = { color: LIGHT_BG };

  // Filter: not Closed and no decision captured
  const openRows = rows.filter(r =>
    r.status !== "Closed" && (!r.decisionMade || !r.decisionMade.trim())
  );

  // Sort: Red → Amber → Green, then complexity High → Medium → Low
  const ragOrd     = { Red: 0, Amber: 1, Green: 2 };
  const complexOrd = { High: 0, Medium: 1, Low: 2 };
  openRows.sort((a, b) => {
    const rd = (ragOrd[getRAGStatus(a)] ?? 3) - (ragOrd[getRAGStatus(b)] ?? 3);
    if (rd !== 0) return rd;
    return (complexOrd[a.complexity] ?? 3) - (complexOrd[b.complexity] ?? 3);
  });

  // ── Title & subtitle ──
  slide.addText("Open Decisions — Action Required", {
    x: 0.4, y: 0.3, w: 12.5, h: 0.48,
    fontSize: 20, bold: true,
    color: TEXT_DARK, fontFace: "Calibri"
  });

  slide.addText(`${openRows.length} KDDs require a decision before go-live`, {
    x: 0.4, y: 0.78, w: 12.5, h: 0.3,
    fontSize: 11, color: TEXT_MID, fontFace: "Calibri"
  });

  const MAX_ROWS    = 12;
  const displayRows = openRows.slice(0, MAX_ROWS);
  const overflow    = openRows.length - displayRows.length;

  // ── Build table ───────────────────────────────────────────────────────────
  const hOpt = (align) => ({
    bold: true, color: WHITE, fill: { color: PURPLE },
    fontSize: 9, fontFace: "Calibri",
    align: align || "center", valign: "middle"
  });

  const headerRow = [
    { text: "KDD ID",           options: hOpt("center") },
    { text: "Scope Item",       options: hOpt("left")   },
    { text: "Design Question",  options: hOpt("left")   },
    { text: "Owner",            options: hOpt("left")   },
    { text: "Complexity",       options: hOpt("center") },
    { text: "RAG",              options: hOpt("center") }
  ];

  const dataRows = displayRows.map(r => {
    const rag  = getRAGStatus(r);
    const rCol = ragColor(rag);
    const base = { fontSize: 8, fontFace: "Calibri", valign: "middle", fill: { color: WHITE } };
    return [
      { text: r.kddId || "",
        options: { ...base, align: "center", color: TEXT_DARK } },
      { text: truncate(r.scopeItemName || r.scopeItemId || "", 26),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: truncate(r.designQuestion || "", 80),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: truncate(r.decisionOwner || "", 24),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: r.complexity || "",
        options: { ...base, align: "center", color: TEXT_DARK } },
      { text: ragLabel(rag),
        options: { ...base, align: "center", color: rCol, bold: true } }
    ];
  });

  slide.addTable([headerRow, ...dataRows], {
    x: 0.4, y: 1.15, w: 12.5,
    rowH: 0.34,
    colW: [1.1, 1.9, 5.05, 2.1, 1.1, 0.75],
    border: { type: "solid", color: BORDER, pt: 0.5 }
  });

  if (overflow > 0) {
    slide.addText(`… and ${overflow} more`, {
      x: 0.4, y: 7.0, w: 6, h: 0.22,
      fontSize: 9, italic: true,
      color: TEXT_MID, fontFace: "Calibri"
    });
  }

  addFooter(slide, meta);
}

// ═════════════════════════════════════════════════════════════════════════════
// SLIDE 5 — TOP RISKS
// ═════════════════════════════════════════════════════════════════════════════
function addTopRisksSlide(pptx, rows, meta) {
  const slide = pptx.addSlide();
  slide.background = { color: LIGHT_BG };

  // Only Gap + High or Medium complexity
  const riskRows = rows.filter(r =>
    r.fitGap === "Gap" && (r.complexity === "High" || r.complexity === "Medium")
  );

  // ── Title & subtitle ──
  slide.addText("Top Risk Items", {
    x: 0.4, y: 0.3, w: 12.5, h: 0.48,
    fontSize: 20, bold: true,
    color: TEXT_DARK, fontFace: "Calibri"
  });

  slide.addText(
    "Gap items with High or Medium complexity — resolve before Realize phase",
    {
      x: 0.4, y: 0.78, w: 12.5, h: 0.3,
      fontSize: 11, color: TEXT_MID, fontFace: "Calibri"
    }
  );

  const MAX_ROWS    = 10;
  const displayRows = riskRows.slice(0, MAX_ROWS);
  const overflow    = riskRows.length - displayRows.length;

  // ── Build table ───────────────────────────────────────────────────────────
  const hOpt = (align) => ({
    bold: true, color: WHITE, fill: { color: PURPLE },
    fontSize: 9, fontFace: "Calibri",
    align: align || "center", valign: "middle"
  });

  const headerRow = [
    { text: "KDD ID",          options: hOpt("center") },
    { text: "Design Question", options: hOpt("left")   },
    { text: "Scope Item",      options: hOpt("left")   },
    { text: "Owner",           options: hOpt("left")   },
    { text: "Decision Made",   options: hOpt("left")   }
  ];

  const dataRows = displayRows.map(r => {
    const isPending     = !r.decisionMade || !r.decisionMade.trim();
    const decisionText  = isPending ? "— Pending —" : truncate(r.decisionMade, 40);
    const decisionColor = isPending ? AMBER : TEXT_DARK;
    const base = { fontSize: 8, fontFace: "Calibri", valign: "middle", fill: { color: WHITE } };
    return [
      { text: r.kddId || "",
        options: { ...base, align: "center", color: TEXT_DARK } },
      { text: truncate(r.designQuestion || "", 80),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: truncate(r.scopeItemName || r.scopeItemId || "", 26),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: truncate(r.decisionOwner || "", 24),
        options: { ...base, align: "left",   color: TEXT_DARK } },
      { text: decisionText,
        options: { ...base, align: "left",   color: decisionColor, italic: isPending } }
    ];
  });

  slide.addTable([headerRow, ...dataRows], {
    x: 0.4, y: 1.15, w: 12.5,
    rowH: 0.38,
    colW: [1.1, 4.6, 2.4, 2.25, 2.15],
    border: { type: "solid", color: BORDER, pt: 0.5 }
  });

  if (overflow > 0) {
    slide.addText(`… and ${overflow} more`, {
      x: 0.4, y: 7.0, w: 6, h: 0.22,
      fontSize: 9, italic: true,
      color: TEXT_MID, fontFace: "Calibri"
    });
  }

  addFooter(slide, meta);
}

// ═════════════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════════════
async function main() {
  const { inputFile, outputFile } = parseArgs();

  // ── Read input ──
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

  // ── Resolve output path (same naming pattern as excel-builder.js) ─────────
  const client  = (meta.client  || "").replace(/[^a-zA-Z0-9]/g, "_").substring(0, 20);
  const project = (meta.project || "").replace(/[^a-zA-Z0-9]/g, "_").substring(0, 20);
  const date    = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const defaultName =
    `Accenture_KDD${client ? "_" + client : ""}${project ? "_" + project : ""}_${date}.pptx`;
  const outPath = outputFile || path.join("output", defaultName);

  const outDir = path.dirname(outPath);
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  // ── Build presentation ────────────────────────────────────────────────────
  const pptx = new PptxGenJS();
  pptx.layout  = "LAYOUT_WIDE";           // 13.33 × 7.5 inches
  pptx.author  = "Accenture — Fulcrum";
  pptx.company = "Accenture";
  pptx.title   = `KDD Status — ${[meta.client, meta.project].filter(Boolean).join(" ")}`.trim();
  pptx.subject = "SAP S/4HANA Cloud Public Edition — Key Design Decisions";

  addCoverSlide(pptx, meta);
  addHealthSlide(pptx, rows, meta);
  addScopeStatusSlide(pptx, rows, meta);
  addOpenDecisionsSlide(pptx, rows, meta);
  addTopRisksSlide(pptx, rows, meta);

  // ── Write to disk ─────────────────────────────────────────────────────────
  await pptx.writeFile({ fileName: outPath });

  console.log(`✅  PowerPoint written: ${outPath}`);
  console.log(`    Slides: Cover · Health · Scope Status · Open Decisions · Top Risks`);
}

main().catch(err => {
  console.error("❌  Unexpected error:", err.message || String(err));
  process.exit(1);
});
