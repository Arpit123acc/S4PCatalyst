// bdcq-agent/routes.js — Express router for all /api/bdcq/* endpoints
// Mounted in server.js via:  app.use("/api/bdcq", require("./bdcq-agent/routes"))
"use strict";

const express = require("express");
const path    = require("path");
const fs      = require("fs");
const os      = require("os");
const { spawn, execSync } = require("child_process");
const { rowMatchesScope } = require("./scope-match");
const router  = express.Router();

const isWin          = process.platform === "win32";
const CLAUDE_MODEL   = process.env.CLAUDE_MODEL || "claude-haiku-4-5-20251001";

const { COUNTRY_RULES, INDUSTRY_RULES, CLOUDPE_CONSTRAINTS, normalizeCountries, normalizeIndustry } =
  require("../bdcq-enricher/localization");

// Resolve the claude executable on Windows across every common install method.
// Bare "claude" is not spawnable without a shell (Node ignores PATHEXT), so a
// tester who can run `claude` in a terminal still hits ENOENT unless we resolve
// the full path here.
// Priority: CLAUDE_PATH → known install locations → `where claude` (PATH).
function resolveClaudeExe() {
  if (!isWin) return "claude";
  if (process.env.CLAUDE_PATH && fs.existsSync(process.env.CLAUDE_PATH)) {
    return process.env.CLAUDE_PATH;
  }
  const up           = process.env.USERPROFILE || process.env.HOME || os.homedir();
  const appData      = process.env.APPDATA      || path.join(up, "AppData", "Roaming");
  const localAppData = process.env.LOCALAPPDATA || path.join(up, "AppData", "Local");
  const knownPaths = [
    path.join(up, ".local", "bin", "claude.exe"),
    path.join(localAppData, "Programs", "claude", "claude.exe"),
    path.join(appData, "npm", "claude.exe"),
    path.join(appData, "npm", "claude.cmd"),   // npm global shim — needs shell:true
    path.join(up, ".local", "bin", "claude.cmd"),
  ];
  for (const p of knownPaths) {
    if (fs.existsSync(p)) return p;
  }
  // Whatever is on PATH — `where` yields the full path with extension.
  try {
    const found = execSync("where claude", { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] })
      .split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const pick = found.find(p => p.toLowerCase().endsWith(".exe"))
              || found.find(p => p.toLowerCase().endsWith(".cmd"))
              || found[0];
    if (pick && fs.existsSync(pick)) return pick;
  } catch (_) { /* claude not on PATH */ }
  // Last resort: rely on PATH (may fail in child processes)
  return "claude";
}

// Spawn  claude --print  and resolve with stdout text
function runClaude(prompt) {
  return new Promise((resolve, reject) => {
    const claudeExe = resolveClaudeExe();
    const isCmd     = isWin && /\.(cmd|bat)$/i.test(claudeExe);
    // .cmd/.bat shims run through cmd.exe (shell:true); quote so paths with
    // spaces (e.g. C:\Users\First Last\AppData\Roaming\npm\claude.cmd) work.
    const spawnExe  = isCmd ? `"${claudeExe}"` : claudeExe;
    const proc = spawn(spawnExe, ["--model", CLAUDE_MODEL, "--print", "--dangerously-skip-permissions"], {
      stdio: ["pipe", "pipe", "pipe"],
      shell: isCmd,   // .cmd files require shell:true on Windows
      cwd: os.tmpdir(),
      env: {
        ...process.env,
        PATH: [
          path.join(os.homedir(), ".local", "bin"),
          path.join(os.homedir(), "AppData", "Local", "Programs", "claude"),
          path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "npm"),
          process.env.PATH || "",
        ].join(isWin ? ";" : ":"),
      },
    });
    let out = "", err = "";
    proc.stdout.on("data", d => { out += d; });
    proc.stderr.on("data", d => { err += d; });
    proc.on("error", e => {
      const msg = e.code === "ENOENT"
        ? "Claude CLI not found. Install it from claude.ai/code and confirm `claude --version` works in a terminal, then restart START.bat. If it's in a custom location, set the CLAUDE_PATH environment variable to the full path of claude.exe."
        : `Claude CLI error: ${e.message}`;
      reject(new Error(msg));
    });
    proc.on("close", code => {
      // Log raw output to server console for debugging
      if (!out.includes("[")) {
        console.error("[BDCQ Claude] Exit:", code, "| stdout:", out.slice(0, 400), "| stderr:", err.slice(0, 200));
      }
      if (code !== 0) return reject(new Error(err.slice(0, 300) || `Claude exited with code ${code}`));
      resolve(out);
    });
    proc.stdin.write(prompt, "utf8");
    proc.stdin.end();
  });
}

// ── Extract first balanced JSON array from Claude output ─────────────────────
// lastIndexOf("]") fails when Claude appends trailing text that contains "]".
// This walks character-by-character so it stops at the real closing bracket.
function extractJsonArray(text) {
  const s = text.indexOf("[");
  if (s === -1) return null;
  let depth = 0, inString = false, escape = false;
  for (let i = s; i < text.length; i++) {
    const ch = text[i];
    if (escape)                      { escape = false; continue; }
    if (ch === "\\" && inString)     { escape = true;  continue; }
    if (ch === '"')                  { inString = !inString; continue; }
    if (inString)                    continue;
    if (ch === "[" || ch === "{")    depth++;
    else if (ch === "]" || ch === "}") {
      depth--;
      if (depth === 0) return text.slice(s, i + 1);
    }
  }
  return null;
}

// ── LOB → BDCQ domain fuzzy mapping ─────────────────────────────────────────
// Both sides are normalised to lowercase for comparison.
const LOB_KEYWORDS = [
  { lob: ["finance", "fac finance", "controlling", "accounting"],      domain: ["finance", "controlling"] },
  { lob: ["asset management"],                                          domain: ["asset"] },
  { lob: ["manufacturing"],                                             domain: ["manufactur"] },
  { lob: ["supply chain", "logistics"],                                 domain: ["supply chain", "logistic", "warehouse", "inventory"] },
  { lob: ["sales"],                                                     domain: ["sales", "order management"] },
  { lob: ["service"],                                                   domain: ["service"] },
  { lob: ["human resources", "hr"],                                     domain: ["human resource", "hr", "payroll", "workforce"] },
  { lob: ["sourcing", "procurement"],                                   domain: ["procure", "sourcing", "purchasing"] },
  { lob: ["professional services"],                                     domain: ["professional service", "project"] },
  { lob: ["r&d", "engineering"],                                        domain: ["r&d", "engineering", "product"] },
  { lob: ["solutions for specific industries"],                         domain: ["industry", "solution"] },
];

// ── Derive scope item IDs from scope-catalog.json for a given domain ─────────
// Uses LOB_KEYWORDS to find which catalog LOBs match the domain, then returns
// the first `limit` scope item IDs found — same data source as KDD generation.
function getDomainScopeRefs(domain, limit = 8) {
  const catalogPath = path.join(ROOT, "scope-catalog.json");
  if (!fs.existsSync(catalogPath)) return "";
  try {
    const catalog   = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
    const processes = catalog.processes || [];
    const domNorm   = domain.toLowerCase().trim();

    // Find LOB keywords that match this domain name
    const matchingLobKws = [];
    for (const rule of LOB_KEYWORDS) {
      if (rule.domain.some(kw => domNorm.includes(kw) || kw.includes(domNorm.split(/\s+/)[0]))) {
        matchingLobKws.push(...rule.lob);
      }
    }
    if (!matchingLobKws.length) return "";

    // Collect IDs from matching LOB processes
    const ids = [];
    for (const proc of processes) {
      if (!proc.id) continue;
      // Same widening as /scope-lookup, in the opposite direction: an industry
      // scope item is findable by its business area or not at all.
      const lobNorm = ((proc.lob || "") + " " + (proc.businessArea || "")).toLowerCase();
      if (matchingLobKws.some(kw => lobNorm.includes(kw))) {
        if (!ids.includes(proc.id)) ids.push(proc.id);
        if (ids.length >= limit) break;
      }
    }
    return ids.join(", ");
  } catch { return ""; }
}

function lobsToDomains(lobs, availableDomains) {
  const result = new Set();
  const normLobs    = lobs.map(l => l.toLowerCase());
  const normDomains = availableDomains.map(d => ({ orig: d, norm: d.toLowerCase() }));

  for (const lobNorm of normLobs) {
    for (const rule of LOB_KEYWORDS) {
      const lobMatch = rule.lob.some(kw => lobNorm.includes(kw));
      if (!lobMatch) continue;
      for (const domEntry of normDomains) {
        if (rule.domain.some(kw => domEntry.norm.includes(kw))) {
          result.add(domEntry.orig);
        }
      }
    }
    // Fallback: direct substring match between lob and domain
    for (const domEntry of normDomains) {
      if (domEntry.norm.includes(lobNorm) || lobNorm.includes(domEntry.norm)) {
        result.add(domEntry.orig);
      }
    }
  }
  return [...result];
}

// ROOT is two levels up from this file (bdcq-agent/ → explore-accelerator/)
const ROOT = path.join(__dirname, "..");

// ── Grounding library — user-uploaded Excel files, one JSON per file ──────────
// Each uploaded Excel → bdcq/grounding/<slug>.json
// readGrounding() merges all files in the folder at call time.
const GROUNDING_DIR = path.join(ROOT, "bdcq", "grounding");

function groundingSlug(filename) {
  return filename.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 60);
}

