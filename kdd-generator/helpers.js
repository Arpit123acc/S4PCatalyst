// helpers.js — Fulcrum · KDD Generator
// Ported from SAPKDDAgent_VS.jsx — no browser dependencies

"use strict";

const ONPREM_TERMS = [
  "ABAP","user exit","BADI","BAdI","SE16","SM30","SE38","SE80",
  "CMOD","SMOD","on-premise","on premise","private cloud","ECC","R/3",
  "table maintenance","SM31","SM34","SAP GUI","SAPGUI","OBYC","backend custom"
];

function detectOnPrem(row) {
  const text = [
    row.designQuestion, row.rationale, row.impactActions, row.notes
  ].join(" ").toLowerCase();
  return ONPREM_TERMS.filter(t => text.includes(t.toLowerCase()));
}

function getContent(d) {
  const a = d && d.tabs && d.tabs["All Content"] && d.tabs["All Content"].raw || "";
  const b = d && d.rawData && d.rawData.fullPageText || "";
  return a.length > b.length ? a : b;
}

function parseSections(content) {
  const lines = content.split("\n").map(l => l.trim()).filter(Boolean);
  const sec = { overview: [], keyProcessFlow: [], capabilities: [] };
  const MAP = {
    "Overview": "overview",
    "Key Process Flow": "keyProcessFlow",
    "Solution Capabilities": "capabilities"
  };
  const SKIP = new Set([
    "Info Label","Leading","Name","Version","Compare","Update",
    "Additional Information","Description","Diagrams","Accelerators",
    "Line of Business","Solution Area","Title","Category","Type"
  ]);
  let cur = null;
  for (const line of lines) {
    let matched = false;
    for (const k of Object.keys(MAP)) {
      if (line === k || line.startsWith(k)) { cur = MAP[k]; matched = true; break; }
    }
    if (matched) continue;
    if (SKIP.has(line) || line.length < 2) continue;
    if (cur && sec[cur]) sec[cur].push(line);
  }
  return sec;
}

function getModule(sec) {
  const c = (sec.capabilities || []).join(" ");
  if (c.includes("Credit") || c.includes("Receivable")) return "FIN";
  if (c.includes("MRP")    || c.includes("Production"))  return "PP";
  if (c.includes("Sales")  || c.includes("Order"))       return "SD";
  if (c.includes("Procurement") || c.includes("Purchase")) return "MM";
  if (c.includes("Finance") || c.includes("Accounting")) return "FIN";
  return "Cross";
}

function parseForOverview(sapData) {
  if (!sapData) return null;
  const content = getContent(sapData);
  if (!content) return null;
  const meta = sapData._meta || {};
  const sec  = parseSections(content);
  const sections = { overview: "", keyProcessFlow: "", businessBenefits: "" };
  const lines = content.split("\n").map(l => l.trim()).filter(Boolean);
  let cur = null;
  for (const line of lines) {
    if (line === "Overview")        { cur = "overview";        continue; }
    if (line === "Key Process Flow") { cur = "keyProcessFlow"; continue; }
    if (line === "Business Benefits") { cur = "businessBenefits"; continue; }
    if (["Solution Capabilities","Industry Relevance","Whats New","Diagrams","Accelerators"].includes(line)) {
      cur = null; continue;
    }
    if (cur && sections[cur] !== undefined)
      sections[cur] += (sections[cur] ? " | " : "") + line;
  }
  return {
    id:              meta.processId || "",
    name:            meta.processName || "",
    module:          getModule(sec),
    lob:             meta.businessProcessGroupName || "",
    version:         meta.version || "",
    overview:        sections.overview || content.substring(0, 2000),
    keyProcessFlow:  sections.keyProcessFlow
      ? sections.keyProcessFlow.split(" | ").map((s,i) => (i+1) + ". " + s.trim()).join("\n")
      : "",
    businessBenefits: sections.businessBenefits
      ? sections.businessBenefits.split(" | ").map((s,i) => (i+1) + ". " + s.trim()).join("\n")
      : "",
    source: "SAP for Me"
  };
}

function getDecisionOwner(row) {
  const mod = (row.module || "").toUpperCase();
  const q   = (row.designQuestion || "").toLowerCase();
  if (mod === "FIN" || q.includes("finance")     || q.includes("accounting"))  return "Finance Lead / CFO";
  if (mod === "SD"  || q.includes("sales")       || q.includes("customer"))    return "Sales Lead / CCO";
  if (mod === "MM"  || q.includes("procurement") || q.includes("purchase"))    return "Procurement Lead";
  if (mod === "PP"  || q.includes("production")  || q.includes("manufacturing")) return "Operations Lead";
  if (mod === "HCM" || q.includes("payroll")     || q.includes("employee"))    return "HR Lead / CHRO";
  if (q.includes("authoriz") || q.includes("role") || q.includes("access"))   return "IT Security Lead";
  if (q.includes("integrat") || q.includes("interface"))                       return "Integration Lead";
  if (q.includes("migration") || q.includes("data load"))                      return "Data Migration Lead";
  return "Business Process Owner";
}

