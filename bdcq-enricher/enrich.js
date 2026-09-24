#!/usr/bin/env node
// bdcq-enricher/enrich.js — Fulcrum · BDCQ Enricher
// Reads bdcq/bdcq-questions.json, applies country + industry + Cloud PE enrichment,
// writes output/bdcq-enriched-<date>.html and opens it in the browser.
//
// Usage:
//   node bdcq-enricher/enrich.js
//   node bdcq-enricher/enrich.js --country "India,Germany" --industry Manufacturing
//   node bdcq-enricher/enrich.js --country India --industry "Professional Services" --client "Acme Corp"

"use strict";

const fs       = require("fs");
const path     = require("path");
const readline = require("readline");
const { execSync } = require("child_process");

const {
  COUNTRY_RULES, INDUSTRY_RULES, CLOUDPE_CONSTRAINTS,
  normalizeCountries, normalizeIndustry
} = require("./localization");

// ── CLI argument parsing ──────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const get  = flag => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : null; };
  return {
    countriesArg: get("--country") || get("--countries"),
    industryArg:  get("--industry"),
    clientArg:    get("--client"),
    projectArg:   get("--project"),
    inputFile:    get("--input")  || "bdcq/bdcq-questions.json",
    outputFile:   get("--output") || null,
    noOpen:       args.includes("--no-open")
  };
}

// ── Interactive prompt ────────────────────────────────────────────────────────

async function prompt(rl, question) {
  return new Promise(resolve => rl.question(question, resolve));
}

async function getInputsInteractively(args) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

  console.log("\n▲ Fulcrum · BDCQ Enricher\n");

  const client    = args.clientArg    || await prompt(rl, "  Client name:           ");
  const project   = args.projectArg   || await prompt(rl, "  Project name:          ");
  const countries = args.countriesArg || await prompt(rl,
    "  Countries in scope:    (e.g. India, Germany) ");
  const industry  = args.industryArg  || await prompt(rl,
    "  Industry vertical:     (Manufacturing / Retail / Professional Services / Pharma /\n" +
    "                          Utilities / Financial Services / Oil & Gas / High Tech) ");

  rl.close();
  return { client: client.trim(), project: project.trim(), countries: countries.trim(), industry: industry.trim() };
}

// ── Enrichment engine ─────────────────────────────────────────────────────────

function matchKeywords(text, keywords) {
  const t = text.toLowerCase();
  return keywords.some(kw => t.includes(kw.toLowerCase()));
}

function getCountryEnrichments(row, countryCodes) {
  const combined = [
    row["Process Area"] || "",
    row["SSCUI Reference"] || "",
    row["Topic"] || "",
    row["Question"] || "",
    row["Topic Definition"] || ""
  ].join(" ").toLowerCase();

  const results = [];
  for (const code of countryCodes) {
    const def = COUNTRY_RULES[code];
    if (!def) continue;
    for (const rule of def.rules) {
      if (matchKeywords(combined, rule.processAreaKeywords)) {
        results.push({ code, countryName: def.name, ...rule });
      }
    }
  }
  return results;
}

function getIndustryEnrichment(row, industryKey) {
  if (!industryKey) return null;
  const def = INDUSTRY_RULES[industryKey];
  if (!def) return null;

  const processArea = (row["Process Area"] || "").toLowerCase();
  const topic       = (row["Topic"] || "").toLowerCase();
  const question    = (row["Question"] || "").toLowerCase();
  const combined    = `${processArea} ${topic} ${question}`;

  if (def.excludeKeywords && matchKeywords(combined, def.excludeKeywords)) return null;

  let note = def.processAreaThemes?.default || null;
  for (const [key, text] of Object.entries(def.processAreaThemes || {})) {
    if (key !== "default" && combined.includes(key.toLowerCase())) { note = text; break; }
  }

  const mustAsk = (def.mustAsk || []).find(q =>
    q.toLowerCase().split(" ").filter(w => w.length > 5).some(w => combined.includes(w))
  ) || null;

  if (!note && !mustAsk) return null;
  return { industryName: def.name, note, mustAsk };
}

function getCloudPEConstraint(row) {
  const combined = [
    row["SSCUI Reference"] || "",
    row["Topic"] || "",
    row["Topic Definition"] || "",
    row["Question"] || ""
  ].join(" ").toLowerCase();

  return CLOUDPE_CONSTRAINTS.find(c => matchKeywords(combined, c.keywords)) || null;
}