function readGrounding() {
  if (!fs.existsSync(GROUNDING_DIR)) return null;
  const files = fs.readdirSync(GROUNDING_DIR).filter(f => f.endsWith(".json"));
  if (!files.length) return null;
  const allSheets = [];
  for (const f of files) {
    try {
      const g = JSON.parse(fs.readFileSync(path.join(GROUNDING_DIR, f), "utf8"));
      if (Array.isArray(g.sheets)) allSheets.push(...g.sheets);
    } catch { /* skip corrupt file */ }
  }
  return allSheets.length ? { sheets: allSheets } : null;
}

function listGroundingFiles() {
  if (!fs.existsSync(GROUNDING_DIR)) return [];
  return fs.readdirSync(GROUNDING_DIR).filter(f => f.endsWith(".json")).map(f => {
    try {
      const g    = JSON.parse(fs.readFileSync(path.join(GROUNDING_DIR, f), "utf8"));
      return {
        slug:       f.replace(".json", ""),
        filename:   f,
        origName:   g.origName   || f,
        uploadedAt: g.uploadedAt || null,
        totalRows:  g.totalRows  || 0,
        sheets:     (g.sheets || []).map(s => ({ sheet: s.sheet, columns: s.columns, rowCount: s.rows.length }))
      };
    } catch { return null; }
  }).filter(Boolean);
}

// ── SAP text typo fixer (applied to all Claude-generated text) ───────────────
function fixSapTypos(text) {
  return String(text || "")
    // Fix "public" misspellings first (standalone word)
    .replace(/\bpublc\b/gi,  "public")
    .replace(/\bpubilc\b/gi, "public")
    .replace(/\bpubic\b/gi,  "public")
    // Normalise "Cloud, public edition" / "Cloud, publc edition" → "Cloud Public Edition"
    // Also catches lower-case variants like "cloud, public edition"
    .replace(/S\/4HANA Cloud,?\s+publc\s+edition/gi,  "S/4HANA Cloud Public Edition")
    .replace(/S\/4HANA Cloud,?\s+pubic\s+edition/gi,  "S/4HANA Cloud Public Edition")
    .replace(/S\/4HANA Cloud,?\s+public\s+edition/gi, "S/4HANA Cloud Public Edition")
    // Catch plain "public edition" (no S/4HANA prefix) with comma
    .replace(/cloud,\s+publc\s+edition/gi,  "Cloud Public Edition")
    .replace(/cloud,\s+public\s+edition/gi, "Cloud Public Edition");
}

// ── AllCountries BDCQ — Country / Region lookup ───────────────────────────────
// Reads the "BDCQ Questionnaire" sheet from the AllCountries grounding file.
// Returns an array of rows, or null if the file is not present.
const ALL_COUNTRIES_SLUG = "BDCQ_S4HANA_PublicCloud_v4_AllCountries";
let _allCountriesCache = null;

function loadAllCountriesRows() {
  if (_allCountriesCache) return _allCountriesCache;
  const fp = path.join(GROUNDING_DIR, `${ALL_COUNTRIES_SLUG}.json`);
  if (!fs.existsSync(fp)) return null;
  try {
    const data  = JSON.parse(fs.readFileSync(fp, "utf8"));
    const sheet = data.sheets?.find(s => s.sheet === "BDCQ Questionnaire");
    _allCountriesCache = sheet?.rows || null;
    return _allCountriesCache;
  } catch { return null; }
}

// Domain aliases: maps common BDCQ domain names → AllCountries BPA equivalents
const BPA_ALIASES = {
  "human resources":    "hr / payroll",
  "hr":                 "hr / payroll",
  "payroll":            "hr / payroll",
  "asset management":   "procurement",    // closest BPA in AllCountries
  "project management": "cross-module",
  "professional services": "cross-module",
  "service":            "sales",          // closest BPA
  "retail":             "sales",
  "treasury":           "finance",
  "sourcing":           "procurement",
  "sourcing & procurement": "procurement",
  "quality management": "manufacturing",
  "r&d engineering":    "manufacturing",
};

// ISO-2 → display name used to build "Local – <Country>" strings
const _ISO_DISPLAY = {
  IN:"India", DE:"Germany", US:"USA", GB:"UK", BR:"Brazil",
  AE:"UAE", SA:"Saudi Arabia", FR:"France", AU:"Australia", SG:"Singapore",
  JP:"Japan", CN:"China", KR:"South Korea", VN:"Vietnam", NL:"Netherlands",
  PL:"Poland", IT:"Italy", ES:"Spain", AT:"Austria", SE:"Sweden",
  MX:"Mexico", CA:"Canada", ZA:"South Africa", AR:"Argentina",
  DK:"Denmark", FI:"Finland", NO:"Norway", IE:"Ireland", BE:"Belgium",
  PT:"Portugal", CH:"Switzerland", IL:"Israel", EG:"Egypt", KW:"Kuwait",
  QA:"Qatar", OM:"Oman", MY:"Malaysia", ID:"Indonesia", PH:"Philippines",
  TW:"Taiwan", HK:"Hong Kong", NZ:"New Zealand", KZ:"Kazakhstan", LU:"Luxembourg",
};

// For a given Business Process Area (domain) + optional Topic keyword,
// returns a formatted "Country / Region" string sourced from AllCountries.
// selectedCountryCodes: ISO-2 codes the user ticked (e.g. ["IN","DE","US"]).
// If empty, all countries are included.
function lookupCountryRegion(domain, topicKeyword, selectedCountryCodes) {
  const rows = loadAllCountriesRows();
  if (!rows) return "";

  const domRaw   = (domain || "").toLowerCase();
  const domAlias = BPA_ALIASES[domRaw] || domRaw;
  const topicLow = (topicKeyword || "").toLowerCase();

  // Match rows by Business Process Area — check both alias and original
  const bpaRows = rows.filter(row => {
    const bpa = (row["Business Process Area"] || "").toLowerCase();
    if (!bpa) return false;
    if (bpa === domAlias) return true;
    // Keyword overlap (3+ char words)
    return bpa.split(/\W+/).some(w => w.length > 3 && (domAlias.includes(w) || domRaw.includes(w))) ||
           domAlias.split(/\W+/).some(w => w.length > 3 && bpa.includes(w)) ||
           domRaw.split(/\W+/).some(w => w.length > 3 && bpa.includes(w));
  });

  // If a topic keyword is given, try to narrow further; fall back to all BPA rows
  let matched = bpaRows;
  if (topicLow) {
    const topicNarrow = bpaRows.filter(row => {
      const t = ((row["Topic"] || "") + " " + (row["Question"] || "")).toLowerCase();
      return topicLow.split(/\W+/).some(w => w.length > 3 && t.includes(w));
    });
    if (topicNarrow.length) matched = topicNarrow;
  }

  // Collect unique country → CLT pairs from matching rows (exclude Global rows)
  const countryMap = new Map(); // displayCountry → clt label
  for (const row of matched) {
    const cr  = (row["Country / Region"] || "").trim();
    const clt = (row["Localization Category\n(CLT / Non-CLT / N/A)"] || "").trim();
    if (!cr || cr === "Global") continue;
    // Strip "Local – " prefix
    const countryPart = cr.replace(/^Local\s*[–\-]\s*/i, "").trim();
    // Could be "All Countries", "India", "IN, DE, JP", "USA, India, Germany, UK"
    const parts = countryPart.split(/[,;]/).map(p => p.trim()).filter(Boolean);
    for (const c of parts) {
      if (!countryMap.has(c)) countryMap.set(c, clt);
    }
  }

  if (!countryMap.size) return ""; // no local requirements for this BPA — don't surface "Global"

  // If user selected specific countries, filter to those
  let entries = [...countryMap.entries()];
  if (selectedCountryCodes && selectedCountryCodes.length) {
    const ISO_TO_NAME = {
      IN:"India", DE:"Germany", US:"USA", GB:"UK", BR:"Brazil",
      AE:"UAE", SA:"Saudi Arabia", FR:"France", AU:"Australia", SG:"Singapore",
      JP:"Japan", CN:"China", KR:"South Korea", VN:"Vietnam", NL:"Netherlands",
      PL:"Poland", IT:"Italy", ES:"Spain", AT:"Austria", SE:"Sweden",
      MX:"Mexico", CA:"Canada", ZA:"South Africa", AR:"Argentina",
    };
    const selNames  = selectedCountryCodes.map(c => (ISO_TO_NAME[c] || c).toLowerCase());
    const selCodes  = selectedCountryCodes.map(c => c.toLowerCase());
    entries = entries.filter(([c]) => {
      const cLow = c.toLowerCase();
      return selNames.some(n => cLow.includes(n) || n.includes(cLow)) ||
             selCodes.some(code => cLow === code || cLow.startsWith(code));
    });
    if (!entries.length) return ""; // None of the selected countries have local requirements for this BPA
  }

  return entries
    .map(([c, clt]) => (clt && clt !== "N/A") ? `${c} (${clt})` : c)
    .join(", ");
}

// ── Functional-team verified overrides (keyed by SAP ID) ─────────────────────
const OVERRIDES_FILE = path.join(__dirname, "standards-overrides.json");
function loadOverrides() {
  try { return JSON.parse(fs.readFileSync(OVERRIDES_FILE, "utf8")); } catch { return {}; }
}

// ── Locate bdcq-questions.json ────────────────────────────────────────────────
// Checked in priority order; first match wins.
const SEARCH_PATHS = [
  path.join(ROOT, "bdcq", "bdcq-questions.json"),
  path.join(ROOT, "output", "bdcq", "bdcq-questions.json"),
  path.join(ROOT, "bdcq-questions.json")
];

