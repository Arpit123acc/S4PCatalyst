// bdcq-builder.js — Fulcrum · BDCQ Excel Generator
// Kept in bdcq-agent/ so BDCQ logic stays separate from KDD logic.
// Uses xlsx from kdd-generator/node_modules (already installed).
"use strict";

const XLSX = require("../kdd-generator/node_modules/xlsx");
const { rowMatchesScope } = require("./scope-match");
const path = require("path");
const fs   = require("fs");

/**
 * Build a BDCQ Excel workbook from bdcq-questions.json data.
 *
 * @param {object}   opts
 * @param {object}   opts.bdcqData        — parsed bdcq-questions.json
 * @param {string[]} opts.scopeIds        — scope item IDs to filter (empty = all)
 * @param {string[]} opts.selectedColumns — columns to include (empty = all)
 * @param {string[]} opts.countries       — 2-letter country codes to include (empty = global only)
 * @param {string}   opts.clientName      — client name (for cover sheet)
 * @param {string}   opts.projectName     — project name (for cover sheet)
 * @param {string}   opts.overviewText    — cover sheet overview paragraph (optional)
 * @param {string}   opts.clientLogo      — base64 data URL for client logo (optional)
 * @param {string}   opts.partnerLogo     — base64 data URL for partner logo (optional)
 * @param {string}   opts.filename        — output filename (without .xlsx)
 * @param {string}   opts.outputDir       — directory to write to
 * @returns {{ filename: string, rowCount: number, logoFiles: string[] }}
 */