function buildWorkshopPrompt(row, countryEnrichments, industryEnrichment, cloudPE) {
  const sapQ = (row["Question"] || "").trim();
  if (!sapQ) return "";

  const lines = [];
  const mandatory = countryEnrichments.filter(e => e.mandatory);

  if (mandatory.length > 0) {
    mandatory.forEach(item => {
      lines.push(
        `For your ${item.countryName} entity — ${item.requirement} is mandatory (${item.basis}). ` +
        `SAP Cloud PE approach: ${item.cloudPEApproach} ` +
        (item.confirmPrompt ? `Confirm: ${item.confirmPrompt}` : "")
      );
    });
  } else {
    lines.push(sapQ);
    const nonMandatory = countryEnrichments.filter(e => !e.mandatory);
    if (nonMandatory.length > 0) {
      lines.push("Country context: " + nonMandatory.map(e => `${e.countryName} — ${e.cloudPEApproach}`).join(" | "));
    }
  }

  if (industryEnrichment?.mustAsk) {
    lines.push(`[${industryEnrichment.industryName}] Additional question: ${industryEnrichment.mustAsk}`);
  }

  if (cloudPE) {
    lines.push(
      `Cloud PE note: Clients often expect ${cloudPE.expectation.toLowerCase()}. ` +
      `In S/4HANA Cloud PE: ${cloudPE.reality}` +
      (cloudPE.extensionPath ? ` Extension path: ${cloudPE.extensionPath}` : "")
    );
  }

  return lines.join("\n\n");
}

function enrichRow(row, countryCodes, industryKey) {
  const countryEnrichments = getCountryEnrichments(row, countryCodes);
  const industryEnrichment = getIndustryEnrichment(row, industryKey);
  const cloudPEConstraint  = getCloudPEConstraint(row);
  const workshopPrompt     = buildWorkshopPrompt(row, countryEnrichments, industryEnrichment, cloudPEConstraint);
  return {
    processArea:     row["Process Area"] || "",
    topic:           row["Topic"] || "",
    topicDef:        (row["Topic Definition"] || "").replace(/\n/g, " ").substring(0, 250),
    scopeRef:        row["Scope Ref."] || "",
    sscui:           row["SSCUI Reference"] || "",
    sapQuestion:     row["Question"] || "",
    mandatory:       countryEnrichments.some(e => e.mandatory),
    countryEnrichments,
    industryEnrichment,
    cloudPEConstraint,
    workshopPrompt
  };
}

// ── Process bdcq-questions.json ───────────────────────────────────────────────

function processData(data, countryCodes, industryKey) {
  const enrichedDomains = [];

  for (const domain of (data.domains || [])) {
    const enrichedQuestions = [];

    for (const sheet of (domain.sheets || [])) {
      if (!sheet.headers || sheet.headers.join("").trim().length < 5) continue;

      const hasQuestionCol = sheet.headers.some(h => h === "Question");
      if (!hasQuestionCol) continue;

      for (const row of (sheet.rows || [])) {
        const q = row["Question"];
        if (!q || !q.trim()) continue;

        const relevant = row["Project Relevant? Y/N"];
        if (relevant && relevant.trim().toUpperCase() === "N") continue;

        enrichedQuestions.push(enrichRow(row, countryCodes, industryKey));
      }
    }

    if (enrichedQuestions.length === 0) continue;

    enrichedDomains.push({
      domain: domain.domain,
      filename: domain.filename,
      questions: enrichedQuestions,
      mandatoryCount:  enrichedQuestions.filter(q => q.mandatory).length,
      cloudPECount:    enrichedQuestions.filter(q => q.cloudPEConstraint).length,
      industryCount:   enrichedQuestions.filter(q => q.industryEnrichment).length,
      countryCount:    enrichedQuestions.filter(q => q.countryEnrichments.length > 0).length
    });
  }

  return enrichedDomains;
}

// ── HTML generation ───────────────────────────────────────────────────────────

function esc(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/\n/g, "<br>");
}