function findBDCQFile() {
  for (const p of SEARCH_PATHS) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function readBDCQData() {
  const p = findBDCQFile();
  if (!p) return null;
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

// Determine if a sheet name is a 2-letter country/locale code
const isCountrySheet = name => /^[A-Za-z]{2}$/.test(name);

// ── GET /api/bdcq/status ──────────────────────────────────────────────────────
// Returns metadata from bdcq-questions.json without exposing row data.
router.get("/status", (req, res) => {
  try {
    const data = readBDCQData();
    if (!data) {
      return res.json({ found: false });
    }

    const globalColumnSet = new Set();
    const countrySet      = new Set();
    let   totalQuestions  = 0;

    // Per-domain metadata: name + its own column set + question count
    const domainMeta = (data.domains || []).map(domain => {
      const domColSet = new Set();
      let   domQCount = 0;
      for (const sheet of (domain.sheets || [])) {
        const sheetName = sheet.sheet || "";
        (sheet.headers || []).forEach(h => {
          if (h) { domColSet.add(h); globalColumnSet.add(h); }
        });
        if (isCountrySheet(sheetName)) countrySet.add(sheetName.toUpperCase());
        const rowCount = (sheet.rows || []).length;
        domQCount      += rowCount;
        totalQuestions += rowCount;
      }
      return {
        name:      domain.domain || "",
        filename:  domain.filename || "",
        columns:   [...domColSet],
        questions: domQCount
      };
    }).filter(d => d.name);

    res.json({
      found:          true,
      totalDomains:   data.totalDomains || domainMeta.length,
      totalQuestions,
      modules:        domainMeta.map(d => d.name),
      columns:        [...globalColumnSet],   // kept for backward compat
      domainMeta,                             // per-domain: name, columns, questions
      countries:      [...countrySet].sort(),
      generatedAt:    data.generatedAt || null
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── POST /api/bdcq/upload ─────────────────────────────────────────────────────
// Accepts { content: base64 } body, validates and saves to bdcq/bdcq-questions.json.
router.post("/upload", express.json({ limit: "50mb" }), (req, res) => {
  const { content } = req.body || {};
  if (!content) return res.status(400).json({ ok: false, error: "No content provided" });

  try {
    const buf  = Buffer.from(content, "base64");
    const text = buf.toString("utf8");
    const data = JSON.parse(text);

    if (!data || !Array.isArray(data.domains)) {
      return res.status(400).json({ ok: false, error: "Invalid file: missing domains array" });
    }

    const dir  = path.join(ROOT, "bdcq");
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const dest = path.join(dir, "bdcq-questions.json");
    fs.writeFileSync(dest, text, "utf8");

    res.json({ ok: true, totalDomains: (data.domains || []).length, savedTo: dest });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── POST /api/bdcq/upload-grounding ──────────────────────────────────────────
// Parses each uploaded Excel and saves it as bdcq/grounding/<slug>.json.
// Body: { files: [{ filename, content: base64 }] }
router.post("/upload-grounding", express.json({ limit: "50mb" }), (req, res) => {
  const { files = [] } = req.body || {};
  if (!files.length) return res.status(400).json({ ok: false, error: "No files provided" });

  let XLSX;
  try { XLSX = require(path.join(ROOT, "kdd-generator", "node_modules", "xlsx")); }
  catch { return res.status(500).json({ ok: false, error: "xlsx module not found — run npm install in kdd-generator/" }); }

  if (!fs.existsSync(GROUNDING_DIR)) fs.mkdirSync(GROUNDING_DIR, { recursive: true });

  const saved = [];
  for (const { filename, content } of files) {
    if (!content || !filename) continue;
    const sheets = [];
    try {
      const buf = Buffer.from(content, "base64");
      const wb  = XLSX.read(buf, { type: "buffer", cellText: true, cellDates: true });
      for (const sheetName of wb.SheetNames) {
        const ws  = wb.Sheets[sheetName];
        const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });
        if (aoa.length < 2) continue;

        // Smart header detection: find the row (within first 8) with the most
        // non-empty cells — handles merged-title first rows.
        let hdrIdx = 0, bestScore = 0;
        for (let i = 0; i < Math.min(8, aoa.length); i++) {
          const nonEmpty = aoa[i].filter(c => String(c ?? "").trim().length > 0);
          const avgLen   = nonEmpty.length
            ? nonEmpty.reduce((s, c) => s + String(c).length, 0) / nonEmpty.length : 999;
          const score = nonEmpty.length > 1 ? nonEmpty.length - (avgLen > 80 ? 5 : 0) : 0;
          if (score > bestScore) { bestScore = score; hdrIdx = i; }
        }

        // Build unique header names (__colN for blanks)
        const rawHdrs = aoa[hdrIdx].map((h, i) => String(h ?? "").trim() || `__col${i}`);
        const headers = rawHdrs.filter(h => !h.startsWith("__col"));
        if (!headers.length) continue;

        const rows = aoa.slice(hdrIdx + 1)
          .filter(row => row.some(cell => String(cell ?? "").trim()))
          .map(row => {
            const obj = {};
            rawHdrs.forEach((h, i) => {
              if (h.startsWith("__col")) return;
              const v = String(row[i] ?? "").trim();
              if (v) obj[h] = v;
            });
            return obj;
          })
          .filter(obj => Object.keys(obj).length > 0);
        if (!rows.length) continue;
        sheets.push({ source: filename, sheet: sheetName, columns: headers, rows });
      }
    } catch (e) {
      return res.status(400).json({ ok: false, error: `Failed to parse ${filename}: ${e.message}` });
    }

    if (!sheets.length) continue;

    const slug     = groundingSlug(filename);
    const payload  = {
      origName:   filename,
      uploadedAt: new Date().toISOString(),
      totalRows:  sheets.reduce((s, sh) => s + sh.rows.length, 0),
      sheets
    };
    fs.writeFileSync(path.join(GROUNDING_DIR, `${slug}.json`), JSON.stringify(payload, null, 2), "utf8");
    // Bust AllCountries cache if this file was just replaced
    if (slug === ALL_COUNTRIES_SLUG) _allCountriesCache = null;
    saved.push({
      slug,
      origName:  filename,
      totalRows: payload.totalRows,
      sheets:    sheets.map(s => ({ sheet: s.sheet, columns: s.columns, rowCount: s.rows.length }))
    });
  }

  if (!saved.length) return res.status(400).json({ ok: false, error: "No data rows found in uploaded file(s)" });

  res.json({ ok: true, saved });
});

// ── GET /api/bdcq/grounding ───────────────────────────────────────────────────
// Returns per-file metadata (no row data — keep response small).
router.get("/grounding", (req, res) => {
  const files = listGroundingFiles();
  res.json({
    ok:        true,
    exists:    files.length > 0,
    files,
    totalRows: files.reduce((s, f) => s + f.totalRows, 0)
  });
});

// ── DELETE /api/bdcq/grounding/:slug ─────────────────────────────────────────
// Delete one grounding file by its slug.
router.delete("/grounding/:slug", (req, res) => {
  const safe = path.basename(req.params.slug).replace(/[^a-zA-Z0-9_-]/g, "") + ".json";
  const file = path.join(GROUNDING_DIR, safe);
  if (!fs.existsSync(file)) return res.status(404).json({ ok: false, error: "File not found" });
  try {
    fs.unlinkSync(file);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── DELETE /api/bdcq/grounding ────────────────────────────────────────────────
// Delete all grounding files.
router.delete("/grounding", (req, res) => {
  try {
    if (fs.existsSync(GROUNDING_DIR)) {
      fs.readdirSync(GROUNDING_DIR).filter(f => f.endsWith(".json"))
        .forEach(f => fs.unlinkSync(path.join(GROUNDING_DIR, f)));
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── POST /api/bdcq/generate ───────────────────────────────────────────────────
// Runs the builder with user-supplied config, returns filename + rowCount.
router.post("/generate", express.json({ limit: "512kb" }), (req, res) => {
  const {
    filename,
    scopeIds        = [],
    selectedDomains = [],
    selectedColumns = [],
    countries       = [],
    clientName      = "",
    projectName     = "",
    overviewText    = "",
    clientLogo      = null,   // base64 data URL
    partnerLogo     = null    // base64 data URL
  } = req.body || {};

  if (!filename) {
    return res.status(400).json({ ok: false, error: "filename is required" });
  }

  try {
    const data = readBDCQData();
    if (!data) {
      return res.status(404).json({
        ok: false,
        error: "bdcq-questions.json not found. Upload the file first."
      });
    }

    const { buildBDCQExcel } = require("./bdcq-builder");
    const outDir = path.join(ROOT, "output");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

    const result = buildBDCQExcel({
      bdcqData:        data,
      scopeIds:        Array.isArray(scopeIds)        ? scopeIds        : [],
      selectedDomains: Array.isArray(selectedDomains) ? selectedDomains : [],
      selectedColumns: Array.isArray(selectedColumns) ? selectedColumns : [],
      countries:       Array.isArray(countries)       ? countries       : [],
      clientName,
      projectName,
      overviewText,
      clientLogo,
      partnerLogo,
      filename,
      outputDir: outDir
    });

    res.json({ ok: true, filename: result.filename, rowCount: result.rowCount, logoFiles: result.logoFiles || [] });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── GET /api/bdcq/scope-lookup?ids=BD6,J59 ───────────────────────────────────
// Maps scope item IDs (from DDA) to BDCQ domain names via LOB field.
router.get("/scope-lookup", (req, res) => {
  const ids = (req.query.ids || "").split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
  if (!ids.length) return res.json({ domains: [], matchedIds: [], matchedLobs: [] });

  const catalogPath = path.join(ROOT, "scope-catalog.json");
  if (!fs.existsSync(catalogPath)) {
    return res.json({ domains: [], error: "scope-catalog.json not found" });
  }

  const catalog  = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  const processes = catalog.processes || [];

  const lobSet     = new Set();
  const matchedIds = [];
  for (const item of processes) {
    if (ids.includes((item.id || "").toUpperCase())) {
      matchedIds.push(item.id);
      if (item.lob) lobSet.add(item.lob);
      // BUSINESS AREA TOO, not just LOB. An industry scope item carries the
      // generic LOB "Solutions for Specific Industries", which names no
      // questionnaire, while its business area names one exactly --
      // J11 Customer Project Management is lob="Solutions for Specific
      // Industries", businessArea="Professional Services", and Professional
      // Services is a BDCQ domain with 12 questions.
      //
      // Measured over the 679-item catalogue: 571 resolve from LOB alone, 55
      // resolve ONLY from business area, and 53 genuinely have no
      // questionnaire. Those 55 returned "No matching BDCQ domains found",
      // which reads as "SAP published none" rather than "we did not look".
      if (item.businessArea) lobSet.add(item.businessArea);
    }
  }

  const bdcqData        = readBDCQData();
  const availableDomains = bdcqData ? (bdcqData.domains || []).map(d => d.domain || "").filter(Boolean) : [];
  const domains         = lobsToDomains([...lobSet], availableDomains);

  res.json({ domains, matchedIds, matchedLobs: [...lobSet] });
});

// ── POST /api/bdcq/preview ────────────────────────────────────────────────────
// Same filtering as generate, but returns rows as JSON for the in-browser editor.
router.post("/preview", express.json({ limit: "512kb" }), (req, res) => {
  const {
    scopeIds        = [],
    selectedDomains = [],
    selectedColumns = [],
    countries       = []
  } = req.body || {};

  try {
    const data = readBDCQData();
    if (!data) return res.status(404).json({ ok: false, error: "bdcq-questions.json not found. Upload the file first." });

    const scopeSet   = new Set((scopeIds        || []).map(s => s.toUpperCase().trim()).filter(Boolean));
    const countrySet = new Set((countries       || []).map(c => c.toUpperCase().trim()).filter(Boolean));
    const domainSet  = new Set((selectedDomains || []).map(d => d.toLowerCase().trim()).filter(Boolean));
    // 2-letter ISO codes = country-specific sheet; "LC" = "Local – All Countries" injected rows
    const isCountrySheet = name => /^[A-Za-z]{2}$/.test(name) || name === "LC";

    const allRows    = [];
    const allHeaders = [];
    const headerSet  = new Set();

    for (const domain of data.domains) {
      const domainName = domain.domain || "";
      if (domainSet.size > 0 && !domainSet.has(domainName.toLowerCase().trim())) continue;

      for (const sheet of (domain.sheets || [])) {
        const sheetName = sheet.sheet || "";
        // Skip metadata sheets that are never questions
        if (/template|change history|overview|instructions|cover|readme/i.test(sheetName)) continue;
        if (isCountrySheet(sheetName)) {
          if (countrySet.size === 0 || !countrySet.has(sheetName.toUpperCase())) continue;
        }
        (sheet.headers || []).forEach(h => {
          if (h && !headerSet.has(h)) { headerSet.add(h); allHeaders.push(h); }
        });
        for (const row of (sheet.rows || [])) {
          // A Scope Ref cell holds a LIST ("BD9, 2EQ, I9I"), so comparing the
          // whole cell against one id skipped every multi-item row. Shared
          // with bdcq-builder.js: the two copies of this test were identical
          // and would have drifted the moment either was edited.
          if (!rowMatchesScope(row, scopeSet)) continue;
          allRows.push({
            Module: domainName,
            Section: sheetName,
            _localizationCountry: isCountrySheet(sheetName) ? sheetName.toUpperCase() : "Global",
            ...row
          });
        }
      }
    }

    // ── Inject country-local questions from AllCountries grounding ────────────
    // When countries are selected, pull matching "Local – <country>" rows from
    // BDCQ_S4HANA_PublicCloud_v4_AllCountries.json and add them to the editor.
    if (countrySet.size > 0) {
      const acRows = loadAllCountriesRows();
      if (acRows) {
        // Map full country names and ISO codes → ISO-2 code for matching
        const COUNTRY_NORM = {
          "india":"IN","germany":"DE","usa":"US","uk":"GB","brazil":"BR",
          "france":"FR","australia":"AU","singapore":"SG","japan":"JP",
          "china":"CN","south korea":"KR","vietnam":"VN","netherlands":"NL",
          "poland":"PL","italy":"IT","spain":"ES","austria":"AT","sweden":"SE",
          "mexico":"MX","canada":"CA","south africa":"ZA","argentina":"AR",
          "uae":"AE","saudi arabia":"SA","denmark":"DK","finland":"FI",
          "norway":"NO","ireland":"IE","belgium":"BE","portugal":"PT",
          "switzerland":"CH","israel":"IL","egypt":"EG","kuwait":"KW",
          "qatar":"QA","oman":"OM","malaysia":"MY","indonesia":"ID",
          "philippines":"PH","taiwan (china)":"TW","hong kong (china)":"HK",
          "new zealand":"NZ","kazakhstan":"KZ","luxembourg":"LU",
          "in":"IN","de":"DE","us":"US","gb":"GB","br":"BR","fr":"FR",
          "au":"AU","sg":"SG","jp":"JP","cn":"CN","kr":"KR","vn":"VN",
          "ae":"AE","sa":"SA","lu":"LU",
        };

        // Deduplicate by question text + section (avoid double-adding "All Countries" per country)
        const seenQuestions = new Set();

        for (const row of acRows) {
          const cr        = (row["Country / Region"] || "").trim();
          const question  = (row["Question"] || "").trim();
          if (!cr || cr === "Global" || !question) continue;

          const countryPart    = cr.replace(/^Local\s*[–\-]\s*/i, "").trim();
          const isAllCountries = countryPart.toLowerCase() === "all countries";

          // Determine which section code to use and whether this row is relevant
          let sectionCode = null;

          if (isAllCountries) {
            // These apply to ALL selected countries — inject once, section = "LC"
            const dedupKey = `LC::${question}`;
            if (seenQuestions.has(dedupKey)) continue;
            seenQuestions.add(dedupKey);
            sectionCode = "LC"; // "Local – All Countries"
          } else {
            // Parse multi-country values like "IN, DE, JP, CN, BR, KR"
            const parts = countryPart.split(/[,;]/).map(p => p.trim().toLowerCase());
            for (const part of parts) {
              const iso = COUNTRY_NORM[part] || part.toUpperCase().slice(0, 2);
              if (countrySet.has(iso)) {
                const dedupKey = `${iso}::${question}`;
                if (seenQuestions.has(dedupKey)) { sectionCode = null; break; }
                seenQuestions.add(dedupKey);
                sectionCode = iso;
                break;
              }
            }
          }

          if (!sectionCode) continue;

          // Check if this row's BPA matches one of the selected domains
          const bpa    = (row["Business Process Area"] || "").toLowerCase();
          const cltTag = (row["Localization Category\n(CLT / Non-CLT / N/A)"] || "").trim();

          let matchesDomain = domainSet.size === 0;
          if (!matchesDomain) {
            for (const d of domainSet) {
              const alias = BPA_ALIASES[d] || d;
              if (bpa === alias ||
                  bpa.split(/\W+/).some(w => w.length > 2 && (alias.includes(w) || d.includes(w))) ||
                  alias.split(/\W+/).some(w => w.length > 2 && bpa.includes(w)) ||
                  d.split(/\W+/).some(w => w.length > 2 && bpa.includes(w))) {
                matchesDomain = true; break;
              }
            }
          }
          if (!matchesDomain) continue;

          const moduleName = row["Business Process Area"] || "General";
          allRows.push({
            Module:               moduleName,
            Section:              sectionCode,   // 2-char code → locale badge in UI
            _localizationCountry: sectionCode,
            _fromAllCountries:    true,
            Question:             question,
            Topic:                row["Topic"] || "",
            "Topic Definition":   row["Topic Definition"] || "",
            "SAP Workstream":     row["SAP Workstream"] || "",
            "Localization Type":  cltTag,
            "Sample Value":       row["Sample Value"] || "",
            "Country / Region":   sectionCode === "LC"
              ? "Local – All Countries"
              : `Local – ${_ISO_DISPLAY[sectionCode] || sectionCode}`,
          });

          // Also ensure these columns appear in allHeaders
          ["Question","Topic","Topic Definition","SAP Workstream","Localization Type","Sample Value"]
            .forEach(h => { if (!headerSet.has(h)) { headerSet.add(h); allHeaders.push(h); } });
        }

        console.log(`[BDCQ preview] injected ${allRows.filter(r => r._fromAllCountries).length} AllCountries local questions for ${[...countrySet].join(",")}`);
      }
    }

    // Deduplicate rows with identical question text — append Topic to disambiguate
    const _seenQ = new Map();
    for (const row of allRows) {
      const qText = String(row.Question || row["Business Driven Configuration Questionnaire"] || "").trim();
      const normQ = qText.toLowerCase();
      if (!normQ) continue;
      if (_seenQ.has(normQ)) {
        const topic = String(row.Topic || row["Process Area"] || "").trim();
        if (topic) {
          if (row.Question && row.Question.trim() === qText) row.Question = `${qText} [${topic}]`;
          const bdcqKey = "Business Driven Configuration Questionnaire";
          if (row[bdcqKey] && row[bdcqKey].trim() === qText) row[bdcqKey] = `${qText} [${topic}]`;
        }
      } else {
        _seenQ.set(normQ, row);
      }
    }

    // Determine output columns (same logic as builder)
    const baseColumns = ["Module", "Section"];
    let dataColumns;
    if (selectedColumns && selectedColumns.length > 0) {
      // Match known headers case-insensitively; keep custom column names as-is
      // (custom cols won't be in allHeaders — they appear empty in Excel, ready for manual fill-in)
      dataColumns = selectedColumns
        .map(c => allHeaders.find(h => h.toLowerCase() === c.toLowerCase()) || c)
        .filter(Boolean);
    } else {
      dataColumns = allHeaders;
    }
    const outputColumns = [...baseColumns, ...dataColumns];

    // Detect the "question" column: prefer a col named "question",
    // fallback to the data column with the most cumulative content length.
    const dataOnlyCols = dataColumns.filter(Boolean);
    const colContentScore = {};
    for (const row of allRows.slice(0, 30)) {
      for (const col of dataOnlyCols) {
        const len = String(row[col] || "").trim().length;
        colContentScore[col] = (colContentScore[col] || 0) + len;
      }
    }
    const questionCol =
      dataOnlyCols.find(c => /question/i.test(c)) ||
      Object.entries(colContentScore).sort((a, b) => b[1] - a[1])[0]?.[0] ||
      dataOnlyCols[0] || "Question";

    res.json({ ok: true, rows: allRows, columns: outputColumns, questionCol, rowCount: allRows.length });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── POST /api/bdcq/enrich ─────────────────────────────────────────────────────
// Claude enriches a batch of rows with SAP Standard Value + Watch Out columns.
// Body: { domain, questionCol, rows, country (array|string), industry (string) }
router.post("/enrich", express.json({ limit: "2mb" }), async (req, res) => {
  const { domain = "", questionCol = "Question", rows: rawRows = [],
          country: rawCountry, industry: rawIndustry } = req.body || {};
  if (!rawRows.length) return res.json({ ok: true, enrichments: [] });

  // ── Column name aliases — SAP BDCQ files use slightly different names ────────
  function getCol(row, ...candidates) {
    for (const c of candidates) {
      const key = Object.keys(row).find(k => k.toLowerCase().includes(c.toLowerCase()));
      if (key && String(row[key]).trim()) return String(row[key]).trim();
    }
    return "";
  }

  // ── Filter out rows with no real question text ────────────────────────────────
  // Only skip rows that are truly blank or are a literal copy of the domain name.
  // Do NOT filter by regex patterns — SAP BDCQ questions can be short 2-word phrases
  // like "Payment Terms", "Fiscal Year", "Cost Object" which are valid config decisions.
  const domainNameLower = (domain || "").trim().toLowerCase();
  const isInvalidRow = (r) => {
    const q = String(r[questionCol] || r["Question"] || "").trim();
    if (!q || q.length < 3)               return true;  // truly blank
    if (q.toLowerCase() === domainNameLower) return true;  // row is just the domain name
    return false;
  };

  const rows = rawRows.filter(r => !isInvalidRow(r));

  // Track original indices so enrichments map back to rawRows correctly
  const validIndices = rawRows.map(r => !isInvalidRow(r));

  if (!rows.length) {
    console.warn(`[BDCQ enrich] All ${rawRows.length} rows for "${domain}" were filtered as invalid questions.`);
    return res.json({ ok: true, enrichments: rawRows.map(() => ({ standardValue: "", watchOut: "" })) });
  }

  // Build per-question reference block — includes SSCUI ref + SAP ID so Claude
  // can recall the exact pre-delivered configuration values from its training knowledge.
  const questionLines = rows.map((r, i) => {
    const question   = String(r[questionCol] || r["Question"] || "").trim().slice(0, 300);
    const topicDef   = getCol(r, "topic definition", "definition", "description").slice(0, 400);
    const stdVal     = getCol(r, "solution", "sample value", "standard value", "standard values").slice(0, 600);
    const watchOut   = getCol(r, "watch out", "constraint", "watch-out").slice(0, 500);
    const sscui      = getCol(r, "sscui reference", "sscui", "configuration activity").slice(0, 150);
    const sapId      = getCol(r, "sap id", "sapid", "id").slice(0, 20);
    const topic      = getCol(r, "topic").slice(0, 100);

    let block = `${i + 1}. Question: ${question}`;
    if (topic)    block += `\n   Topic: ${topic}`;
    if (sscui)    block += `\n   SSCUI: ${sscui}`;
    if (sapId)    block += `\n   SAP ID: ${sapId}`;
    if (topicDef) block += `\n   Context: ${topicDef}`;
    if (stdVal)   block += `\n   SAP Standard Value (from BDCQ): ${stdVal}`;
    if (watchOut) block += `\n   SAP Watch-Out (from BDCQ): ${watchOut}`;
    return block;
  }).join("\n\n");

  const hasRefData = rows.some(r =>
    getCol(r, "solution", "sample value", "standard value") || getCol(r, "watch out", "constraint")
  );

  // ── Build country localisation context ────────────────────────────────────
  const countryCodes = Array.isArray(rawCountry)
    ? rawCountry.flatMap(c => normalizeCountries(c))
    : normalizeCountries(rawCountry || "");

  // Collect all text from rows to match against processAreaKeywords
  const rowsText = rows.map(r =>
    [getCol(r, "process area"), getCol(r, "sscui reference", "sscui"), getCol(r, "topic"), String(r[questionCol] || "")]
      .join(" ").toLowerCase()
  ).join(" ");

  let countryBlock = "";
  if (countryCodes.length) {
    const lines = [];
    for (const code of countryCodes) {
      const countryDef = COUNTRY_RULES[code];
      if (!countryDef) continue;
      const matchingRules = countryDef.rules.filter(rule =>
        rule.processAreaKeywords.some(kw => rowsText.includes(kw.toLowerCase()))
      );
      if (!matchingRules.length) continue;
      lines.push(`\n${countryDef.name}:`);
      for (const rule of matchingRules) {
        lines.push(`  • [${rule.mandatory ? "MANDATORY" : "Conditional"}] ${rule.requirement}`);
        lines.push(`    Basis: ${rule.basis}`);
        lines.push(`    Cloud PE approach: ${rule.cloudPEApproach}`);
        lines.push(`    Confirm with client: ${rule.confirmPrompt}`);
      }
    }
    if (lines.length) {
      countryBlock = `\n\nCOUNTRY LOCALISATION CONTEXT (${countryCodes.join(", ")}):\n` +
        `For questions related to the areas below, incorporate these statutory requirements into your standardValue and watchOut.\n` +
        `MANDATORY items MUST appear in the standardValue as pre-answered context. Include the confirmPrompt in watchOut where relevant.\n` +
        lines.join("\n");
    }
  }

  // ── Build industry context ─────────────────────────────────────────────────
  const industryKey = normalizeIndustry(rawIndustry || "");
  let industryBlock = "";
  if (industryKey && INDUSTRY_RULES[industryKey]) {
    const ind = INDUSTRY_RULES[industryKey];
    // Find relevant themes: match domain + row text against theme keys
    const domainLower = domain.toLowerCase();
    const themeLines = [];
    for (const [themeKey, themeText] of Object.entries(ind.processAreaThemes)) {
      if (themeKey === "default" || domainLower.includes(themeKey) || rowsText.includes(themeKey)) {
        themeLines.push(`  • ${themeText}`);
      }
    }
    industryBlock = `\n\nINDUSTRY CONTEXT — ${ind.name}:\n` +
      `Frame ALL answers from a ${ind.name} industry perspective. Use ${ind.name}-specific terminology, role names, and examples throughout — avoid generic consulting titles (e.g. "Senior Consultant", "Junior Developer", "Manager") unless they are the standard term for this industry. Relevant themes for this domain:\n` +
      (themeLines.length ? themeLines.join("\n") : `  • ${ind.processAreaThemes.default}`) + "\n" +
      `Industry-specific questions to raise in standardValue where applicable:\n` +
      ind.mustAsk.map(q => `  → ${q}`).join("\n");
  }

  // ── Check Cloud PE constraints relevant to this batch ─────────────────────
  let cloudPEBlock = "";
  const relevantConstraints = CLOUDPE_CONSTRAINTS.filter(c =>
    c.keywords.some(kw => rowsText.includes(kw.toLowerCase()))
  );
  if (relevantConstraints.length) {
    cloudPEBlock = `\n\nCLOUD PE CONSTRAINT WATCH-OUTS for this batch:\n` +
      relevantConstraints.map(c =>
        `  [${c.type}] Client may expect: "${c.expectation}" → Reality: ${c.reality} Extension: ${c.extensionPath}`
      ).join("\n");
  }

  // ── Build grounding block from user-uploaded reference Excel ─────────────
  let groundingBlock = "";
  const groundingData = readGrounding();
  if (groundingData && groundingData.sheets && groundingData.sheets.length) {
    // Build a combined search corpus: question text + domain + topic + process area
    const searchCorpus = rows.map(r =>
      [String(r[questionCol] || r["Question"] || ""),
       getCol(r, "topic"), getCol(r, "process area"), domain]
        .join(" ").toLowerCase()
    ).join(" ");

    // Tokenise to words of 3+ chars (lowered from 4 to catch "gst", "tds", "vat", "bom" etc.)
    const words = [...new Set((searchCorpus.match(/\b\w{3,}\b/g) || []))];

    const scored = [];
    for (const sheet of groundingData.sheets) {
      // Bonus: sheet name that matches domain scores all its rows higher
      const sheetBonus = sheet.sheet.toLowerCase().split(/\W+/)
        .some(w => w.length >= 3 && searchCorpus.includes(w)) ? 3 : 0;

      for (const row of sheet.rows) {
        const rowStr = Object.values(row).join(" ").toLowerCase();
        const hits   = words.filter(w => rowStr.includes(w)).length;
        const score  = hits + sheetBonus;
        // Include any row that shares at least 1 keyword OR is in a matching sheet
        if (score > 0) scored.push({ score, sheet: sheet.sheet, source: sheet.source, row });
      }
    }

    // Sort by relevance, take top 10 (up from 8 to give grounding more surface area)
    scored.sort((a, b) => b.score - a.score);
    const topRows = scored.slice(0, 10);

    if (topRows.length) {
      const lines = topRows.map(({ sheet, row }) => {
        const cells = Object.entries(row)
          .filter(([, v]) => v)
          .map(([k, v]) => `${k}: ${String(v).slice(0, 300)}`)
          .join(" | ");
        return `  [${sheet}] ${cells}`;
      });
      groundingBlock = `\n\nREFERENCE GROUNDING DATA (from client's uploaded reference Excel):\n` +
        `Use the rows below as the PRIMARY source for standardValue where they directly answer a question.\n` +
        `Country-specific or project-specific content here takes precedence over generic SAP defaults.\n` +
        lines.join("\n");
    }
    console.log(`[BDCQ enrich] grounding: ${groundingData.sheets.reduce((s,sh)=>s+sh.rows.length,0)} total rows → ${topRows.length} matched for domain "${domain}"`);
  }

  console.log(`[BDCQ enrich] domain="${domain}" rows=${rows.length} | grounding=${groundingBlock ? "YES" : "NO"} | country=${countryCodes.join(",") || "none"} | industry=${industryKey || "none"}`);

  const prompt = `You are an SAP S/4HANA Cloud Public Edition (Cloud PE) configuration consultant.

For each question, use the Topic, SSCUI reference, and SAP ID provided to give the most specific answer you can from your SAP knowledge. Where you know specific pre-delivered codes, types, or IDs (e.g. document types, work item types, number ranges) include them. Where you are less certain, give the best Cloud PE guidance you can and name the relevant Fiori app.${groundingBlock}${countryBlock}${industryBlock}${cloudPEBlock}

${hasRefData ? `Where SAP BDCQ reference data is provided, reformat it:
- "standardValue": 1–3 concise sentences. Incorporate country statutory requirements where applicable — MANDATORY items must appear as pre-answered context, not open questions. Add industry-specific framing. Keep any specific codes/types. Name the Fiori app.
- "watchOut": 1–3 bullet points. Include country statutory deadlines or compliance risks. Flag Cloud PE constraints where relevant.
Where reference data is absent for a question, use your SAP Cloud PE knowledge.` : `For each question:
- "standardValue": Best available SAP Cloud PE standard or default. Incorporate country statutory requirements for MANDATORY items as pre-answered context. Frame from ${industryKey || "the client"} industry perspective. Include specific codes/types where known. Name the Fiori app. If genuinely client-specific, say "Client-specific — confirm in workshop".
- "watchOut": Key constraints or irreversible decisions. Include country statutory deadlines or compliance risks. Flag Cloud PE constraints. Use "" if none.`}

RULES — strictly enforced:
- SAP S/4HANA Cloud Public Edition ONLY. No T-codes, SPRO, ABAP, SAP GUI, or on-premise references.
- ALWAYS return a valid JSON array — even if uncertain, return your best answer in JSON. Never refuse.
- Array length MUST exactly match the number of questions (${rows.length}).
- The "i" field MUST be the 0-based position of the question in the list above. Never omit it.
- NEVER reference question numbers (e.g. "Q16", "see Q16–17", "question 5") in any answer — reference topic names instead (e.g. "see Revenue Recognition configuration").${industryKey ? `\n- Industry examples (ENFORCED): Every standardValue must use ${INDUSTRY_RULES[industryKey]?.name || industryKey}-specific terminology, roles, and examples. Generic consulting titles such as "Senior Consultant", "Junior Developer", or "Manager" are PROHIBITED unless that is the standard industry term. Use the role names and examples from the INDUSTRY CONTEXT block above.` : ""}${industryKey ? `\n- Industry examples (ENFORCED): Every standardValue must use ${INDUSTRY_RULES[industryKey]?.name || industryKey}-specific terminology, roles, and examples. Generic consulting titles such as "Senior Consultant", "Junior Developer", or "Manager" are PROHIBITED unless that is the standard industry term. Use the role names and examples from the INDUSTRY CONTEXT block above.` : ""}

Domain: ${domain}
Questions (${rows.length} total):
${questionLines}

Return format — one object per question, include the 0-based index "i" so alignment can be verified:
[{"i":0,"standardValue":"...","watchOut":"..."},{"i":1,"standardValue":"...","watchOut":"..."},...]`;

  try {
    const raw = await runClaude(prompt);
    // Strip markdown code fences if present (```json ... ```)
    const cleaned = raw.replace(/```(?:json)?\s*/gi, "").replace(/```\s*/g, "").trim();
    const jsonStr = extractJsonArray(cleaned);
    if (!jsonStr) {
      const preview = cleaned.slice(0, 300).replace(/\n/g, " ");
      throw new Error(`No JSON array in Claude response. Claude said: "${preview}"`);
    }
    let parsedEnrichments = JSON.parse(jsonStr);

    // Re-order by the "i" index field Claude was asked to include, to prevent
    // off-by-one content swaps when Claude shifts its output array.
    const hasIndex = parsedEnrichments.length > 0 && typeof parsedEnrichments[0].i === "number";
    if (hasIndex) {
      const sorted = new Array(rows.length).fill(null);
      for (const item of parsedEnrichments) {
        const idx = item.i;
        if (typeof idx === "number" && idx >= 0 && idx < rows.length) sorted[idx] = item;
      }
      // Fill any gaps with empty objects (in case Claude missed a row)
      parsedEnrichments = sorted.map(item => item || { standardValue: "", watchOut: "" });
    }
    const validEnrichments = parsedEnrichments;

    // Map enrichments back to rawRows — skipped rows get empty enrichment
    let validIdx = 0;
    const enrichments = validIndices.map(isValid => {
      if (isValid) return validEnrichments[validIdx++] || { standardValue: "", watchOut: "" };
      return { standardValue: "", watchOut: "" };
    });

    // Fix common SAP typos in Claude's output (e.g. "publc" → "public")
    const cleanedEnrichments = enrichments.map(e => ({
      standardValue: fixSapTypos(e.standardValue),
      watchOut:      fixSapTypos(e.watchOut),
    }));

    // Apply functional-team overrides — verified values always win over Claude
    const overrides = loadOverrides();
    const finalEnrichments = cleanedEnrichments.map((e, i) => {
      const sapId = String(rawRows[i]?.["SAP ID"] || rawRows[i]?.["SAP id"] || "").trim();
      const ov = sapId && overrides[sapId];
      if (!ov) return e;
      return {
        standardValue: ov.standardValue || e.standardValue,
        watchOut:      ov.watchOut      || e.watchOut
      };
    });

    // Add Country / Region from AllCountries grounding (direct lookup, no Claude)
    const withCountryRegion = finalEnrichments.map((e, i) => {
      const row   = rawRows[i] || {};
      const topic = String(row["Topic"] || row["Process"] || "").trim();
      const cr    = lookupCountryRegion(domain, topic, countryCodes);
      return cr ? { ...e, countryRegion: cr } : e;
    });

    console.log(`[BDCQ enrich] countryRegion lookup complete (allCountries ${loadAllCountriesRows() ? "loaded" : "not found"})`);
    res.json({ ok: true, enrichments: withCountryRegion });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ── POST /api/bdcq/export-editor ─────────────────────────────────────────────
// Builds Excel with Cover sheet + BDCQ Questionnaire sheet.
// Body: { filename, rows, columns, clientName, clientLogo, partnerLogo }
//   clientLogo / partnerLogo: base64 data URL strings (optional)
router.post("/export-editor", express.json({ limit: "20mb" }), async (req, res) => {
  const { filename, rows: rawRows = [], columns = [], clientName = "", overviewText = "", clientLogo = null, partnerLogo = null, selectedCountries = [] } = req.body || {};
  if (!filename) return res.status(400).json({ ok: false, error: "filename is required" });
  if (!rawRows.length) return res.status(400).json({ ok: false, error: "No rows to export" });
  console.log(`[BDCQ export] columns received (${columns.length}):`, columns.join(", "));

  // ── Filter out empty / template rows before export ────────────────────────
  // Only filter truly blank rows — AllCountries rows (_fromAllCountries) always pass.
  const rows = rawRows.filter(r => {
    if (r._fromAllCountries) return true; // AllCountries grounding rows always export
    const q = String(
      r["Question"] || r["Business Driven Configuration Questionnaire"] || ""
    ).trim();
    const passes = q.length >= 3;
    if (!passes) console.log(`[BDCQ export] dropping row — blank question:`, JSON.stringify(r).slice(0, 120));
    return passes;
  });
  console.log(`[BDCQ export] ${rawRows.length} received → ${rows.length} after filter`);

  try {
    const ExcelJS = require("exceljs");
    const outDir  = path.join(ROOT, "output");
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

    const wb = new ExcelJS.Workbook();
    wb.creator  = "Fulcrum";
    wb.created  = new Date();

    // ── Helper: parse base64 data URL → Buffer ──────────────────────────────
    function dataUrlToBuffer(dataUrl) {
      if (!dataUrl) return null;
      const match = dataUrl.match(/^data:image\/(png|jpeg|jpg|gif);base64,(.+)$/i);
      if (!match) return null;
      return { ext: match[1].replace("jpg","jpeg"), buf: Buffer.from(match[2], "base64") };
    }

    // ── SHEET 1: Cover ───────────────────────────────────────────────────────
    const cover = wb.addWorksheet("Cover", { views: [{ showGridLines: false }] });
    cover.getColumn("A").width = 4;
    cover.getColumn("B").width = 28;
    cover.getColumn("C").width = 36;
    cover.getColumn("D").width = 28;
    cover.getColumn("E").width = 4;

    // Purple header band (rows 1-8)
    for (let r = 1; r <= 8; r++) {
      const row = cover.getRow(r);
      row.height = r === 1 || r === 8 ? 8 : 30;
      ["A","B","C","D","E"].forEach(c => {
        const cell = cover.getCell(`${c}${r}`);
        cell.fill = { type:"pattern", pattern:"solid", fgColor:{ argb:"FF5B21B6" } };
      });
    }

    // Logo placeholders / actual logos in header
    const clientImg  = dataUrlToBuffer(clientLogo);
    const partnerImg = dataUrlToBuffer(partnerLogo);

    if (clientImg) {
      const imgId = wb.addImage({ buffer: clientImg.buf, extension: clientImg.ext });
      cover.addImage(imgId, { tl:{ col:1, row:1 }, br:{ col:2.8, row:7 }, editAs:"oneCell" });
    } else if (clientName) {
      const nc = cover.getCell("B3");
      nc.value = clientName;
      nc.font  = { name:"Calibri", size:18, bold:true, color:{ argb:"FFFFFFFF" } };
      nc.alignment = { vertical:"middle" };
      cover.mergeCells("B3:C6");
    }

    if (partnerImg) {
      const imgId2 = wb.addImage({ buffer: partnerImg.buf, extension: partnerImg.ext });
      cover.addImage(imgId2, { tl:{ col:3, row:1 }, br:{ col:4.8, row:7 }, editAs:"oneCell" });
    } else {
      const pc = cover.getCell("D3");
      pc.value = "Accenture";
      pc.font  = { name:"Calibri", size:16, bold:true, color:{ argb:"FFFFFFFF" } };
      pc.alignment = { vertical:"middle", horizontal:"right" };
      cover.mergeCells("D3:D6");
    }

    // Title row (row 9)
    cover.getRow(9).height = 14;

    // Main title (rows 10-13)
    cover.mergeCells("B10:D12");
    const title = cover.getCell("B10");
    title.value = "Business Driven Configuration Questionnaire";
    title.font  = { name:"Calibri", size:22, bold:true, color:{ argb:"FF1E293B" } };
    title.alignment = { vertical:"middle", wrapText:true };
    cover.getRow(10).height = 36;
    cover.getRow(11).height = 36;
    cover.getRow(12).height = 36;

    // Subtitle
    cover.mergeCells("B13:D13");
    const sub = cover.getCell("B13");
    sub.value = "SAP S/4HANA Cloud Public Edition · Explore Phase";
    sub.font  = { name:"Calibri", size:12, color:{ argb:"FF64748B" } };
    cover.getRow(13).height = 20;

    // Divider row
    cover.getRow(14).height = 12;
    ["B","C","D"].forEach(c => {
      cover.getCell(`${c}14`).border = { bottom:{ style:"medium", color:{ argb:"FF5B21B6" } } };
    });

    // Summary stats (row 16+)
    const domains   = [...new Set(rows.map(r => r.Module).filter(Boolean))];
    const answered  = rows.filter(r => String(r.Answer||"").trim()).length;
    const exported  = new Date().toLocaleDateString("en-GB", { day:"2-digit", month:"short", year:"numeric" });

    // Derive countries in scope — prefer UI-selected countries, fall back to row data
    const _ISO_NAMES = {
      IN:"India", DE:"Germany", US:"USA", GB:"UK", BR:"Brazil",
      AE:"UAE", SA:"Saudi Arabia", FR:"France", AU:"Australia", SG:"Singapore",
      JP:"Japan", CN:"China", KR:"South Korea", VN:"Vietnam", NL:"Netherlands",
      PL:"Poland", IT:"Italy", ES:"Spain", AT:"Austria", SE:"Sweden",
      MX:"Mexico", CA:"Canada", ZA:"South Africa", AR:"Argentina",
      DK:"Denmark", FI:"Finland", NO:"Norway", IE:"Ireland", BE:"Belgium",
      PT:"Portugal", CH:"Switzerland", IL:"Israel", EG:"Egypt", KW:"Kuwait",
      QA:"Qatar", OM:"Oman", MY:"Malaysia", ID:"Indonesia", PH:"Philippines",
    };
    let countriesInScope = "";
    if (selectedCountries.length) {
      // Use exactly what the user ticked in the UI
      countriesInScope = selectedCountries
        .map(c => _ISO_NAMES[c.toUpperCase()] || c)
        .join(", ");
    } else {
      // Fall back to codes derived from row data (excludes "LC" catch-all)
      const localeCodes = [...new Set(
        rows
          .map(r => String(r._localizationCountry || "").trim().toUpperCase())
          .filter(c => c && c !== "GLOBAL" && c !== "LC" && /^[A-Z]{2,3}$/.test(c))
      )].sort();
      countriesInScope = localeCodes.map(c => _ISO_NAMES[c] || c).join(", ");
    }

    const coverIndustry = rows.find(r => r.Industry)?.Industry || "";

    const stats = [
      ["Client",              clientName || "—"],
      ...(coverIndustry      ? [["Industry",           coverIndustry]]     : []),
      ["Total Questions",     String(rows.length)],
      ["Domains Covered",     domains.join(", ") || "—"],
      ...(countriesInScope   ? [["Countries in Scope", countriesInScope]]  : []),
      ["Answered",            `${answered} / ${rows.length}`],
      ["Export Date",         exported],
      ["Prepared by",         "Fulcrum v1 · Accenture"],
    ];

    cover.getRow(15).height = 10;
    stats.forEach(([label, value], i) => {
      const rowNum = 16 + i * 2;
      cover.getRow(rowNum).height = 22;

      const lc = cover.getCell(`B${rowNum}`);
      lc.value = label;
      lc.font  = { name:"Calibri", size:10, bold:true, color:{ argb:"FF9CA3AF" } };

      const vc = cover.getCell(`C${rowNum}`);
      vc.value = value;
      vc.font  = { name:"Calibri", size:11, color:{ argb:"FF1E293B" } };
      cover.mergeCells(`C${rowNum}:D${rowNum}`);
    });

    // Overview text row (below stats, only if provided)
    if (overviewText) {
      const ovRow = 16 + stats.length * 2 + 1;
      cover.getRow(ovRow - 1).height = 10;
      const ovCell = cover.getCell(`B${ovRow}`);
      cover.getRow(ovRow).height = 50;
      ovCell.value = overviewText;
      ovCell.font  = { name:"Calibri", size:10, italic:true, color:{ argb:"FF475569" } };
      ovCell.alignment = { wrapText:true, vertical:"top" };
      cover.mergeCells(`B${ovRow}:D${ovRow}`);
    }

    // ── SHEET 2: BDCQ Questionnaire ─────────────────────────────────────────
    const ws = wb.addWorksheet("BDCQ Questionnaire", {
      views: [{ state:"frozen", ySplit:1 }]
    });

    const aiCols   = ["SAP Standard Value", "Watch Out / Constraint", "Country / Region", "Answer", "Notes"];
    const INTERNAL = new Set(["_localizationCountry"]);

    // Use the columns array from frontend (includes custom cols the user added).
    // Fall back to row keys only if columns wasn't sent.
    const allRowKeys = rows.length ? Object.keys(rows[0]).filter(k => !aiCols.includes(k) && !INTERNAL.has(k)) : [];
    const rawBase  = columns.length > 0 ? columns : allRowKeys;
    const baseCols = rawBase.filter(k => !INTERNAL.has(k));

    // Ensure any custom columns not in row data are still included (they'll be blank)
    const hasLocale  = rows.some(r => r._localizationCountry && r._localizationCountry !== "Global");
    const sectionIdx = baseCols.indexOf("Section");
    if (hasLocale) {
      const insertAt = sectionIdx >= 0 ? sectionIdx + 1 : baseCols.length;
      baseCols.splice(insertAt, 0, "Localization Country");
    }

    // Add # row number as first column
    const outCols = ["#", ...baseCols, ...aiCols];

    // Column widths
    ws.columns = outCols.map(col => {
      const cl = col.toLowerCase();
      let width = 22;
      if (cl.includes("standard value") || cl.includes("watch out") ||
          cl.includes("question") || cl.includes("guidance")) width = 55;
      else if (cl === "country / region") width = 40;
      else if (cl === "answer" || cl === "notes") width = 38;
      return { header: col, key: col, width };
    });

    // Header row style
    const headerRow = ws.getRow(1);
    headerRow.height = 22;
    outCols.forEach((col, i) => {
      const cell = headerRow.getCell(i + 1);
      cell.value = col;
      cell.font  = { name:"Calibri", size:10, bold:true, color:{ argb:"FFFFFFFF" } };
      const isCR = col === "Country / Region";
      cell.fill  = { type:"pattern", pattern:"solid", fgColor:{ argb: isCR ? "FF065F46" : "FF5B21B6" } };
      cell.alignment = { vertical:"middle", wrapText:true };
      cell.border = { bottom:{ style:"thin", color:{ argb: isCR ? "FF10B981" : "FF7C3AED" } } };
    });

    // Data rows
    rows.forEach((row, ri) => {
      const dataRow = ws.addRow(outCols.map((col, ci) => {
        if (col === "#")                     return ri + 1;                          // row number
        if (col === "Localization Country")  return row._localizationCountry || "Global";
        const v = row[col];
        if (v === undefined || v === null) return "";
        // Fix SAP typos in all text cells (catches typos from original Excel as well as Claude output)
        return typeof v === "number" ? v : fixSapTypos(String(v));
      }));
      dataRow.height = 36;
      dataRow.eachCell({ includeEmpty:true }, cell => {
        cell.alignment = { vertical:"top", wrapText:true };
        cell.font = { name:"Calibri", size:10 };
        // Alternate row shading
        if (ri % 2 === 1) {
          cell.fill = { type:"pattern", pattern:"solid", fgColor:{ argb:"FFF5F3FF" } };
        }
      });
      // Highlight AI columns
      ["SAP Standard Value","Watch Out / Constraint"].forEach(col => {
        const ci = outCols.indexOf(col);
        if (ci >= 0) {
          const cell = dataRow.getCell(ci + 1);
          cell.font = { name:"Calibri", size:10, color:{ argb:"FF3730A3" } };
        }
      });
      // Country / Region — teal highlight
      const crColIdx = outCols.indexOf("Country / Region");
      if (crColIdx >= 0) {
        const cell = dataRow.getCell(crColIdx + 1);
        cell.font = { name:"Calibri", size:10, color:{ argb:"FF065F46" } };
        if (cell.value) {
          cell.fill = { type:"pattern", pattern:"solid", fgColor:{ argb:"FFD1FAE5" } };
        }
      }
    });

    // ── Save ─────────────────────────────────────────────────────────────────
    const outFilename = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
    const outPath     = path.join(outDir, outFilename);
    await wb.xlsx.writeFile(outPath);

    res.json({ ok: true, filename: outFilename, rowCount: rows.length });
  } catch (e) {
    console.error("[BDCQ export]", e);
    res.status(500).json({ ok: false, error: e.message });
  }
});

// ── POST /api/bdcq/ai-local-questions ────────────────────────────────────────
// When AllCountries grounding has no local questions for a domain+country combo,
// ask Claude to generate country-specific BDCQ questions from its SAP knowledge.
// Body: { domain, countries: ["IN","GB",...], industry }
router.post("/ai-local-questions", express.json({ limit: "64kb" }), async (req, res) => {
  const { domain = "", countries = [], industry = "" } = req.body || {};
  if (!domain || !countries.length) {
    return res.status(400).json({ ok: false, error: "domain and countries are required" });
  }

  const ISO_TO_NAME = {
    IN:"India", DE:"Germany", US:"USA", GB:"UK", BR:"Brazil",
    AE:"UAE", SA:"Saudi Arabia", FR:"France", AU:"Australia", SG:"Singapore",
    JP:"Japan", CN:"China", KR:"South Korea", VN:"Vietnam", NL:"Netherlands",
    PL:"Poland", IT:"Italy", ES:"Spain", AT:"Austria", SE:"Sweden",
    MX:"Mexico", CA:"Canada", ZA:"South Africa", AR:"Argentina",
    DK:"Denmark", FI:"Finland", NO:"Norway", IE:"Ireland", BE:"Belgium",
    PT:"Portugal", CH:"Switzerland", IL:"Israel", EG:"Egypt", KW:"Kuwait",
    QA:"Qatar", OM:"Oman", MY:"Malaysia", ID:"Indonesia", PH:"Philippines",
  };

  const countryNames   = countries.map(c => ISO_TO_NAME[c] || c).join(", ");
  const industryCtx    = industry ? ` for a ${industry} client` : "";
  const domainScopeRef = getDomainScopeRefs(domain);

  const prompt = `You are an SAP S/4HANA Cloud Public Edition consultant preparing a Business Driven Configuration Questionnaire (BDCQ) workshop${industryCtx}.
Use regulations and mandates current as of 2025. Be precise about deadlines — if a date is uncertain or has been delayed, say so in the watchOut rather than stating a hard date.

KEY 2025 REGULATORY FACTS (must reflect these accurately):
- Germany B2B e-invoicing: receiving structured e-invoices (EN 16931 / XRechnung / ZUGFeRD) is mandatory from January 2025. Issuing is mandatory from January 2027 (revenue > €800K) or January 2028 (all businesses). Do NOT say it is "not yet required" — receiving is already required. For Germany use XRechnung, ZUGFeRD, EN 16931, and CIUS-DE terminology. Do NOT mention ZATCA in any Germany context — ZATCA is Saudi Arabia's tax authority and is irrelevant to Germany.
- France B2B e-invoicing (PPF/Chorus Pro): the B2B mandate has been delayed multiple times. As of mid-2025 the mandate is expected in 2026 but no firm date is confirmed. Do NOT state "January 2025" or any specific hard deadline for French B2B. Instead, flag in watchOut: "France B2B e-invoicing mandate has been delayed — monitor official DGFIP communications for confirmed go-live date." Chorus Pro is already mandatory for public-sector (B2G) invoicing.
- Brazil NF-e / NFS-e: SEFAZ real-time authorisation is mandatory for goods; NFS-e varies by municipality. eSocial is mandatory for all employers.
- India e-invoicing: mandatory for taxpayers above ₹5 crore turnover threshold.
- UK MTD: Making Tax Digital for VAT is live; MTD for ITSA phases from 2026.

ACCOUNTING STANDARDS — use the correct standard for each domain:
- Professional Services / Project-based revenue: IFRS 15 (Revenue from Contracts with Customers). Do NOT reference IFRS 17 — that is the Insurance Contracts standard and is never relevant to PS.
- Finance / General: IFRS 16 (Leases), IFRS 9 (Financial Instruments) as applicable.

CLOUD PE GUARDRAILS — strictly enforce:
- All configuration via SAP Fiori apps only. No T-codes, SPRO, SM30, SE16, ABAP, BAdI, user exits, or SAP GUI.
- Extensions only via SAP BTP (side-by-side) — never in-app modifications.
- For localisation: SAP Localisation Hub and DRC (Document and Reporting Compliance) scope items handle country statutory content.
- Time recording: SAP Cloud for Projects (formerly Cloud for Service and Projects). No CATS T-code references.
- Intercompany billing: use standard Intercompany Billing Profile configuration via Fiori; no custom billing routines.
- Revenue recognition: via Revenue Accounting and Reporting (RAR) module — IFRS 15 five-step model. Not manual journal entries.

Generate country-specific BDCQ questions for the **${domain}** process area that are relevant specifically for: **${countryNames}**.

These are questions that arise ONLY because of country-specific statutory, regulatory, or legal requirements — NOT generic global questions.
Examples of what to include:
- Tax/VAT/GST statutory requirements (e.g. UK MTD, Brazil NF-e, India GST/TDS/e-invoice)
- Payroll social security and withholding (e.g. UK PAYE/NIC, Brazil eSocial)
- Country-specific document requirements (e.g. e-invoicing mandates, audit file formats)
- Local GAAP / IFRS parallel ledger requirements
- Country-specific number formats, fiscal year variants, bank formats

RULES:
- SAP S/4HANA Cloud Public Edition ONLY — no T-codes, ABAP, on-premise references
- Each question must reference the specific country it applies to
- Generate 3–4 questions PER COUNTRY (not 3–4 total). Each country in scope deserves its own set of localisation questions at the same depth.
- Only include questions where you are confident of the statutory requirement
- If a regulatory deadline is uncertain or subject to change, flag this in watchOut — never state an unconfirmed date as fact

For scopeRef: MANDATORY — always populate with the most relevant SAP S/4HANA Cloud Public Edition scope item IDs.
  Known scope items for the ${domain} domain from the SAP catalog: ${domainScopeRef || "see SAP scope catalog"}.
  For country statutory / DRC items also include relevant localisation scope IDs (e.g. BR6 for Brazil, J68 for e-invoicing, BD6 for document compliance).
  Choose the most applicable IDs from the list above. Only leave blank if no scope item can reasonably apply.
For sapId: use the SAP BDCQ question ID format (e.g. PS-FR-001, PS-DE-002). Assign a plausible ID if not known from SAP documentation.

Return a JSON array only, no other text:
[
  {
    "question": "Question text",
    "topic": "Short topic (e.g. GST / e-Invoicing)",
    "country": "ISO-2 code (e.g. IN)",
    "countryName": "Full country name",
    "localizationType": "CLT or Non-CLT",
    "sampleValue": "SAP standard default or pre-delivered value if known, else empty string",
    "scopeRef": "Relevant SAP scope item IDs, or empty string",
    "sapId": "SAP BDCQ question ID if known, or empty string"
  }
]`;

  try {
    const raw     = await runClaude(prompt);
    const cleaned = raw.replace(/```(?:json)?\s*/gi, "").replace(/```\s*/g, "").trim();
    const jsonStr2 = extractJsonArray(cleaned);
    if (!jsonStr2) throw new Error("No JSON array in Claude response");
    const questions = JSON.parse(jsonStr2);

    // Convert to BDCQ row format; fix any SAP typos Claude introduced
    const rows = questions.map(q => ({
      Module:              domain,
      Section:             (q.country || "AI").toUpperCase().slice(0, 2),
      _localizationCountry: (q.country || "AI").toUpperCase().slice(0, 2),
      _fromAllCountries:   false,
      _aiGenerated:        true,
      Question:            fixSapTypos(q.question  || ""),
      Topic:               q.topic     || "",
      "Localization Type": q.localizationType || "",
      "Sample Value":      fixSapTypos(q.sampleValue || ""),
      "Country / Region":  q.countryName ? `Local – ${q.countryName}` : "",
      "Scope Ref":         q.scopeRef || domainScopeRef,
      "SAP ID":            q.sapId    || "",
    }));

    console.log(`[BDCQ ai-local] generated ${rows.length} questions for ${domain} + ${countryNames}`);
    res.json({ ok: true, rows, domain, countries, countryNames });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

module.exports = router;