function buildBDCQExcel({
  bdcqData,
  scopeIds        = [],
  selectedDomains = [],   // domain names to include — empty = all
  selectedColumns = [],
  countries       = [],
  clientName      = "",
  projectName     = "",
  overviewText    = "",
  clientLogo      = null,
  partnerLogo     = null,
  filename,
  outputDir
}) {
  if (!bdcqData || !Array.isArray(bdcqData.domains)) {
    throw new Error("Invalid bdcqData: missing domains array");
  }
  if (!filename)  throw new Error("filename is required");
  if (!outputDir) throw new Error("outputDir is required");

  const wb = XLSX.utils.book_new();

  // ── Normalise filters ────────────────────────────────────────────────────────
  const scopeSet   = new Set((scopeIds       || []).map(s => s.toUpperCase().trim()).filter(Boolean));
  const countrySet = new Set((countries      || []).map(c => c.toUpperCase().trim()).filter(Boolean));
  const domainSet  = new Set((selectedDomains|| []).map(d => d.toLowerCase().trim()).filter(Boolean));

  // A sheet name is a locale/country sheet when it is exactly 2 letters
  const isCountrySheet = name => /^[A-Za-z]{2}$/.test(name);

  // ── Collect filtered rows ────────────────────────────────────────────────────
  const allRows   = [];
  const allHeaders = [];   // ordered union of column headers
  const headerSet  = new Set();

  for (const domain of bdcqData.domains) {
    const domainName = domain.domain || "";

    // Domain filter — skip if not in selected set (empty set = include all)
    if (domainSet.size > 0 && !domainSet.has(domainName.toLowerCase().trim())) continue;

    for (const sheet of (domain.sheets || [])) {
      const sheetName = sheet.sheet || "";

      // Country filter ─────────────────────────────────────────────────────────
      if (isCountrySheet(sheetName)) {
        // Country sheets are only included when explicitly requested
        if (countrySet.size === 0 || !countrySet.has(sheetName.toUpperCase())) {
          continue;
        }
      }

      // Collect headers (preserve first-seen order)
      (sheet.headers || []).forEach(h => {
        if (h && !headerSet.has(h)) { headerSet.add(h); allHeaders.push(h); }
      });

      // Row filter ─────────────────────────────────────────────────────────────
      for (const row of (sheet.rows || [])) {
        // A Scope Ref cell holds a LIST ("BD9, 2EQ, I9I"), so comparing the
        // whole cell against one id skipped every multi-item row. Shared with
        // routes.js: the two copies of this test were identical and would have
        // drifted the moment either was edited.
        if (!rowMatchesScope(row, scopeSet)) continue;
        allRows.push({ Module: domainName, Section: sheetName, ...row });
      }
    }
  }

  // ── Determine output columns ─────────────────────────────────────────────────
  // Module and Section are always first; then the requested subset (or all headers).
  const baseColumns = ["Module", "Section"];
  let dataColumns;

  if (selectedColumns && selectedColumns.length > 0) {
    // Match selected column names case-insensitively against headers actually found
    dataColumns = selectedColumns
      .map(c => allHeaders.find(h => h.toLowerCase() === c.toLowerCase()) || null)
      .filter(Boolean);
  } else {
    dataColumns = allHeaders;
  }

  const outputColumns = [...baseColumns, ...dataColumns];

  // ── Sheet 1: Cover ───────────────────────────────────────────────────────────
  const today = new Date().toISOString().slice(0, 10);

  // Save logo files alongside the Excel (SheetJS community doesn't support inline images)
  const logoFiles = [];
  const baseName  = filename.endsWith(".xlsx") ? filename.slice(0, -5) : filename;

  function saveLogo(dataUrl, suffix) {
    if (!dataUrl || typeof dataUrl !== "string") return null;
    // dataUrl format: "data:image/png;base64,<base64>"  or  "data:image/jpeg;base64,<base64>"
    const match = dataUrl.match(/^data:image\/(png|jpeg|jpg|webp|gif);base64,(.+)$/i);
    if (!match) return null;
    const ext    = match[1].toLowerCase().replace("jpeg", "jpg");
    const buf    = Buffer.from(match[2], "base64");
    const fname  = `${baseName}_${suffix}.${ext}`;
    const fpath  = path.join(outputDir, fname);
    fs.writeFileSync(fpath, buf);
    logoFiles.push(fname);
    return fname;
  }

  const clientLogoFile  = saveLogo(clientLogo,  "client_logo");
  const partnerLogoFile = saveLogo(partnerLogo, "partner_logo");

  const defaultOverview =
    "This Business Driven Configuration Questionnaire (BDCQ) has been generated from " +
    "the SAP Roadmap Viewer for the SAP S/4HANA Cloud Public Edition Explore phase. " +
    "Please complete all relevant sections before the Fit-to-Standard workshops.";

  const coverAoa = [
    ["BDCQ — Business Driven Configuration Questionnaire"],
    [""],
    ["Client",          clientName  || "—"],
    ["Project",         projectName || "—"],
    ["Generated",       today],
    [""],
    ["Project Overview"],
    [overviewText || defaultOverview],
    [""],
    ["Scope Summary"],
    ["Total Domains",   bdcqData.totalDomains || bdcqData.domains.length],
    ["Filtered Rows",   allRows.length],
    ["Scope Filter",    scopeSet.size   > 0 ? [...scopeSet].join(", ")   : "All questions included"],
    ["Countries",       countrySet.size > 0 ? [...countrySet].join(", ") : "Global sheets only"],
    [""],
    ["Source",          "SAP Roadmap Viewer — BDCQ Export"],
    ["Tool",            "Fulcrum · SAP S/4HANA Cloud PE"],
    [""],
    ...(clientLogoFile  ? [["Client Logo",   clientLogoFile  + " (saved alongside this file)"]] : []),
    ...(partnerLogoFile ? [["Partner Logo",  partnerLogoFile + " (saved alongside this file)"]] : []),
    [""],
    ["Columns",         outputColumns.join(", ")]
  ];

  const wsCover = XLSX.utils.aoa_to_sheet(coverAoa);
  // Wrap the overview text cell (row 8, col B = index [7][0] in aoa → cell A8)
  // Overview text sits at row index 7 (0-based), col A
  const overviewCellAddr = XLSX.utils.encode_cell({ r: 7, c: 0 });
  if (wsCover[overviewCellAddr]) {
    wsCover[overviewCellAddr].s = { alignment: { wrapText: true, vertical: "top" } };
  }
  wsCover["!cols"] = [{ wch: 22 }, { wch: 90 }];
  wsCover["!rows"] = [];
  wsCover["!rows"][7] = { hpx: 72 };  // taller row for overview text
  XLSX.utils.book_append_sheet(wb, wsCover, "Cover");

  // ── Sheet 2: BDCQ Questionnaire ──────────────────────────────────────────────
  const wsAoa = [outputColumns]; // header row first

  for (const row of allRows) {
    wsAoa.push(outputColumns.map(col => {
      const val = row[col];
      return (val === undefined || val === null) ? "" : String(val);
    }));
  }

  const wsQuestionnaire = XLSX.utils.aoa_to_sheet(wsAoa);

  // Column widths — wide for question/guidance/notes columns
  wsQuestionnaire["!cols"] = outputColumns.map(col => {
    const cl = col.toLowerCase();
    if (cl.includes("question") || cl.includes("guidance") ||
        cl.includes("notes")    || cl.includes("description")) {
      return { wch: 60 };
    }
    return { wch: 22 };
  });

  // Freeze header row
  wsQuestionnaire["!freeze"] = {
    xSplit: 0, ySplit: 1,
    topLeftCell: "A2",
    activePane: "bottomLeft",
    state: "frozen"
  };

  XLSX.utils.book_append_sheet(wb, wsQuestionnaire, "BDCQ Questionnaire");

  // ── Write file ───────────────────────────────────────────────────────────────
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const outFilename = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
  const outPath     = path.join(outputDir, outFilename);
  XLSX.writeFile(wb, outPath);

  return { filename: outFilename, rowCount: allRows.length, logoFiles };
}

module.exports = { buildBDCQExcel };