function buildQuestionCard(q, idx) {
  const hasMandatory = q.mandatory;
  const hasCountry   = q.countryEnrichments.length > 0;
  const hasIndustry  = !!q.industryEnrichment;
  const hasCloudPE   = !!q.cloudPEConstraint;

  let borderColor = "#1a1a1a";
  if (hasMandatory)     borderColor = "#FF4D4D44";
  else if (hasCloudPE)  borderColor = "#FFAB0044";
  else if (hasCountry)  borderColor = "#00AAFF44";

  const badges = [
    hasMandatory ? `<span class="badge badge-mandatory">⚑ MANDATORY</span>` : "",
    hasCountry && !hasMandatory ? `<span class="badge badge-country">🌍 COUNTRY</span>` : "",
    hasIndustry  ? `<span class="badge badge-industry">◆ INDUSTRY</span>` : "",
    hasCloudPE   ? `<span class="badge badge-cloudpe">⚠ CLOUD PE ${q.cloudPEConstraint.type.toUpperCase()}</span>` : ""
  ].filter(Boolean).join(" ");

  // Mandatory / country blocks
  const countryBlocks = q.countryEnrichments.map(e => `
    <div class="q-section ${e.mandatory ? "mandatory-section" : "country-section"}">
      <div class="q-section-label">${e.mandatory ? "⚑ MANDATORY" : "🌍 COUNTRY"} — ${esc(e.countryName)}</div>
      <div class="q-section-body">
        <strong>${esc(e.requirement)}</strong><br>
        <span class="basis">Basis: ${esc(e.basis)}</span><br>
        <span class="approach">Cloud PE: ${esc(e.cloudPEApproach)}</span>
        ${e.confirmPrompt ? `<br><span class="confirm">✔ Confirm: ${esc(e.confirmPrompt)}</span>` : ""}
      </div>
    </div>`).join("");

  const industryBlock = hasIndustry ? `
    <div class="q-section industry-section">
      <div class="q-section-label">◆ INDUSTRY — ${esc(q.industryEnrichment.industryName)}</div>
      <div class="q-section-body">
        ${esc(q.industryEnrichment.note || "")}
        ${q.industryEnrichment.mustAsk ? `<br><span class="confirm">➕ Add: ${esc(q.industryEnrichment.mustAsk)}</span>` : ""}
      </div>
    </div>` : "";

  const cloudPEBlock = hasCloudPE ? `
    <div class="q-section cloudpe-section">
      <div class="q-section-label">⚠ CLOUD PE — ${esc(q.cloudPEConstraint.type)}</div>
      <div class="q-section-body">
        <span class="expect">Client expects: ${esc(q.cloudPEConstraint.expectation)}</span><br>
        <span class="approach">Cloud PE reality: ${esc(q.cloudPEConstraint.reality)}</span>
        ${q.cloudPEConstraint.extensionPath ? `<br><span class="confirm">Extension path: ${esc(q.cloudPEConstraint.extensionPath)}</span>` : ""}
      </div>
    </div>` : "";

  const workshopBlock = q.workshopPrompt && q.workshopPrompt !== q.sapQuestion ? `
    <div class="workshop-prompt">
      <div class="workshop-label">WORKSHOP PROMPT</div>
      <div class="workshop-text">${esc(q.workshopPrompt)}</div>
    </div>` : "";

  return `
  <div class="q-card"
       style="border-color:${borderColor}"
       data-mandatory="${hasMandatory}"
       data-cloudpe="${hasCloudPE}"
       data-industry="${hasIndustry}"
       data-country="${hasCountry}">
    <div class="q-meta">
      <span class="q-area">${esc(q.processArea)}</span>
      ${q.scopeRef ? `<span class="q-scope">${esc(q.scopeRef)}</span>` : ""}
      ${q.sscui ? `<span class="q-sscui">${esc(q.sscui)}</span>` : ""}
      <span style="flex:1"></span>
      ${badges}
    </div>
    <div class="q-topic">${esc(q.topic)}</div>
    ${q.topicDef ? `<div class="q-topicdef">${esc(q.topicDef)}</div>` : ""}
    <div class="q-original-label">SAP QUESTION</div>
    <div class="q-original">${esc(q.sapQuestion)}</div>
    ${countryBlocks}
    ${industryBlock}
    ${cloudPEBlock}
    ${workshopBlock}
  </div>`;
}