function getSAPActivatePhase(row) {
  const q = (row.designQuestion || "").toLowerCase();
  if (q.includes("org structure") || q.includes("chart of accounts") || q.includes("company code")) return "Explore";
  if (q.includes("master data")   || q.includes("number range")     || q.includes("output") || q.includes("form")) return "Explore";
  if (q.includes("configur")      || q.includes("setting")          || q.includes("parameter"))  return "Realize";
  if (q.includes("workflow")      || q.includes("approval")         || q.includes("notification")) return "Realize";
  if (q.includes("integrat")      || q.includes("interface")        || q.includes("api"))          return "Realize";
  if (q.includes("migration")     || q.includes("data load")        || q.includes("cutover"))      return "Deploy";
  if (q.includes("authoriz")      || q.includes("role")             || q.includes("user"))         return "Deploy";
  if (row.fitGap === "Gap") return "Realize";
  return "Explore";
}

function getRAGStatus(row) {
  if (row.fitGap === "Gap"          && row.complexity === "High")   return "Red";
  if (row.fitGap === "Gap"          && row.complexity === "Medium") return "Amber";
  if (row.fitGap === "Partial Fit"  && row.complexity === "High")   return "Amber";
  if (row.fitGap === "Gap"          && row.complexity === "Low")    return "Amber";
  return "Green";
}

function getIntegrationFlags(row) {
  const text = (
    (row.designQuestion || "") + " " +
    (row.impactActions  || "") + " " +
    (row.rationale      || "")
  ).toLowerCase();
  const flags = [];
  if (text.includes("ariba")          || text.includes("sourcing"))         flags.push("SAP Ariba");
  if (text.includes("successfactor")  || text.includes("payroll"))          flags.push("SuccessFactors");
  if (text.includes("concur"))                                               flags.push("SAP Concur");
  if (text.includes("fieldglass"))                                           flags.push("SAP Fieldglass");
  if (text.includes("btp")            || text.includes("business technology")) flags.push("SAP BTP");
  if (text.includes("analytics")      || text.includes("sac") || text.includes("datasphere")) flags.push("SAP Analytics Cloud");
  if (text.includes("third party")    || text.includes("3rd party") || text.includes("external system")) flags.push("3rd Party");
  const mods = [];
  if ((row.module === "SD" || row.module === "MM") && text.includes("financ")) mods.push("SD-FIN");
  if (row.module === "MM" && (text.includes("product") || text.includes("mrp"))) mods.push("MM-PP");
  if (row.module === "SD" && (text.includes("warehouse") || text.includes("delivery"))) mods.push("SD-WM");
  return [...flags, ...mods].join(", ");
}

function getConfigEffort(row) {
  if (row.fitGap === "Gap"         && row.complexity === "High")   return "High (5+ days)";
  if (row.fitGap === "Gap"         && row.complexity === "Medium") return "Medium (2-5 days)";
  if (row.fitGap === "Gap"         && row.complexity === "Low")    return "Low (1-2 days)";
  if (row.fitGap === "Partial Fit" && row.complexity === "High")   return "Medium (2-5 days)";
  if (row.fitGap === "Partial Fit")                                return "Low (1-2 days)";
  return "Minimal (<1 day)";
}

function dedup(rows) {
  const seen = new Set();
  return rows.filter(r => {
    const k = (r.designQuestion || "").toLowerCase().substring(0, 60).trim();
    if (!k || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

// Expected SAP release based on today's date
function expectedRelease() {
  const now   = new Date();
  const yy    = now.getFullYear() % 100;
  const month = now.getMonth() + 1;
  return month >= 8 ? `${yy}08` : `${yy}02`;
}

function isCatalogOutdated(catalogVersion) {
  return parseInt(catalogVersion, 10) < parseInt(expectedRelease(), 10);
}

module.exports = {
  ONPREM_TERMS,
  detectOnPrem,
  getContent,
  parseSections,
  getModule,
  parseForOverview,
  getDecisionOwner,
  getSAPActivatePhase,
  getRAGStatus,
  getIntegrationFlags,
  getConfigEffort,
  dedup,
  expectedRelease,
  isCatalogOutdated
};
