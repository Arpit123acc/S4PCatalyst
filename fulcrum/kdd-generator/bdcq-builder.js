// bdcq-builder.js — Fulcrum · BDCQ Excel Generator
"use strict";

const XLSX = require("xlsx");
const path = require("path");
const fs   = require("fs");

/**
 * Build a BDCQ Excel workbook from bdcq-questions.json data.
 *
 * @param {object} opts
 * @param {object}   opts.bdcqData        — parsed bdcq-questions.json
 * @param {string[]} opts.scopeIds        — scope item IDs to filter (empty = all)
 * @param {string[]} opts.selectedColumns — columns to include (empty = all)
 * @param {string[]} opts.countries       — 2-letter country codes to include (empty = global only)
 * @param {string}   opts.clientName      — client name (for cover sheet)
 * @param {string}   opts.projectName     — project name (for cover sheet)
 * @param {string}   opts.filename        — output filename (without .xlsx)
 * @param {string}   opts.outputDir       — directory to write to
 * @returns {{ filename: string, rowCount: number }}
 */
function buildBDCQExcel({ bdcqData, scopeIds = [], selectedColumns = [], countries = [], clientName = "", projectName = "", filename, outputDir }) {
  if (!bdcqData || !Array.isArray(bdcqData.domains)) {
    throw new Error("Invalid bdcqData: missing domains array");
  }
  if (!filename) throw new Error("filename is required");
  if (!outputDir) throw new Error("outputDir is required");

  const wb = XLSX.utils.book_new();

  // ── Normalise filters ────────────────────────────────────────────────────────
  const scopeSet   = new Set((scopeIds   || []).map(s => s.toUpperCase().trim()).filter(Boolean));
  const countrySet = new Set((countries  || []).map(c => c.toUpperCase().trim()).filter(Boolean));

  // Determine if a sheet name is a 2-letter country code
  const isCountrySheet = name => /^[A-Za-z]{2}$/.test(name);

  // ── Collect filtered rows ────────────────────────────────────────────────────
  const allRows = [];
  let allHeaders = [];   // union of all column headers found (for column selection)
  const headerSet = new Set();

  for (const domain of bdcqData.domains) {
    const domainName = domain.domain || "";
    for (const sheet of (domain.sheets || [])) {
      const sheetName = sheet.sheet || "";

      // Country filter: skip 2-letter sheets not in the countries list
      if (isCountrySheet(sheetName)) {
        if (countrySet.size > 0 && !countrySet.has(sheetName.toUpperCase())) {
          continue;
        }
        // If countrySet is empty → no countries selected → skip all country sheets
        if (countrySet.size === 0) {
          continue;
        }
      }

      // Collect header names
      (sheet.headers || []).forEach(h => { if (h && !headerSet.has(h)) { headerSet.add(h); allHeaders.push(h); } });

      for (const row of (sheet.rows || [])) {
        // Scope filter: if scopeSet non-empty, at least one cell value must match
        if (scopeSet.size > 0) {
          const values = Object.values(row).map(v => String(v || "").toUpperCase());
          const match  = values.some(v => scopeSet.has(v));
          if (!match) continue;
        }

        // Build output row with Module and Section prepended
        const outRow = { Module: domainName, Section: sheetName, ...row };
        allRows.push(outRow);
      }
    }
  }

  // ── Determine output columns ─────────────────────────────────────────────────
  // Always prepend Module and Section, then selected columns (or all found headers)
  const baseColumns = ["Module", "Section"];
  let dataColumns;
  if (selectedColumns && selectedColumns.length > 0) {
    // Use selected columns — match case-insensitively against found headers
    const headerLower = allHeaders.map(h => h.toLowerCase());
    dataColumns = selectedColumns.filter(c => {
      const cl = c.toLowerCase();
      return allHeaders.some(h => h.toLowerCase() === cl);
    });
    // Ensure we use the original casing from headers
    dataColumns = dataColumns.map(c => {
      const found = allHeaders.find(h => h.toLowerCase() === c.toLowerCase());
      return found || c;
    });
  } else {
    dataColumns = allHeaders;
  }

  const outputColumns = [...baseColumns, ...dataColumns];

  // ── Sheet 1: Cover ───────────────────────────────────────────────────────────
  const today = new Date().toISOString().slice(0, 10);
  const coverData = [
    ["BDCQ Questionnaire"],
    [""],
    ["Client",         clientName  || "—"],
    ["Project",        projectName || "—"],
    ["Generated",      today],
    ["Total Domains",  bdcqData.totalDomains || bdcqData.domains.length],
    ["Total Rows",     allRows.length],
    [""],
    ["Scope Filter",   scopeSet.size > 0 ? [...scopeSet].join(", ") : "All questions included"],
    ["Countries",      countrySet.size > 0 ? [...countrySet].join(", ") : "Global sheets only"],
    [""],
    ["Source",         "SAP Roadmap Viewer — BDCQ Export"],
    ["Tool",           "Fulcrum · SAP S/4HANA Cloud PE"],
    [""],
    ["Columns included", outputColumns.join(", ")]
  ];

  const wsCover = XLSX.utils.aoa_to_sheet(coverData);
  // Style the title row
  wsCover["A1"] = { v: "BDCQ Questionnaire", t: "s", s: { font: { bold: true, sz: 16 } } };
  wsCover["!cols"] = [{ wch: 22 }, { wch: 60 }];
  XLSX.utils.book_append_sheet(wb, wsCover, "Cover");

  // ── Sheet 2: BDCQ Questionnaire ──────────────────────────────────────────────
  const wsData = [];
  // Header row
  wsData.push(outputColumns);

  // Data rows
  for (const row of allRows) {
    const outRow = outputColumns.map(col => {
      const val = row[col];
      return (val === undefined || val === null) ? "" : String(val);
    });
    wsData.push(outRow);
  }

  const wsQuestionnaire = XLSX.utils.aoa_to_sheet(wsData);

  // Column widths — "Question" type cols get 60 chars, others 22
  wsQuestionnaire["!cols"] = outputColumns.map(col => {
    const cl = col.toLowerCase();
    if (cl.includes("question") || cl.includes("guidance") || cl.includes("notes") || cl.includes("description")) {
      return { wch: 60 };
    }
    return { wch: 22 };
  });

  // Freeze top row
  wsQuestionnaire["!freeze"] = { xSplit: 0, ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft", state: "frozen" };

  XLSX.utils.book_append_sheet(wb, wsQuestionnaire, "BDCQ Questionnaire");

  // ── Write file ───────────────────────────────────────────────────────────────
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const outFilename = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
  const outPath     = path.join(outputDir, outFilename);
  XLSX.writeFile(wb, outPath);

  return { filename: outFilename, rowCount: allRows.length };
}

module.exports = { buildBDCQExcel };