function buildDomainSection(d, idx) {
  const processAreas = [...new Set(d.questions.map(q => q.processArea).filter(Boolean))];
  const qHTML = d.questions.map((q, i) => buildQuestionCard(q, i)).join("\n");

  return `
<div class="domain" id="domain-${idx}">
  <div class="domain-header" onclick="toggleDomain(this)">
    <span class="toggle-icon">▼</span>
    <span class="domain-title">${esc(d.domain)}</span>
    <span class="domain-count">${d.questions.length} questions</span>
    ${d.mandatoryCount > 0 ? `<span class="domain-badge badge-mandatory">⚑ ${d.mandatoryCount}</span>` : ""}
    ${d.cloudPECount   > 0 ? `<span class="domain-badge badge-cloudpe">⚠ ${d.cloudPECount}</span>` : ""}
    ${d.industryCount  > 0 ? `<span class="domain-badge badge-industry">◆ ${d.industryCount}</span>` : ""}
    <span class="domain-visible-count" style="color:#333;font-size:10px;margin-left:auto">${d.questions.length} shown</span>
    <div class="domain-line"></div>
  </div>
  <div class="domain-questions">
    ${qHTML}
  </div>
</div>`;
}

function generateHTML(enrichedDomains, meta) {
  const totalQ     = enrichedDomains.reduce((s, d) => s + d.questions.length, 0);
  const totalMand  = enrichedDomains.reduce((s, d) => s + d.mandatoryCount, 0);
  const totalCPE   = enrichedDomains.reduce((s, d) => s + d.cloudPECount, 0);
  const totalInd   = enrichedDomains.reduce((s, d) => s + d.industryCount, 0);
  const totalCtry  = enrichedDomains.reduce((s, d) => s + d.countryCount, 0);

  const countryNames = meta.countryCodes.map(c => COUNTRY_RULES[c]?.name || c).join(", ") || "—";
  const industryDef  = INDUSTRY_RULES[meta.industryKey];
  const industryName = industryDef ? industryDef.name : (meta.industry || "—");
  const dateStr      = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

  const domainsHTML = enrichedDomains.map((d, i) => buildDomainSection(d, i)).join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BDCQ Enriched — ${esc(meta.client || "Report")} — Fulcrum</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Tahoma,sans-serif;background:#0a0a0a;color:#fff;font-size:13px}

/* ── Header ── */
.header{background:#000;border-bottom:2px solid #A100FF;padding:14px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.brand{display:flex;align-items:center;gap:12px}
.tri{width:0;height:0;border-left:10px solid transparent;border-right:10px solid transparent;border-bottom:18px solid #A100FF;flex-shrink:0}
.brand-title{font-size:13px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}
.brand-sub{font-size:10px;color:#444;margin-top:1px}
.print-btn{background:transparent;border:0.5px solid #333;color:#555;font-size:10px;font-weight:600;padding:5px 14px;border-radius:3px;cursor:pointer;text-transform:uppercase;letter-spacing:.06em}
.print-btn:hover{border-color:#A100FF;color:#A100FF}

/* ── Client bar ── */
.client-bar{background:#050505;border-bottom:0.5px solid #111;padding:7px 28px;display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:#555}
.client-bar strong{color:#ccc}
.client-bar .sep{color:#222}

/* ── Stats ── */
.stats{background:#0d0d0d;border-bottom:0.5px solid #1a1a1a;padding:10px 28px;display:flex;gap:28px;flex-wrap:wrap}
.stat{display:flex;flex-direction:column}
.stat-val{font-size:22px;font-weight:700;line-height:1.1}
.stat-lbl{font-size:9px;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-top:1px}
.c-purple{color:#A100FF}.c-red{color:#FF4D4D}.c-amber{color:#FFAB00}.c-green{color:#00C875}.c-cyan{color:#00AAFF}.c-white{color:#fff}

/* ── Filter bar ── */
.filter-bar{background:#0a0a0a;border-bottom:0.5px solid #111;padding:7px 28px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;position:sticky;top:52px;z-index:99}
.filter-lbl{font-size:9px;color:#333;text-transform:uppercase;letter-spacing:.08em;margin-right:4px}
.fbtn{font-size:10px;font-weight:600;padding:4px 13px;border:0.5px solid #222;background:#111;color:#444;border-radius:3px;cursor:pointer;transition:all .15s;letter-spacing:.04em}
.fbtn:hover{border-color:#A100FF;color:#A100FF}
.fbtn.fa{background:#A100FF22;border-color:#A100FF;color:#A100FF}
.fbtn.fr{background:#FF4D4D15;border-color:#FF4D4D;color:#FF4D4D}
.fbtn.famb{background:#FFAB0015;border-color:#FFAB00;color:#FFAB00}
.fbtn.fg{background:#00C87515;border-color:#00C875;color:#00C875}
.fbtn.fcy{background:#00AAFF15;border-color:#00AAFF;color:#00AAFF}
.filter-status{font-size:10px;color:#333;margin-left:8px}

/* ── Content ── */
.content{max-width:1320px;margin:0 auto;padding:20px 28px 48px}

/* ── Domain ── */
.domain{margin-bottom:28px}
.domain-header{display:flex;align-items:center;gap:10px;cursor:pointer;padding:8px 0;user-select:none}
.domain-header:hover .domain-title{color:#c040ff}
.toggle-icon{font-size:10px;color:#555;width:12px;flex-shrink:0}
.domain-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#A100FF}
.domain-count{font-size:10px;color:#333}
.domain-line{flex:1;height:0.5px;background:#1a1a1a;margin-left:6px}
.domain-badge{font-size:9px;font-weight:700;padding:1px 7px;border-radius:3px}
.domain-questions.collapsed{display:none}

/* ── Question card ── */
.q-card{background:#111;border:0.5px solid #1a1a1a;border-radius:7px;padding:14px 16px;margin-bottom:8px;transition:border-color .15s}
.q-card:hover{border-color:#2a2a2a}

.q-meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:6px}
.q-area{font-size:9px;font-weight:700;color:#444;text-transform:uppercase;letter-spacing:.06em}
.q-scope{font-size:9px;color:#555;background:#1a1a1a;padding:1px 7px;border-radius:3px;font-family:monospace}
.q-sscui{font-size:9px;color:#2a2a2a}

.badge{display:inline-block;font-size:9px;font-weight:700;padding:2px 8px;border-radius:3px}
.badge-mandatory{background:#2a0505;color:#FF4D4D;border:0.5px solid #FF4D4D55}
.badge-country{background:#001a2a;color:#00AAFF;border:0.5px solid #00AAFF55}
.badge-industry{background:#0a1a00;color:#00C875;border:0.5px solid #00C87555}
.badge-cloudpe{background:#1a1000;color:#FFAB00;border:0.5px solid #FFAB0055}

.q-topic{font-size:13px;font-weight:600;color:#ddd;margin-bottom:4px}
.q-topicdef{font-size:10px;color:#444;line-height:1.5;margin-bottom:8px;font-style:italic}
.q-original-label{font-size:9px;color:#333;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
.q-original{font-size:12px;color:#555;margin-bottom:10px;line-height:1.5}

/* ── Enrichment sections ── */
.q-section{margin-top:8px;padding:10px 13px;border-radius:5px;line-height:1.6}
.q-section-label{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
.q-section-body{font-size:11px}
.basis{color:#777;font-size:10px}
.approach{color:#aaa}
.expect{color:#888}
.confirm{font-weight:600}

.mandatory-section{background:#1a0505;border-left:2px solid #FF4D4D}
.mandatory-section .q-section-label{color:#FF4D4D}
.mandatory-section .approach{color:#FFa090}
.mandatory-section .confirm{color:#FFd0c0}

.country-section{background:#001828;border-left:2px solid #00AAFF}
.country-section .q-section-label{color:#00AAFF}
.country-section .approach{color:#80ccff}
.country-section .confirm{color:#a0e0ff}

.industry-section{background:#091600;border-left:2px solid #00C875}
.industry-section .q-section-label{color:#00C875}
.industry-section .q-section-body{color:#90e8b8}
.industry-section .confirm{color:#b0f8d0;font-weight:600}

.cloudpe-section{background:#160f00;border-left:2px solid #FFAB00}
.cloudpe-section .q-section-label{color:#FFAB00}
.cloudpe-section .approach{color:#ffd080}
.cloudpe-section .confirm{color:#ffe8a0;font-weight:600}

/* ── Workshop prompt ── */
.workshop-prompt{background:#0d0d0d;border:0.5px solid #252525;border-radius:5px;padding:11px 14px;margin-top:10px}
.workshop-label{font-size:9px;font-weight:800;color:#A100FF;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.workshop-text{font-size:12px;color:#ccc;line-height:1.7}

/* ── Footer ── */
.footer{background:#000;border-top:0.5px solid #111;padding:10px 28px;font-size:9px;color:#333;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-top:40px}

/* ── Print ── */
@media print{
  .filter-bar,.print-btn{display:none!important}
  .header{position:static}
  .q-card{break-inside:avoid;page-break-inside:avoid}
  body{background:#fff;color:#000}
  .domain-questions.collapsed{display:block!important}
}
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="tri"></div>
    <div>
      <div class="brand-title">Fulcrum · BDCQ Enricher</div>
      <div class="brand-sub">SAP S/4HANA Cloud Public Edition · Explore Phase</div>
    </div>
  </div>
  <button class="print-btn" onclick="window.print()">🖨 Print / Save PDF</button>
</div>

<div class="client-bar">
  <span><strong>${esc(meta.client || "—")}</strong></span>
  <span class="sep">·</span>
  <span>${esc(meta.project || "—")}</span>
  <span class="sep">·</span>
  <span>Countries: <strong>${esc(countryNames)}</strong></span>
  <span class="sep">·</span>
  <span>Industry: <strong>${esc(industryName)}</strong></span>
  <span class="sep">·</span>
  <span style="color:#333">${dateStr}</span>
</div>

<div class="stats">
  <div class="stat"><span class="stat-val c-white">${totalQ}</span><span class="stat-lbl">Questions</span></div>
  <div class="stat"><span class="stat-val c-red">${totalMand}</span><span class="stat-lbl">⚑ Mandatory</span></div>
  <div class="stat"><span class="stat-val c-amber">${totalCPE}</span><span class="stat-lbl">⚠ Cloud PE Flags</span></div>
  <div class="stat"><span class="stat-val c-green">${totalInd}</span><span class="stat-lbl">◆ Industry Notes</span></div>
  <div class="stat"><span class="stat-val c-cyan">${totalCtry}</span><span class="stat-lbl">🌍 Country Notes</span></div>
  <div class="stat"><span class="stat-val c-purple">${enrichedDomains.length}</span><span class="stat-lbl">Domains</span></div>
</div>

<div class="filter-bar">
  <span class="filter-lbl">Filter:</span>
  <button class="fbtn fa"   onclick="setFilter('all',this)">All</button>
  <button class="fbtn"      onclick="setFilter('mandatory',this)">⚑ Mandatory (${totalMand})</button>
  <button class="fbtn"      onclick="setFilter('cloudpe',this)">⚠ Cloud PE Gaps (${totalCPE})</button>
  <button class="fbtn"      onclick="setFilter('industry',this)">◆ Industry Notes (${totalInd})</button>
  <button class="fbtn"      onclick="setFilter('country',this)">🌍 Country Notes (${totalCtry})</button>
  <span class="filter-status" id="filterStatus"></span>
</div>

<div class="content">
${domainsHTML}
</div>

<div class="footer">
  <span>Fulcrum · BDCQ Enricher · SAP S/4HANA Cloud Public Edition</span>
  <span>Mandatory items must be validated with a certified compliance advisor · Cloud PE constraints subject to SAP release changes</span>
  <span>Generated ${dateStr}</span>
</div>

<script>
function setFilter(filter, btn) {
  document.querySelectorAll('.fbtn').forEach(b =>
    b.className = b.className.replace(/ f[a-z]+$/, "").replace(/ fa| fr| famb| fg| fcy/g, "").trim()
  );
  const cls = { all:'fa', mandatory:'fr', cloudpe:'famb', industry:'fg', country:'fcy' };
  btn.className += ' ' + (cls[filter] || 'fa');

  let shown = 0, total = 0;
  document.querySelectorAll('.q-card').forEach(card => {
    total++;
    const visible = filter === 'all'
      || (filter === 'mandatory' && card.dataset.mandatory === 'true')
      || (filter === 'cloudpe'   && card.dataset.cloudpe   === 'true')
      || (filter === 'industry'  && card.dataset.industry  === 'true')
      || (filter === 'country'   && card.dataset.country   === 'true');
    card.style.display = visible ? '' : 'none';
    if (visible) shown++;
  });

  document.querySelectorAll('.domain').forEach(d => {
    const vis = [...d.querySelectorAll('.q-card')].filter(c => c.style.display !== 'none').length;
    const el  = d.querySelector('.domain-visible-count');
    if (el) el.textContent = vis + ' shown';
  });

  const statusEl = document.getElementById('filterStatus');
  if (statusEl) statusEl.textContent = filter !== 'all' ? `${shown} of ${total} shown` : '';
}

function toggleDomain(el) {
  const qs   = el.closest('.domain').querySelector('.domain-questions');
  const icon = el.querySelector('.toggle-icon');
  const collapsed = qs.classList.toggle('collapsed');
  icon.textContent = collapsed ? '▶' : '▼';
}
</script>
</body>
</html>`;
}

// ── Open in browser ───────────────────────────────────────────────────────────

function openBrowser(filePath) {
  const abs = path.resolve(filePath);
  try {
    if (process.platform === "win32") execSync(`start "" "${abs}"`, { stdio: "ignore" });
    else if (process.platform === "darwin") execSync(`open "${abs}"`, { stdio: "ignore" });
    else execSync(`xdg-open "${abs}"`, { stdio: "ignore" });
  } catch (e) {
    console.log(`  Open manually: file://${abs}`);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  const args   = parseArgs();
  const inputs = await getInputsInteractively(args);

  const countryCodes = normalizeCountries(inputs.countries);
  const industryKey  = normalizeIndustry(inputs.industry);

  if (countryCodes.length === 0) {
    console.log("\n⚠  No recognised countries — continuing without country localisation.\n");
  }

  // Read BDCQ data
  const inputFile = args.inputFile;
  if (!fs.existsSync(inputFile)) {
    console.error(`\n❌  Not found: ${inputFile}`);
    console.error(`   Run the SAP Deck Agent extension on the Roadmap Viewer page first.\n`);
    process.exit(1);
  }

  let data;
  try {
    data = JSON.parse(fs.readFileSync(inputFile, "utf8"));
  } catch (e) {
    console.error(`\n❌  Failed to parse ${inputFile}: ${e.message}\n`);
    process.exit(1);
  }

  console.log(`\n  Catalog: ${data.totalDomains || 0} domains  ·  Generated: ${data.generatedAt || "unknown"}`);
  console.log(`  Countries: ${countryCodes.length > 0 ? countryCodes.join(", ") : "none"}`);
  console.log(`  Industry:  ${industryKey || "none"}\n`);
  console.log("  Enriching…");

  const enrichedDomains = processData(data, countryCodes, industryKey);

  const totalQ    = enrichedDomains.reduce((s, d) => s + d.questions.length, 0);
  const totalMand = enrichedDomains.reduce((s, d) => s + d.mandatoryCount, 0);
  const totalCPE  = enrichedDomains.reduce((s, d) => s + d.cloudPECount, 0);
  const totalInd  = enrichedDomains.reduce((s, d) => s + d.industryCount, 0);

  console.log(`\n  ✓ ${totalQ} questions enriched across ${enrichedDomains.length} domains`);
  console.log(`    ⚑ Mandatory: ${totalMand}  ⚠ Cloud PE: ${totalCPE}  ◆ Industry: ${totalInd}\n`);

  // Generate HTML
  const meta = {
    client: inputs.client,
    project: inputs.project,
    countryCodes,
    industryKey,
    industry: inputs.industry
  };

  const html = generateHTML(enrichedDomains, meta);

  // Write output
  const outDir  = "output";
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const date       = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const clientSlug = (inputs.client || "").replace(/[^a-zA-Z0-9]/g, "_").substring(0, 20);
  const outFile    = args.outputFile || path.join(outDir, `BDCQ_Enriched${clientSlug ? "_" + clientSlug : ""}_${date}.html`);

  fs.writeFileSync(outFile, html, "utf8");
  console.log(`  📄 Report: ${outFile}\n`);

  if (!args.noOpen) openBrowser(outFile);
}

main().catch(e => {
  console.error("\n❌  " + e.message);
  process.exit(1);
});
