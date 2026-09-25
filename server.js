// server.js — Fulcrum · SAP S/4HANA Cloud PE
"use strict";

const express    = require("express");
const path       = require("path");
const fs         = require("fs");
const os         = require("os");
const { exec, spawn, execSync } = require("child_process");

// ── Agentic pipeline (agents/ folder) ────────────────────────────────────────
const orchestrator = require("./agents/orchestrator");

const app  = express();
const PORT = process.env.PORT || 8321;
const ROOT = __dirname;

app.use(express.json({ limit: "25mb" }));
app.use(express.static(path.join(ROOT, "public")));

// ── Claude CLI path resolution (Windows) ─────────────────────────────────────
// claude.cmd requires cmd.exe which is not reliably in PATH when spawned from
// Node. Use claude.exe directly from its known install location instead.
const _isWinServer = process.platform === "win32";

// Locate the claude executable across the many ways it can be installed on
// Windows. Bare "claude" is NOT spawnable without a shell (Node does not apply
// PATHEXT), so a tester who can run `claude` in a terminal still hits ENOENT
// unless we resolve the full path (with extension) here.
//   • native installer → %USERPROFILE%\.local\bin\claude.exe
//   • npm global       → %APPDATA%\npm\claude.cmd  (a shim — needs shell:true)
//   • custom / on PATH  → resolved via `where claude`
function resolveClaudeExe() {
  if (!_isWinServer) return "claude";

  // 1. Explicit override always wins
  if (process.env.CLAUDE_PATH && fs.existsSync(process.env.CLAUDE_PATH)) return process.env.CLAUDE_PATH;

  const up           = process.env.USERPROFILE || process.env.HOME || os.homedir();
  const appData      = process.env.APPDATA      || path.join(up, "AppData", "Roaming");
  const localAppData = process.env.LOCALAPPDATA || path.join(up, "AppData", "Local");

  // 2. Known install locations — checked first so we get a real full path
  const knownPaths = [
    path.join(up, ".local", "bin", "claude.exe"),
    path.join(localAppData, "Programs", "claude", "claude.exe"),
    path.join(appData, "npm", "claude.exe"),
    path.join(appData, "npm", "claude.cmd"),
    path.join(up, ".local", "bin", "claude.cmd"),
  ];
  for (const p of knownPaths) { if (fs.existsSync(p)) return p; }

  // 3. Whatever is on PATH — `where` returns the full path WITH extension,
  //    which (unlike bare "claude") Node can spawn directly.
  try {
    const found = execSync("where claude", { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] })
      .split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const pick = found.find(p => p.toLowerCase().endsWith(".exe"))
              || found.find(p => p.toLowerCase().endsWith(".cmd"))
              || found[0];
    if (pick && fs.existsSync(pick)) return pick;
  } catch (_) { /* claude not on PATH */ }

  return "claude"; // last resort — let OS PATH try (may ENOENT without shell)
}
const _claudeExe = resolveClaudeExe(); // resolve once at startup
console.log(`[Fulcrum] Claude CLI: ${_claudeExe}`);

// Standard args and env for every claude --print call
const _claudeArgs = (model) => ["--model", model, "--print", "--dangerously-skip-permissions"];
const _claudeEnv  = () => ({
  ...process.env,
  PATH: [
    path.join(os.homedir(), ".local", "bin"),
    path.join(os.homedir(), "AppData", "Local", "Programs", "claude"),
    path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "npm"),
    process.env.PATH || "",
  ].join(process.platform === "win32" ? ";" : ":"),
});
const _claudeIsCmd = _isWinServer && /\.(cmd|bat)$/i.test(_claudeExe);
const _claudeOpts = () => ({ stdio: ["pipe", "pipe", "pipe"], shell: _claudeIsCmd, cwd: os.tmpdir(), env: _claudeEnv() });

// Spawn claude with the correct shell mode. A .cmd/.bat shim must run through
// cmd.exe (shell:true); quoting the path keeps install dirs with spaces working
// (e.g. C:\Users\First Last\AppData\Roaming\npm\claude.cmd).
function _claudeSpawn(model) {
  const exe = _claudeIsCmd ? `"${_claudeExe}"` : _claudeExe;
  return spawn(exe, _claudeArgs(model), _claudeOpts());
}

// ── helpers ──────────────────────────────────────────────────────────────────

const CATALOG_PATH = path.join(ROOT, "scope-catalog.json");
const CACHE_PATH     = path.join(ROOT, "kdd-cache.json");
const DECISIONS_DIR  = path.join(ROOT, "decisions");

// ── Decision store helpers ────────────────────────────────────────────────────
function clientSlug(client) {
  return (client || "shared").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "shared";
}
function clientLabel(slug) {
  return slug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}
function clientFile(client) {
  if (!fs.existsSync(DECISIONS_DIR)) fs.mkdirSync(DECISIONS_DIR, { recursive: true });
  return path.join(DECISIONS_DIR, `${clientSlug(client)}.json`);
}
function readClientDecisions(client) {
  const f = clientFile(client);
  try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch { return []; }
}
function readAllDecisions() {
  if (!fs.existsSync(DECISIONS_DIR)) return [];
  return fs.readdirSync(DECISIONS_DIR).filter(f => f.endsWith(".json")).flatMap(f => {
    try { return JSON.parse(fs.readFileSync(path.join(DECISIONS_DIR, f), "utf8")); } catch { return []; }
  });
}
function writeClientDecisions(client, decisions) {
  const f = clientFile(client);
  fs.writeFileSync(f, JSON.stringify(decisions, null, 2), "utf8");
}
// Strip client name from stored entry — client identity is the file it lives in
function sanitizeDecisionEntry(entry) {
  const { client, ...rest } = entry;
  return rest;
}

// ── One-time migration: flat decisions.json → decisions/<slug>.json ───────────
(function migrateDecisions() {
  const old = path.join(ROOT, "decisions.json");
  if (!fs.existsSync(old)) return;
  try {
    const all = JSON.parse(fs.readFileSync(old, "utf8"));
    if (!Array.isArray(all) || !all.length) return;
    const byClient = {};
    all.forEach(d => {
      const slug = clientSlug(d.client || "shared");
      (byClient[slug] = byClient[slug] || []).push(sanitizeDecisionEntry(d));
    });
    if (!fs.existsSync(DECISIONS_DIR)) fs.mkdirSync(DECISIONS_DIR, { recursive: true });
    Object.entries(byClient).forEach(([slug, items]) => {
      const dest = path.join(DECISIONS_DIR, `${slug}.json`);
      if (!fs.existsSync(dest)) fs.writeFileSync(dest, JSON.stringify(items, null, 2), "utf8");
    });
    fs.renameSync(old, old + ".migrated");
    console.log(`[startup] Migrated ${all.length} decisions → decisions/ directory`);
  } catch (e) { console.warn("[startup] Decision migration skipped:", e.message); }
})();

const CLAUDE_TIMEOUT_MS   = 240_000;         // 240 s safety net — complex processes can take 100-150s
const CLAUDE_MODEL        = process.env.CLAUDE_MODEL || "claude-haiku-4-5-20251001"; // fast + accurate for structured JSON
const KDD_REQUIRED_FIELDS = [               // fields Claude must return (slim set)
  "kddId", "designQuestion", "fitGap", "complexity",
  "decisionOwner", "sapActivatePhase", "rationale", "impactActions", "notes"
];

// Catalog — in-memory cache; cleared whenever the file changes
let _catalog = null;
let _catalogChangedAt = null;   // set by file watcher when new catalog saved

function readCatalog() {
  if (!_catalog) {
    if (!fs.existsSync(CATALOG_PATH)) return null;
    _catalog = JSON.parse(fs.readFileSync(CATALOG_PATH, "utf8"));
  }
  return _catalog;
}
function clearCatalogCache() { _catalog = null; }

// Watch scope-catalog.json — fires when Chrome extension saves a fresh catalog
fs.watch(ROOT, (event, filename) => {
  if (filename === "scope-catalog.json" && event === "change") {
    _catalog = null;                          // force re-read on next request
    _catalogChangedAt = new Date().toISOString();
    console.log(`[catalog] scope-catalog.json updated at ${_catalogChangedAt}`);
  }
});

function ensureOutput() {
  const dir = path.join(ROOT, "output");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// KDD cache — loaded once on first use
let _cache = null;
function getCache() {
  if (_cache) return _cache;
  if (fs.existsSync(CACHE_PATH)) _cache = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));
  return _cache;
}

// Decision learning store — past approved decisions, injected into Claude prompt
// Reads across all client files so every project benefits from prior decisions
function getPastDecisions(scopeItemId) {
  try {
    return readAllDecisions().filter(d => d.scopeItemId === scopeItemId).slice(0, 5);
  } catch { return []; }
}

// ── catalog routes ────────────────────────────────────────────────────────────

app.get("/api/status", (req, res) => {
  try {
    const cat    = readCatalog();
    const cache  = getCache();
    const outDir = ensureOutput();
    const files  = fs.readdirSync(outDir).filter(f => f.endsWith(".xlsx"));
    const now    = new Date();
    const yy     = now.getFullYear() % 100;
    const mm     = now.getMonth() + 1;
    const expected = mm >= 8 ? `${yy}08` : `${yy}02`;
    res.json({
      catalogExists:   !!cat,
      catalogVersion:  cat ? cat.version  : null,
      catalogLoadedAt: cat ? cat.extractedAt : null,
      catalogChanged:  _catalogChangedAt,
      expectedRelease: expected,
      catalogOutdated: cat ? parseInt(cat.version) < parseInt(expected) : false,
      totalProcesses:  cat ? cat.processes.length : 0,
      outputCount:     files.length,
      cacheReady:      !!cache,
      cachedItems:     cache ? Object.keys(cache.items).length : 0,
      country:         cat ? (cat.country || "—") : "—"
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── catalog management ────────────────────────────────────────────────────────

// Detailed catalog info
app.get("/api/catalog/info", (req, res) => {
  if (!fs.existsSync(CATALOG_PATH)) {
    return res.json({ exists: false });
  }
  const cat   = readCatalog();
  const stat  = fs.statSync(CATALOG_PATH);
  const lobs  = {};
  cat.processes.forEach(p => { lobs[p.lob] = (lobs[p.lob] || 0) + 1; });
  res.json({
    exists:       true,
    version:      cat.version,
    extractedAt:  cat.extractedAt,
    country:      cat.country,
    fileSize:     stat.size,
    processes:    cat.processes.length,
    lobs:         Object.keys(lobs).length,
    changedAt:    _catalogChangedAt   // set by file watcher
  });
});

// Reload catalog from disk (after Chrome extension saves new data) — SSE stream
app.post("/api/catalog/reload", (req, res) => {
  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.flushHeaders();

  const send = d => res.write(`data: ${JSON.stringify(d)}\n\n`);

  if (!fs.existsSync(CATALOG_PATH)) {
    send({ type: "error", message: "scope-catalog.json not found. Use the Chrome extension to load catalog first." });
    res.end(); return;
  }

  // Step 1 — re-read catalog
  clearCatalogCache();
  const cat = readCatalog();
  _catalogChangedAt = null;   // clear the "new file" flag
  send({ type: "progress", message: `Catalog loaded: ${cat.processes.length} processes · version ${cat.version}` });

  // Step 2 — rebuild KDD cache
  send({ type: "progress", message: `Rebuilding KDD cache for ${cat.processes.length} scope items…` });
  const script = path.join(ROOT, "kdd-generator", "pre-generate.js");
  exec(`node "${script}"`, { cwd: ROOT }, (err, stdout, stderr) => {
    if (err) {
      send({ type: "error", message: "Cache rebuild failed: " + (stderr || err.message) });
      res.end(); return;
    }
    _cache = null;   // clear in-memory so next read picks up fresh file
    const fresh = getCache();
    send({
      type:         "complete",
      version:      cat.version,
      processes:    cat.processes.length,
      cachedItems:  fresh ? Object.keys(fresh.items).length : 0,
      totalKDDs:    fresh ? Object.values(fresh.items).reduce((s, v) => s + v.rows.length, 0) : 0
    });
    res.end();
  });
});

// Upload a new scope-catalog.json — validates, backs up old, saves new, reloads
// Used by functional users who receive an updated catalog from the project team (no Chrome extension needed)
app.post("/api/catalog/upload", express.json({ limit: "10mb" }), (req, res) => {
  const data = req.body;
  // Basic validation
  if (!data || !Array.isArray(data.processes) || data.processes.length === 0) {
    return res.status(400).json({ ok: false, error: "Invalid catalog: must have a processes array." });
  }
  if (!data.version) {
    return res.status(400).json({ ok: false, error: "Invalid catalog: missing version field." });
  }
  // Back up existing catalog
  if (fs.existsSync(CATALOG_PATH)) {
    const backup = path.join(ROOT, `scope-catalog.backup.${Date.now()}.json`);
    fs.copyFileSync(CATALOG_PATH, backup);
  }
  // Save and reload
  fs.writeFileSync(CATALOG_PATH, JSON.stringify(data, null, 2), "utf8");
  clearCatalogCache();
  _catalogChangedAt = new Date().toISOString();
  const cat = readCatalog();
  res.json({ ok: true, version: cat.version, processes: cat.processes.length });
});

// Clear catalog — backs up current file then removes it
app.post("/api/catalog/clear", (req, res) => {
  if (!fs.existsSync(CATALOG_PATH)) {
    return res.json({ ok: true, message: "Catalog was already empty." });
  }
  const backup = path.join(ROOT, `scope-catalog.backup.${Date.now()}.json`);
  fs.renameSync(CATALOG_PATH, backup);
  clearCatalogCache();
  _cache = null;
  _catalogChangedAt = null;
  res.json({ ok: true, backup: path.basename(backup) });
});

app.get("/api/catalog/lobs", (req, res) => {
  const cat = readCatalog();
  if (!cat) return res.json([]);
  const counts = {};
  cat.processes.forEach(p => { counts[p.lob] = (counts[p.lob] || 0) + 1; });
  res.json(Object.keys(counts).sort().map(l => ({ name: l, count: counts[l] })));
});

app.get("/api/catalog/lob/:lob", (req, res) => {
  const cat = readCatalog();
  if (!cat) return res.json([]);
  const items = cat.processes
    .filter(p => p.lob === decodeURIComponent(req.params.lob))
    .map(p  => ({ id: p.id, name: p.name, lob: p.lob }))
    .sort((a, b) => a.name.localeCompare(b.name));
  res.json(items);
});

app.get("/api/catalog/item/:id", (req, res) => {
  const cat = readCatalog();
  if (!cat) return res.status(503).json({ error: "Catalog not loaded" });
  const item = cat.processes.find(p => p.id === req.params.id.toUpperCase());
  if (!item) return res.status(404).json({ error: "Not found" });
  res.json(item);
});

app.get("/api/catalog/search", (req, res) => {
  const q = (req.query.q || "").toLowerCase().trim();
  if (!q) return res.json([]);
  const cat = readCatalog();
  if (!cat) return res.json([]);
  const results = cat.processes
    .filter(p => p.id.toLowerCase().includes(q) || p.name.toLowerCase().includes(q))
    .slice(0, 25)
    .map(p => ({ id: p.id, name: p.name, lob: p.lob }));
  res.json(results);
});

// ── DDA file → scope ID extraction ───────────────────────────────────────────
// Accepts { filename, content: base64 }
// Extracts text from the file, matches every token against known scope item IDs,
// returns matched items (validated against catalog) + any unrecognised tokens.
app.post("/api/dda/extract", express.json({ limit: "50mb" }), (req, res) => {
  const { filename = "file", content } = req.body;
  if (!content) return res.status(400).json({ error: "No file content provided" });

  const cat = readCatalog();
  if (!cat) return res.status(503).json({ error: "Catalog not loaded" });

  const idMap = {};
  cat.processes.forEach(p => { idMap[p.id] = p; });

  let text = "";
  const ext = (filename.split(".").pop() || "").toLowerCase();
  const buf = Buffer.from(content, "base64");

  try {
    if (ext === "xlsx" || ext === "xls") {
      // Use xlsx from kdd-generator subfolder (already installed)
      try {
        const XLSX = require("./kdd-generator/node_modules/xlsx");
        const wb   = XLSX.read(buf, { type: "buffer" });
        wb.SheetNames.forEach(sheetName => {
          const ws = wb.Sheets[sheetName];
          text += XLSX.utils.sheet_to_csv(ws) + "\n";
        });
      } catch (_) {
        text = buf.toString("latin1");
      }
    } else {
      // PDF, DOCX, PPTX, TXT — latin1 decode exposes readable strings
      // (DOCX/PPTX are ZIP+XML; scope IDs appear as plain text in the XML)
      text = buf.toString("latin1");
    }
  } catch (_) {
    text = buf.toString("latin1");
  }

  // Uppercase the whole text and find every 2-6 char alphanumeric token
  // then match against the known ID set — exact match only, no guessing
  const upper  = text.toUpperCase();
  const tokens = upper.match(/\b[A-Z0-9]{2,6}\b/g) || [];
  const found  = new Set();
  tokens.forEach(t => { if (idMap[t]) found.add(t); });

  const matched = [...found].sort().map(id => idMap[id]);

  res.json({ matched, total: matched.length, filename });
});

// ── output routes ─────────────────────────────────────────────────────────────

app.get("/api/output", (req, res) => {
  const dir   = ensureOutput();
  // Only list .xlsx — kdd-data.json stays protected
  const files = fs.readdirSync(dir)
    .filter(f => f.endsWith(".xlsx"))
    .map(f => {
      const stat = fs.statSync(path.join(dir, f));
      return { name: f, size: stat.size, modified: stat.mtime };
    })
    .sort((a, b) => new Date(b.modified) - new Date(a.modified));
  res.json({ files, folder: path.join(ROOT, "output") });
});

app.get("/api/output/download/:file", (req, res) => {
  const file = path.join(ROOT, "output", path.basename(req.params.file));
  const allowed = file.endsWith(".xlsx") || file.endsWith(".pptx");
  if (!fs.existsSync(file) || !allowed) {
    return res.status(404).json({ error: "File not found" });
  }
  res.download(file);
});

// Delete one Excel file — kdd-data.json is explicitly protected
app.delete("/api/output/:file", (req, res) => {
  const name = path.basename(req.params.file);
  if (!name.endsWith(".xlsx")) {
    return res.status(400).json({ error: "Only .xlsx files can be deleted." });
  }
  const file = path.join(ROOT, "output", name);
  if (!fs.existsSync(file)) return res.status(404).json({ error: "File not found" });
  fs.unlinkSync(file);
  res.json({ ok: true, deleted: name });
});

// Delete all Excel files — leaves kdd-data.json untouched
app.delete("/api/output", (req, res) => {
  const dir   = ensureOutput();
  const files = fs.readdirSync(dir).filter(f => f.endsWith(".xlsx"));
  files.forEach(f => fs.unlinkSync(path.join(dir, f)));
  res.json({ ok: true, deleted: files.length });
});

// ── Cache rebuild ─────────────────────────────────────────────────────────────

app.post("/api/cache/rebuild", (req, res) => {
  const script = path.join(ROOT, "kdd-generator", "pre-generate.js");
  exec(`node "${script}"`, { cwd: ROOT }, (err, stdout, stderr) => {
    if (err) return res.status(500).json({ error: stderr || err.message });
    _cache = null; // clear in-memory cache so next request reloads from disk
    const cache = getCache();
    res.json({
      ok:          true,
      cachedItems: cache ? Object.keys(cache.items).length : 0,
      totalKDDs:   cache ? Object.values(cache.items).reduce((s, v) => s + v.rows.length, 0) : 0
    });
  });
});

// ── KDD retry — regenerate one scope item without touching others ─────────────
// Preserves any workshop outcome fields already filled in for that item.
app.post("/api/kdd/retry/:id", async (req, res) => {
  const id = req.params.id.toUpperCase();

  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection",    "keep-alive");
  res.flushHeaders();

  const send = d => res.write(`data: ${JSON.stringify(d)}\n\n`);

  const cat = readCatalog();
  if (!cat) { send({ type: "error", item: id, message: "Catalog not loaded" }); res.end(); return; }

  const item = cat.processes.find(p => p.id === id);
  if (!item) { send({ type: "error", item: id, message: `${id} not found in catalog` }); res.end(); return; }

  send({ type: "progress", item: id, name: item.name, message: `Asking Claude to regenerate KDDs for ${item.name}…` });

  let rows;
  let source = "claude";

  try {
    rows = await generateWithClaude(item);
  } catch (claudeErr) {
    const cache = getCache();
    if (cache && cache.items[id]) {
      rows   = cache.items[id].rows;
      source = "cache";
      send({ type: "progress", item: id, message: "Claude unavailable — using cache fallback" });
    } else {
      send({ type: "error", item: id, message: claudeErr.message.slice(0, 120) });
      res.end(); return;
    }
  }

  // Patch kdd-data.json — replace only this item's rows, preserve workshop fields
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  try {
    if (fs.existsSync(jsonPath)) {
      const data = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
      const WORKSHOP_FIELDS = ["workshopDate","decidedBy","signOffStatus","actionItems",
                               "bpdRef","decisionMade","decisionOwner","status","sapConsultant"];
      const prevById = {};
      (data.rows || []).filter(r => r.scopeItemId === id).forEach(r => { prevById[r.kddId] = r; });
      rows = rows.map(r => {
        const prev = prevById[r.kddId];
        if (!prev) return r;
        const merged = { ...r };
        WORKSHOP_FIELDS.forEach(f => { if (prev[f]) merged[f] = prev[f]; });
        return merged;
      });
      data.rows = (data.rows || []).filter(r => r.scopeItemId !== id).concat(rows);
      if (data.meta && Array.isArray(data.meta.overviews)) {
        const newOv = buildOverview(item, cat.version);
        data.meta.overviews = data.meta.overviews.filter(o => o.id !== id);
        data.meta.overviews.push(newOv);
      }
      fs.writeFileSync(jsonPath, JSON.stringify(data, null, 2), "utf8");
      snapshotProject(data);   // keep projects/ copy in sync
    }
  } catch (patchErr) {
    send({ type: "error", item: id, message: "Patch failed: " + patchErr.message });
    res.end(); return;
  }

  send({ type: "item_done", item: id, name: item.name, count: rows.length, source });
  send({ type: "complete", item: id });
  res.end();
});

// ── Project store ─────────────────────────────────────────────────────────────
// Each client+project is saved as output/projects/<slug>.json
// kdd-data.json remains the "active" working copy — unchanged by rest of app

const PROJECTS_DIR = path.join(ROOT, "output", "projects");
if (!fs.existsSync(PROJECTS_DIR)) fs.mkdirSync(PROJECTS_DIR, { recursive: true });

function projectSlug(client, project) {
  const slugify = s => (s || "untitled").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "untitled";
  return `${slugify(client)}--${slugify(project)}`;
}

function snapshotProject(data) {
  // Save a copy of kdd-data to projects/<slug>.json
  if (!data || !data.meta) return;
  const slug = projectSlug(data.meta.client, data.meta.project);
  const dest = path.join(PROJECTS_DIR, `${slug}.json`);
  fs.writeFileSync(dest, JSON.stringify({ ...data, _slug: slug }, null, 2), "utf8");
  return slug;
}

// List all saved projects
app.get("/api/projects", (req, res) => {
  try {
    if (!fs.existsSync(PROJECTS_DIR)) return res.json({ projects: [] });
    const KDD_PATH = path.join(ROOT, "output", "kdd-data.json");
    const active   = fs.existsSync(KDD_PATH)
      ? (JSON.parse(fs.readFileSync(KDD_PATH, "utf8")).meta || {})
      : {};
    const activeSlug = projectSlug(active.client, active.project);

    const projects = fs.readdirSync(PROJECTS_DIR)
      .filter(f => f.endsWith(".json"))
      .map(f => {
        try {
          const d    = JSON.parse(fs.readFileSync(path.join(PROJECTS_DIR, f), "utf8"));
          const meta = d.meta || {};
          const slug = d._slug || f.replace(".json", "");
          return {
            slug,
            client:    meta.client  || "—",
            project:   meta.project || "—",
            rows:      (d.rows || []).length,
            savedAt:   meta.lastEditedAt || meta.generatedAt || null,
            active:    slug === activeSlug
          };
        } catch { return null; }
      })
      .filter(Boolean)
      .sort((a, b) => (b.savedAt || "").localeCompare(a.savedAt || ""));
    res.json({ projects });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Load a saved project → becomes the active kdd-data.json
app.post("/api/projects/load", express.json(), (req, res) => {
  const { slug } = req.body;
  if (!slug) return res.status(400).json({ error: "slug required" });
  const src = path.join(PROJECTS_DIR, `${slug}.json`);
  if (!fs.existsSync(src)) return res.status(404).json({ error: "Project not found" });
  try {
    const data = JSON.parse(fs.readFileSync(src, "utf8"));
    data.meta  = data.meta || {};
    data.meta.lastEditedAt = new Date().toISOString();
    fs.writeFileSync(path.join(ROOT, "output", "kdd-data.json"),
      JSON.stringify(data, null, 2), "utf8");
    res.json({ ok: true, client: data.meta.client, project: data.meta.project,
               rows: (data.rows || []).length });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Delete a saved project
app.delete("/api/projects/:slug", (req, res) => {
  const slug = path.basename(req.params.slug);
  const file = path.join(PROJECTS_DIR, `${slug}.json`);
  if (!fs.existsSync(file)) return res.status(404).json({ error: "Not found" });
  try {
    fs.unlinkSync(file);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── KDD Editor endpoints ──────────────────────────────────────────────────────

// Return current kdd-data.json for the editor
app.get("/api/kdd/data", (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data yet. Generate KDDs first." });
  }
  try {
    res.json(JSON.parse(fs.readFileSync(jsonPath, "utf8")));
  } catch (e) {
    res.status(500).json({ error: "Could not read KDD data: " + e.message });
  }
});

// Save edited rows back to kdd-data.json
app.put("/api/kdd/data", express.json({ limit: "20mb" }), (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  const payload  = req.body;
  if (!payload || !Array.isArray(payload.rows)) {
    return res.status(400).json({ error: "Invalid payload — rows array required." });
  }
  try {
    ensureOutput();
    payload.meta = payload.meta || {};
    payload.meta.lastEditedAt = new Date().toISOString();
    fs.writeFileSync(jsonPath, JSON.stringify(payload, null, 2), "utf8");
    snapshotProject(payload);   // keep projects/ copy in sync
    res.json({ ok: true, rowCount: payload.rows.length, savedAt: payload.meta.lastEditedAt });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Rebuild Excel from current kdd-data.json (after user edits)
app.post("/api/kdd/export", (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data. Generate KDDs first." });
  }
  const cmd = `node "${path.join(ROOT, "kdd-generator", "excel-builder.js")}" --input "${jsonPath}"`;
  exec(cmd, { cwd: ROOT }, (err, stdout) => {
    if (err) return res.status(500).json({ error: err.message });
    const match    = stdout.match(/Excel written:\s*(.+\.xlsx)/);
    const filename = match ? path.basename(match[1].trim()) : null;
    appendRunLog({ ts: new Date().toISOString(), type: "export", filename });
    res.json({ ok: true, filename });
  });
});

// ── Export WRICEF Inventory to Excel ─────────────────────────────────────────
// Reads kdd-data.json, filters to WRICEF rows only, writes a focused Excel.
app.post("/api/kdd/export-wricef", (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data. Generate and save KDDs first." });
  }

  let kddData;
  try { kddData = JSON.parse(fs.readFileSync(jsonPath, "utf8")); }
  catch (e) { return res.status(500).json({ error: "Failed to parse kdd-data.json: " + e.message }); }

  const allRows = kddData.rows || [];
  const wricefRows = allRows.filter(r => r.gapResolution === "WRICEF" && r.ricefType && r.ricefType !== "None");

  if (!wricefRows.length) {
    return res.json({ ok: false, error: "No WRICEF items found. Set Resolution = WRICEF on Gap rows in the editor." });
  }

  const XLSX = require("./kdd-generator/node_modules/xlsx");
  const TYPE_LABELS = {
    W:"W – Workflow", R:"R – Report", I:"I – Interface", C:"C – Conversion",
    E_RAP:"E – Extension BTP – RAP", E_CAPM:"E – Extension BTP – CAPM",
    E_RAP_CAPM:"E – Extension BTP – RAP and CAPM & UI", F:"F – Form"
  };

  const meta     = kddData.meta || {};
  const today    = new Date().toISOString().slice(0, 10);
  const wb       = XLSX.utils.book_new();

  // ── Sheet 1: Cover ──
  const coverAoa = [
    ["WRICEF Inventory"],
    [""],
    ["Client",    meta.client  || "—"],
    ["Project",   meta.project || "—"],
    ["Generated", today],
    [""],
    ["Total WRICEF Items", wricefRows.length],
    ["Source",    "Fulcrum KDD Editor · SAP S/4HANA Cloud PE"],
    [""],
    ["Note", "This register lists all Gap items where the agreed resolution is a WRICEF object " +
             "(Workflow, Report, Interface, Conversion, Extension, Form). " +
             "All extensions use SAP BTP (RAP / CAPM) — no ABAP on-premise."]
  ];
  const wsCover = XLSX.utils.aoa_to_sheet(coverAoa);
  wsCover["!cols"] = [{ wch: 20 }, { wch: 80 }];
  XLSX.utils.book_append_sheet(wb, wsCover, "Cover");

  // ── Sheet 2: WRICEF Inventory ──
  const headers = [
    "WRICEF ID", "KDD ID", "Scope Item ID", "Scope Item Name",
    "Module", "Design Question",
    "WRICEF Type", "Complexity",
    "Extensibility", "BTP Required", "Effort",
    "Status", "Owner",
    "Decision Made", "Action Items", "BPD Reference"
  ];

  const dataRows = wricefRows.map(r => [
    r.ricefId          || "",
    r.kddId            || "",
    r.scopeItemId      || "",
    r.scopeItemName    || "",
    r.module || r.l1   || "",
    r.designQuestion   || "",
    TYPE_LABELS[r.ricefType] || r.ricefType || "",
    r.complexity       || "",
    r.extensibilityType && r.extensibilityType !== "None" ? r.extensibilityType : "",
    r.btpRequired      || "No",
    r.effortEstimate && r.effortEstimate !== "None" ? r.effortEstimate : "",
    r.status           || "Open",
    r.decisionOwner    || "",
    r.decisionMade     || "",
    r.actionItems      || "",
    r.bpdRef           || ""
  ]);

  const wsInv = XLSX.utils.aoa_to_sheet([headers, ...dataRows]);
  wsInv["!cols"] = headers.map(h => {
    const wide = ["Design Question","Decision Made","Action Items"].includes(h);
    return { wch: wide ? 60 : h.includes("ID") ? 14 : 22 };
  });
  wsInv["!freeze"] = { xSplit: 0, ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft", state: "frozen" };
  XLSX.utils.book_append_sheet(wb, wsInv, "WRICEF Inventory");

  // ── Write file ──
  const outputDir  = path.join(ROOT, "output");
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const safeClient  = (meta.client  || "Client").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 30);
  const safeProject = (meta.project || "Project").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 30);
  const filename    = `WRICEF_Inventory_${safeClient}_${safeProject}_${today}.xlsx`;
  const outPath     = path.join(outputDir, filename);
  XLSX.writeFile(wb, outPath);

  appendRunLog({ ts: new Date().toISOString(), type: "export-wricef", filename, rowCount: wricefRows.length });
  res.json({ ok: true, filename, rowCount: wricefRows.length });
});

// Export full cache — all 657 scope items → single Excel baseline
// Used the first time (or after a cache refresh) to produce the master KDD log
app.post("/api/kdd/export-full-cache", (req, res) => {
  const cache   = getCache();
  const catalog = getCatalog();
  if (!cache || !catalog) {
    return res.status(503).json({ error: "Cache or catalog not loaded." });
  }

  const { client = "All Scope Items", project = "Full Cache" } = req.body || {};

  // Build all rows from cache
  const allRows = [];
  const overviews = [];
  const catalogMap = {};
  catalog.processes.forEach(p => { catalogMap[p.id] = p; });

  for (const [id, item] of Object.entries(cache.items)) {
    const rows = (item.rows || []).filter(r => !r.needsRegeneration);
    allRows.push(...rows);
    const cat = catalogMap[id];
    if (cat) {
      const lines = (cat.description || "").split("\n").map(l => l.trim()).filter(Boolean);
      let ov = "", fl = "", bn = "", cur = null;
      for (const line of lines) {
        if (line === "Overview")          { cur = "ov"; continue; }
        if (line === "Key Process Flow")  { cur = "fl"; continue; }
        if (line === "Business Benefits") { cur = "bn"; continue; }
        if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
        if (cur === "ov") ov += (ov ? "\n" : "") + line;
        if (cur === "fl") fl += (fl ? "\n" : "") + line;
        if (cur === "bn") bn += (bn ? "\n" : "") + line;
      }
      overviews.push({
        id, name: cat.name,
        module: rows[0]?.module || "",
        lob: cat.lob,
        version: cache.version || "2608",
        overview: ov.slice(0, 500),
        keyProcessFlow: fl.slice(0, 500),
        businessBenefits: bn.slice(0, 300),
        source: "SAP for Me"
      });
    }
  }

  const today = new Date().toISOString().slice(0,10).replace(/-/g,"");
  const kddData = {
    rows: allRows,
    meta: {
      client, project,
      catalogVersion: cache.version || "2608",
      generatedAt: new Date().toISOString(),
      scopeCount: Object.keys(cache.items).length,
      overviews,
      agentPipeline: true
    }
  };

  const jsonPath = path.join(ROOT, "output", "kdd-data-full.json");
  const xlsxPath = path.join(ROOT, "output", `Accenture_KDD_FullCache_${today}.xlsx`);

  fs.writeFileSync(jsonPath, JSON.stringify(kddData, null, 2), "utf8");

  const cmd = `node "${path.join(ROOT, "kdd-generator", "excel-builder.js")}" --input "${jsonPath}" --output "${xlsxPath}"`;
  exec(cmd, { cwd: ROOT }, (err, stdout) => {
    if (err) return res.status(500).json({ error: err.message });
    const filename = path.basename(xlsxPath);
    appendRunLog({ ts: new Date().toISOString(), type: "export-full-cache", filename, rows: allRows.length });
    res.json({ ok: true, filename, rows: allRows.length, scopeItems: Object.keys(cache.items).length });
  });
});

// Quality critique — SSE — Claude scores each question: specific vs generic
app.post("/api/kdd/critique", async (req, res) => {
  const { rows } = req.body;
  if (!Array.isArray(rows) || !rows.length) {
    return res.status(400).json({ error: "rows array required" });
  }

  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection",    "keep-alive");
  res.flushHeaders();

  const send = d => res.write(`data: ${JSON.stringify(d)}\n\n`);

  // Group rows by scope item
  const byItem = {};
  rows.forEach(r => {
    if (!byItem[r.scopeItemId]) byItem[r.scopeItemId] = [];
    byItem[r.scopeItemId].push(r);
  });

  const allResults = [];

  for (const [itemId, itemRows] of Object.entries(byItem)) {
    send({ type: "progress", item: itemId,
           message: `Reviewing ${itemRows.length} questions for ${itemId}…` });

    const prompt = `You are a quality critic for SAP S/4HANA Cloud Public Edition KDD questions.

Scope item: ${itemId} — ${itemRows[0].scopeItemName}

Review these ${itemRows.length} design questions:
${itemRows.map((r, i) => `${i + 1}. [${r.kddId}] ${r.designQuestion}`).join("\n")}

Rate each question as either:
- "specific" — clearly tied to THIS process, mentions process-specific elements, would NOT apply verbatim to a different SAP process
- "generic" — could apply to any SAP process, lacks process-specific detail (e.g. "How will Fiori be configured?" with no process context)

Return ONLY a JSON array of exactly ${itemRows.length} objects:
[{"kddId":"...","rating":"specific"|"generic","reason":"one short sentence"}]

JSON only. No markdown.`;

    try {
      const scored = await new Promise((resolve, reject) => {
        let timedOut = false;
        const proc  = _claudeSpawn(CLAUDE_MODEL);
        proc.stdin.write(prompt, "utf8"); proc.stdin.end();
        const timer = setTimeout(() => { timedOut = true; proc.kill(); reject(new Error("Timeout")); }, CLAUDE_TIMEOUT_MS);
        let out = "";
        proc.stdout.on("data", d => { out += d; });
        proc.on("error", err => { clearTimeout(timer); reject(err); });
        proc.on("close", code => {
          clearTimeout(timer);
          if (timedOut) return;
          const s = out.indexOf("["), e = out.lastIndexOf("]");
          if (s < 0 || e < 0) return reject(new Error("No JSON array in response"));
          try { resolve(JSON.parse(out.slice(s, e + 1))); } catch (err) { reject(err); }
        });
      });
      allResults.push(...scored);
      const genericCount = scored.filter(s => s.rating === "generic").length;
      send({ type: "item_done", item: itemId, total: scored.length, generic: genericCount });
    } catch (err) {
      send({ type: "item_error", item: itemId, message: err.message.slice(0, 120) });
    }
  }

  send({ type: "complete", results: allResults });
  res.end();
});

// ── KDD generate — Claude Code is the AI runtime, SSE streams progress ────────
//
// Flow:
//   browser POST → server streams SSE events → browser updates live
//   For each scope item:
//     1. Read process description from scope-catalog.json (SAP for Me data)
//     2. Spawn  claude --print  with that data as context
//     3. Claude generates 15 process-specific KDD questions
//     4. Falls back to pre-generate cache if Claude CLI unavailable
//   When all items done → run excel-builder.js → send { type:"complete" }

app.post("/api/kdd/generate", async (req, res) => {
  const { client, project, scopeIds, mode = "fast" } = req.body;
  if (!client || !project || !scopeIds || !scopeIds.length) {
    return res.status(400).json({ error: "client, project, and scopeIds are required" });
  }

  // ── SSE setup ──────────────────────────────────────────────────────────────
  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection",    "keep-alive");
  res.flushHeaders();

  const send      = (data) => res.write(`data: ${JSON.stringify(data)}\n\n`);
  const startTime = Date.now();

  /* Name the run the way S4PC names a pipeline run, so runs.log entries can be
     referred to afterwards and a recorded lesson can point back at one. */
  const runId = agentRun.newRunId("KDD", client, project, ensureOutput());

  const cat   = readCatalog();
  const cache = getCache();

  if (!cat) {
    send({ type: "fatal", message: "Catalog not loaded. Upload scope-catalog.json first." });
    res.end(); return;
  }

  // Resolve scope IDs → catalog items (log unknowns)
  const items = scopeIds.map(rawId => {
    const id   = rawId.toUpperCase().trim();
    const item = cat.processes.find(p => p.id === id);
    if (!item) send({ type: "error", item: id, message: `${id} not found in catalog` });
    return item;
  }).filter(Boolean);

  if (!items.length) {
    send({ type: "fatal", message: "None of the scope IDs were found in the catalog." });
    res.end(); return;
  }

  // ── Agentic pipeline (agents/orchestrator.js) ──────────────────────────────
  // Orchestrator chains 5 agents per item:
  //   Cache → Library → Context → Generation → Validation
  // Each agent emits progress events via onEvent.
  // Cache hits short-circuit after stage 1 (no Claude needed).
  send({ type: "progress", message: `Starting ${mode === "fast" ? "cache" : "AI"} pipeline for ${items.length} scope item(s)…` });

  const { rows: allRows, overviews, srcCounts } = await orchestrator.run({
    items,
    cacheData:   cache,
    onEvent:     send,
    concurrency: 3,
    mode
  });

  if (!allRows.length) {
    send({ type: "fatal", message: "No KDDs generated — check scope IDs exist in the catalog." });
    res.end(); return;
  }

  // ── Build Excel ────────────────────────────────────────────────────────────
  send({ type: "progress", message: "Building Excel workbook…" });

  const outDir   = ensureOutput();
  const payload  = {
    rows: allRows,
    meta: {
      client, project,
      catalogVersion: cat.version,
      generatedAt:    new Date().toISOString(),
      scopeCount:     overviews.length,
      overviews,
      agentPipeline:  true   // flag: generated by agentic pipeline
    }
  };
  const jsonPath = path.join(outDir, "kdd-data.json");
  fs.writeFileSync(jsonPath, JSON.stringify(payload, null, 2), "utf8");

  const cmd = `node "${path.join(ROOT, "kdd-generator", "excel-builder.js")}" --input "${jsonPath}"`;
  exec(cmd, { cwd: ROOT }, (err, stdout) => {
    const durationMs = Date.now() - startTime;
    if (err) {
      appendRunLog({ ts: new Date().toISOString(), runId, agent: agentRun.AGENT_ID,
                     client, project, scopeIds,
                     status: "excel_error", totalKDDs: allRows.length, durationMs, sources: srcCounts });
      send({ type: "fatal", message: "Excel build failed: " + err.message });
    } else {
      const match    = stdout.match(/Excel written:\s*(.+\.xlsx)/);
      const filename = match ? path.basename(match[1].trim()) : null;
      appendRunLog({ ts: new Date().toISOString(), runId, agent: agentRun.AGENT_ID,
                     client, project, scopeIds,
                     status: "ok", filename, totalKDDs: allRows.length, durationMs,
                     sources: srcCounts, agentPipeline: true });
      send({ type: "complete", runId, filename, totalKDDs: allRows.length, sources: srcCounts });

      /* EMAIL NOTIFY — commented out
      if (filename) {
        const scopeList = scopeIds.join(", ");
        const detail    = `${scopeIds.length} scope item${scopeIds.length !== 1 ? "s" : ""}: ${scopeList}`;
        const nr = require("http").request(
          { hostname: "127.0.0.1", port: 8323, path: "/notify", method: "POST",
            headers: { "Content-Type": "application/json" } },
          () => {}
        );
        nr.on("error", () => {});
        nr.end(JSON.stringify({ type: "kdd", detail, claimedFile: filename }));
      }
      */
    }
    res.end();
  });
});

// ── AI Decision Suggestions ───────────────────────────────────────────────────
// On-demand — called from KDD Editor "💡 Suggest Decisions" button.
// Tier 1 (instant): if decisions.json has 3+ past decisions for this scope item, return them
//                   immediately — no Claude needed. Grows faster as library fills up.
// Tier 2 (Claude):  if fewer than 3 past decisions, call Claude with a short focused prompt.
//                   Stripped to bare minimum to reduce cold-start overhead.
app.post("/api/kdd/suggest", express.json({ limit: "512kb" }), (req, res) => {
  const { designQuestion, scopeItemId, scopeItemName, fitGap, rationale, notes, l1, l2 } = req.body;
  if (!designQuestion) return res.status(400).json({ error: "designQuestion required" });

  // ── Tier 1: instant suggestions from past approved decisions ─────────────────
  const past = getPastDecisions(scopeItemId || "");

  if (past.length >= 3) {
    // Enough history — return immediately, no Claude spawn needed
    const suggestions = past.slice(0, 3).map(d => ({
      text:      d.decision,
      rationale: d.rationale || `Based on decision made for ${d.client || "a previous project"}`,
      tag:       d.fitGap === "Gap" ? "Custom Path" : d.fitGap === "Partial Fit" ? "Common Variation" : "SAP Standard",
      source:    "library"
    }));
    return res.json({ ok: true, suggestions, source: "library", libraryCount: past.length });
  }

  // ── Tier 2: Claude — stripped-down prompt for minimum cold-start time ────────
  // Only include what Claude strictly needs — fewer tokens = faster response
  const pastBlock = past.length > 0
    ? `\nPast decisions for ${scopeItemId}:\n` +
      past.map(d => `• ${d.decision} [${d.fitGap || "?"}]`).join("\n")
    : "";

  const prompt = `SAP S/4HANA Cloud Public Edition consultant. Give exactly 3 decision options.

Question: ${designQuestion}
Scope: ${scopeItemId}${scopeItemName ? " — " + scopeItemName : ""}
Fit/Gap: ${fitGap || "?"}${notes ? "\nFiori app: " + notes : ""}${pastBlock}

Rules: Cloud PE only. Option 1=SAP standard, Option 2=client variation, Option 3=extensibility (Key User/BTP). Name specific Fiori app or BTP service. No ABAP/ECC/on-premise/SAP GUI.

JSON only — no markdown:
[{"text":"decision (2 sentences max)","rationale":"why (1 sentence)","tag":"SAP Standard|Common Variation|Custom Path"}]`;

  return new Promise((resolve) => {
    let timedOut   = false;
    let responded  = false;
    const reply = (fn) => { if (!responded) { responded = true; fn(); } };

    const proc  = _claudeSpawn(CLAUDE_MODEL);
    proc.stdin.write(prompt, "utf8"); proc.stdin.end();
    const timer = setTimeout(() => { timedOut = true; proc.kill(); }, 90_000);
    let out = "";
    proc.stdout.on("data", d => { out += d; });
    proc.on("close", () => {
      clearTimeout(timer);
      if (timedOut) { reply(() => res.status(504).json({ error: "Claude timed out — try again" })); return resolve(); }
      reply(() => {
        try {
          const s = out.indexOf("["), e = out.lastIndexOf("]");
          if (s < 0 || e < 0) throw new Error("no JSON array in response");
          res.json({ ok: true, suggestions: JSON.parse(out.slice(s, e + 1)), source: "claude" });
        } catch (err) {
          res.status(500).json({ error: "Parse failed: " + err.message });
        }
      });
      resolve();
    });
    proc.on("error", err => {
      clearTimeout(timer);
      const msg = err.code === "ENOENT"
        ? "Claude CLI not found. Install it from claude.ai/code and confirm `claude --version` works in a terminal, then restart START.bat. If it's in a custom location, set the CLAUDE_PATH environment variable to the full path of claude.exe."
        : err.message;
      reply(() => res.status(503).json({ error: msg }));
      resolve();
    });
  });
});

// ── PPT export ────────────────────────────────────────────────────────────────
// Runs ppt-builder.js on the current kdd-data.json — same pattern as /api/kdd/export
app.post("/api/kdd/ppt", (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data yet. Generate KDDs first." });
  }
  const cmd = `node "${path.join(ROOT, "kdd-generator", "ppt-builder.js")}" --input "${jsonPath}"`;
  exec(cmd, { cwd: ROOT }, (err, stdout) => {
    if (err) return res.status(500).json({ error: "PPT build failed: " + err.message });
    const match = stdout.match(/PowerPoint written:\s*(.+)/);
    const filename = match ? path.basename(match[1].trim()) : null;
    res.json({ ok: true, filename });
  });
});

// ── Claude CLI generation ─────────────────────────────────────────────────────
// Spawns  claude --print  with the SAP process description as context.
// Claude Code is the LLM runtime — no external API key needed.

const LOB_MODULE = { Finance:"FIN", Sales:"SD", Procurement:"MM",
                     Manufacturing:"PP", "Human Resources":"HCM" };

// Validate Claude's output before accepting it — rejects on wrong count or missing fields
function validateKDDRows(rows) {
  if (!Array.isArray(rows) || rows.length !== 15) {
    throw new Error(`Expected 15 rows, got ${Array.isArray(rows) ? rows.length : typeof rows}`);
  }
  for (let i = 0; i < rows.length; i++) {
    const missing = KDD_REQUIRED_FIELDS.filter(f => !rows[i][f]);
    if (missing.length) throw new Error(`Row ${i + 1} missing field(s): ${missing.join(", ")}`);
  }
}

// Fill fields Claude no longer returns — keeps Excel complete without bloating the prompt
function fillDefaults(rows, item, mod) {
  return rows.map((r, i) => ({
    kddId:           r.kddId || `${item.id}-KDD-${String(i + 1).padStart(3, "0")}`,
    scopeItemId:     item.id,
    scopeItemName:   item.name,
    designQuestion:  r.designQuestion  || "",
    fitGap:          r.fitGap          || "Fit",
    complexity:      r.complexity      || "Low",
    status:          "Open",
    module:          mod,
    l1:              r.l1              || "",
    l2:              r.l2              || "",
    l3:              "",
    l4:              "",
    decisionOwner:   r.decisionOwner   || "",
    sapActivatePhase:r.sapActivatePhase|| "Explore",
    rationale:       r.rationale       || "",
    impactActions:   r.impactActions   || "",
    notes:           r.notes           || "",
    decisionMade:    "",
    sapConsultant:   "",
    source:          "SAP for Me - Process Navigator",
    dataSource:      "SAP for Me",
  }));
}

// Append one JSONL line to output/runs.log — audit trail, never blocks
function appendRunLog(entry) {
  try {
    const logPath = path.join(ensureOutput(), "runs.log");
    fs.appendFileSync(logPath, JSON.stringify(entry) + "\n", "utf8");
  } catch (_) { /* non-fatal */ }
}

function parseDesc(raw) {
  // Split catalog description into Overview / Key Process Flow / Business Benefits sections.
  // Same logic as buildOverview — shared so Claude gets the same structured view as the Excel sheet.
  const lines = (raw || "").split("\n").map(l => l.trim()).filter(Boolean);
  const sec   = { overview: [], flow: [], benefits: [] };
  let cur = null;
  for (const line of lines) {
    if (line === "Overview")          { cur = "overview";  continue; }
    if (line === "Key Process Flow")  { cur = "flow";      continue; }
    if (line === "Business Benefits") { cur = "benefits";  continue; }
    if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
    if (cur) sec[cur].push(line);
  }
  // Budget per section: overview 300 chars, flow first 10 steps, benefits first 5 items
  const overview  = sec.overview.join(" ").slice(0, 300);
  const flow      = sec.flow.slice(0, 10).map((s, i) => `${i+1}. ${s}`).join("\n");
  const benefits  = sec.benefits.slice(0, 5).join("; ");
  return { overview, flow, benefits };
}

function generateWithClaude(item, retryCount = 0, allItems = []) {
  const mod = LOB_MODULE[item.lob] || "Cross";

  // Parse catalog description into clean sections — gives Claude Overview + full Key Process Flow + Benefits
  const { overview, flow, benefits } = parseDesc(item.description);
  const descBlock = [
    overview  && `Overview: ${overview}`,
    flow      && `Key Process Flow:\n${flow}`,
    benefits  && `Business Benefits: ${benefits}`
  ].filter(Boolean).join("\n\n");

  // Inject past approved decisions — grounds Claude's rationale in real project outcomes
  const pastDecisions = getPastDecisions(item.id);
  const pastBlock = pastDecisions.length > 0
    ? `\nApproved decisions from past projects for ${item.id} — reference these in rationale where relevant:\n` +
      pastDecisions.map(d => `• ${d.question} → Decision: ${d.decision}${d.fitGap ? ` [${d.fitGap}]` : ""}`).join("\n")
    : "";

  const prompt = `Generate exactly 15 KDD (Key Design Decision) questions for SAP S/4HANA Cloud Public Edition.

Scope item: ${item.id} — ${item.name}
LOB: ${item.lob} | Module: ${mod}

SAP process data (from SAP for Me — Process Navigator):
${descBlock}
${pastBlock}

Each question MUST: name a specific Fiori app or config object; describe a real business scenario; have multiple valid answers (not yes/no); be specific to THIS process only.

Cover these 10 categories (count in brackets):
[2] Fiori Config — name the exact app  [2] Master Data  [2] Approval/Workflow
[1] Outputs/Forms  [2] Integration  [1] Reporting/Analytics
[1] Authorisation  [2] Data Migration  [1] Org Structure  [1] Number Ranges

Cloud PE rules — NEVER mention: ABAP, ECC, R/3, SE16, SM30, SE38, SE80, user exit, BAdI, CMOD, SMOD, on-premise, private cloud, SAP GUI, SAPGUI, SM31, SM34, OBYC, backend custom.
Configuration only via SAP Fiori apps or SAP BTP.

Return ONLY a valid JSON array of exactly 15 objects. Each object must have exactly these 11 fields:
  kddId            "${item.id}-KDD-001" through "${item.id}-KDD-015"
  designQuestion   specific to this process — not a generic SAP question (max 150 chars)
  fitGap           "Fit" | "Partial Fit" | "Gap"
  complexity       "Low" | "Medium" | "High"
  decisionOwner    appropriate role (e.g. "Finance Lead", "IT Security Lead", "Plant Manager")
  sapActivatePhase "Explore" | "Realize" | "Deploy"
  rationale        1 sentence max 100 chars — why this matters for Cloud PE
  impactActions    1 sentence max 100 chars — consequence if not decided before go-live
  notes            Fiori app name or BTP service (max 80 chars)
  l1               top-level process area (e.g. "Finance", "Procurement", "Manufacturing")
  l2               sub-process area (e.g. "Accounts Payable", "Purchase Orders")

Return the JSON array only. No markdown fences, no explanation text.`;

  return new Promise((resolve, reject) => {
    let timedOut = false;

    // Prompt via stdin to avoid shell special-char mangling on Windows.
    const proc = _claudeSpawn(CLAUDE_MODEL);
    proc.stdin.write(prompt, "utf8"); proc.stdin.end();

    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill();
      reject(new Error(`Claude timed out after ${CLAUDE_TIMEOUT_MS / 1000}s for ${item.id}`));
    }, CLAUDE_TIMEOUT_MS);

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", d => { stdout += d; });
    proc.stderr.on("data", d => { stderr += d; });

    proc.on("error", err => {
      clearTimeout(timer);
      reject(new Error(`Claude CLI not found: ${err.message}`));
    });

    proc.on("close", code => {
      clearTimeout(timer);
      if (timedOut) return;   // already rejected above

      if (code !== 0) {
        return reject(new Error(`Claude exited ${code}: ${stderr.slice(0, 200)}`));
      }
      const start = stdout.indexOf("[");
      const end   = stdout.lastIndexOf("]");
      if (start < 0 || end < 0) {
        return reject(new Error("Claude response contained no JSON array"));
      }

      let rows;
      try {
        rows = JSON.parse(stdout.slice(start, end + 1));
      } catch (e) {
        // Parse failed — retry once before giving up
        if (retryCount === 0) {
          console.warn(`[kdd] ${item.id} JSON parse failed — retrying once`);
          return resolve(generateWithClaude(item, 1));
        }
        return reject(new Error(`JSON parse failed after retry: ${e.message}`));
      }

      // ── Schema validation — retry once if Claude returned wrong shape ──
      try {
        validateKDDRows(rows);
      } catch (valErr) {
        if (retryCount === 0) {
          console.warn(`[kdd] ${item.id} validation failed (${valErr.message}) — retrying once`);
          return resolve(generateWithClaude(item, 1, allItems));
        }
        return reject(new Error(`Schema invalid after retry: ${valErr.message}`));
      }

      // Fill in constant/derivable fields Claude no longer generates
      resolve(fillDefaults(rows, item, mod));
    });
  });
}

// ── RICEF classifier — second pass, Gap/Partial rows only, fast focused call ──
// Runs after generateWithClaude. Merges RICEF fields back into the row array.
function classifyRICEF(item, rows) {
  const targets = rows.filter(r => r.fitGap === "Gap" || r.fitGap === "Partial Fit");
  if (!targets.length) return Promise.resolve(rows);  // all Fit — nothing to classify

  const prompt = `You are an SAP S/4HANA Cloud Public Edition extensibility classifier.

Scope item: ${item.id} — ${item.name}

For each design question below, determine the likely RICEF object and extensibility path needed in Cloud PE.

Questions:
${targets.map((r, i) => `${i + 1}. [${r.kddId}] [${r.fitGap}] ${r.designQuestion}`).join("\n")}

Return ONLY a JSON array of exactly ${targets.length} objects, one per question in the same order:
[{
  "kddId": "...",
  "ricefTitle": "short descriptive title, e.g. Payment Tolerance Config — ${item.id}",
  "ricefType": "R" | "I" | "C" | "E" | "F" | "W" | "None",
  "extensibilityType": "Key User In-App" | "Developer ABAP Cloud" | "Side-by-Side BTP" | "None",
  "btpRequired": "Yes" | "No" | "Possible",
  "effortEstimate": "S" | "M" | "L" | "XL" | "None"
}]

RICEF types: R=Report/Analytics, I=Interface/Integration, C=Conversion/Migration, E=Enhancement/Config, F=Form/Output, W=Workflow
Extensibility: Key User In-App = config only; Developer ABAP Cloud = managed extension; Side-by-Side BTP = external app/service
Never suggest on-premise ABAP. Cloud PE only.
JSON array only. No markdown.`;

  return new Promise((resolve) => {
    let timedOut = false;
    const RICEF_TIMEOUT = 45_000;   // Haiku is fast — 45s plenty
    const proc  = _claudeSpawn(CLAUDE_MODEL);
    proc.stdin.write(prompt, "utf8"); proc.stdin.end();
    const timer = setTimeout(() => { timedOut = true; proc.kill(); }, RICEF_TIMEOUT);
    let out = "";
    proc.stdout.on("data", d => { out += d; });
    proc.on("close", () => {
      clearTimeout(timer);
      if (timedOut) { console.warn(`[ricef] ${item.id} timed out — skipping RICEF`); return resolve(rows); }
      try {
        const s = out.indexOf("["), e = out.lastIndexOf("]");
        if (s < 0 || e < 0) throw new Error("no array");
        const classified = JSON.parse(out.slice(s, e + 1));
        // Merge by kddId
        const byId = {};
        classified.forEach(c => { byId[c.kddId] = c; });
        const merged = rows.map(r => {
          const c = byId[r.kddId];
          if (!c) return { ...r, ricefTitle: "", ricefType: "None", extensibilityType: "None", btpRequired: "No", effortEstimate: "None" };
          return { ...r, ricefTitle: c.ricefTitle || "", ricefType: c.ricefType || "None",
                   extensibilityType: c.extensibilityType || "None",
                   btpRequired: c.btpRequired || "No", effortEstimate: c.effortEstimate || "None" };
        });
        resolve(merged);
      } catch (e) {
        console.warn(`[ricef] ${item.id} parse failed — skipping RICEF:`, e.message);
        resolve(rows);   // non-fatal: return rows without RICEF rather than failing the whole item
      }
    });
    proc.on("error", () => { clearTimeout(timer); resolve(rows); });  // non-fatal
  });
}

function buildOverview(item, version) {
  const desc  = item.description || "";
  const lines = desc.split("\n").map(l => l.trim()).filter(Boolean);
  const sec   = { overview: [], flow: [], benefits: [] };
  let cur = null;
  for (const line of lines) {
    if (line === "Overview")          { cur = "overview";  continue; }
    if (line === "Key Process Flow")  { cur = "flow";      continue; }
    if (line === "Business Benefits") { cur = "benefits";  continue; }
    if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
    if (cur && line.length > 3) sec[cur].push(line);
  }
  return {
    id:               item.id,
    name:             item.name,
    module:           LOB_MODULE[item.lob] || "Cross",
    lob:              item.lob,
    version,
    overview:         sec.overview.join(" "),
    keyProcessFlow:   sec.flow.map((s, i) => `${i+1}. ${s}`).join("\n"),
    businessBenefits: sec.benefits.map((s, i) => `${i+1}. ${s}`).join("\n"),
    source:           "SAP for Me"
  };
}

// ── Decision learning store ───────────────────────────────────────────────────
// decisions.json accumulates approved KDD decisions across projects.
// generateWithClaude() reads this file to enrich prompts with past outcomes.
// The editor calls POST /api/decisions/save when a KDD is marked Approved.

// Save or update one approved decision — written to decisions/<client-slug>.json
// client name is NOT stored in the entry itself (security); the file name is the identifier
app.post("/api/decisions/save", express.json({ limit: "512kb" }), (req, res) => {
  const { kddId, scopeItemId, scopeItemName, question, decision, rationale, fitGap, complexity, client, project } = req.body;
  if (!scopeItemId || !question || !decision) {
    return res.status(400).json({ error: "scopeItemId, question, and decision required" });
  }
  try {
    const decisions   = readClientDecisions(client);
    const existingIdx = kddId ? decisions.findIndex(d => d.kddId === kddId) : -1;
    const entry = sanitizeDecisionEntry({
      kddId:       kddId || `${(scopeItemId||"").toUpperCase()}-${Date.now()}`,
      scopeItemId: (scopeItemId || "").toUpperCase(),
      scopeName:   scopeItemName || "",
      question:    question   || "",
      decision:    decision   || "",
      rationale:   rationale  || "",
      fitGap:      fitGap     || "",
      complexity:  complexity || "",
      project:     project    || "",
      savedAt:     new Date().toISOString()
    });
    if (existingIdx >= 0) {
      decisions[existingIdx] = { ...decisions[existingIdx], ...entry, updatedAt: new Date().toISOString() };
    } else {
      decisions.push(entry);
    }
    writeClientDecisions(client, decisions);
    res.json({ ok: true, total: decisions.length, clientSlug: clientSlug(client) });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Sync approved decisions from kdd-data.json → decisions/<client-slug>.json
// Safe to call multiple times — upserts by kddId so no duplicates are created.
app.post("/api/decisions/sync", (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data found. Generate a KDD log first." });
  }
  try {
    const kddData = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
    const meta    = kddData.meta || {};
    const rows    = (kddData.rows || []).filter(r =>
      r.signOffStatus === "Approved" && r.decisionMade && r.decisionMade.trim()
    );

    if (!rows.length) {
      return res.json({ ok: true, synced: 0, total: 0,
        message: "No approved decisions with text found in current KDD data." });
    }

    const decisions = readClientDecisions(meta.client);
    let synced = 0;
    for (const r of rows) {
      const existingIdx = r.kddId ? decisions.findIndex(d => d.kddId === r.kddId) : -1;
      const entry = sanitizeDecisionEntry({
        kddId:       r.kddId || `${(r.scopeItemId||"").toUpperCase()}-${Date.now()}`,
        scopeItemId: (r.scopeItemId || "").toUpperCase(),
        scopeName:   r.scopeItemName  || "",
        question:    r.designQuestion || "",
        decision:    r.decisionMade   || "",
        rationale:   r.rationale      || "",
        fitGap:      r.fitGap         || "",
        complexity:  r.complexity     || "",
        project:     meta.project     || "",
        savedAt:     new Date().toISOString(),
        syncedFrom:  "kdd-data.json"
      });
      if (existingIdx >= 0) {
        decisions[existingIdx] = { ...decisions[existingIdx], ...entry };
      } else {
        decisions.push(entry);
        synced++;
      }
    }

    writeClientDecisions(meta.client, decisions);
    res.json({ ok: true, synced, total: decisions.length, clientSlug: clientSlug(meta.client),
      message: `Synced ${synced} new decision${synced !== 1 ? "s" : ""} (${decisions.length} total) for ${clientLabel(clientSlug(meta.client))}.` });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// List available client folders — used by Decision Library tabs
app.get("/api/decisions/clients", (req, res) => {
  try {
    if (!fs.existsSync(DECISIONS_DIR)) return res.json({ clients: [] });
    const clients = fs.readdirSync(DECISIONS_DIR)
      .filter(f => f.endsWith(".json"))
      .map(f => {
        const slug  = f.replace(".json", "");
        const items = (() => { try { return JSON.parse(fs.readFileSync(path.join(DECISIONS_DIR, f), "utf8")); } catch { return []; } })();
        return { slug, label: clientLabel(slug), count: items.length };
      })
      .filter(c => c.count > 0)
      .sort((a, b) => b.count - a.count);
    res.json({ clients });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Query decisions — optional ?clientSlug= to restrict to one client, ?scopeItemId=, ?q=
app.get("/api/decisions", (req, res) => {
  try {
    const { clientSlug: cs, scopeItemId, q } = req.query;
    const all = cs ? readClientDecisions(cs) : readAllDecisions();
    let results = all;
    if (scopeItemId) results = results.filter(d => d.scopeItemId === scopeItemId.toUpperCase());
    if (q) {
      const ql = q.toLowerCase();
      results = results.filter(d =>
        (d.question || "").toLowerCase().includes(ql) ||
        (d.decision  || "").toLowerCase().includes(ql)
      );
    }
    res.json({ decisions: results.slice(0, 200), total: all.length });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Summary stats — optional ?clientSlug= for per-client summary
app.get("/api/decisions/summary", (req, res) => {
  try {
    const { clientSlug: cs } = req.query;
    const all = cs ? readClientDecisions(cs) : readAllDecisions();
    if (!all.length) {
      return res.json({ total: 0, scopeItems: 0, projects: 0,
        byFitGap: { Fit: 0, "Partial Fit": 0, Gap: 0 }, scopeBreakdown: [] });
    }
    const byScope  = {};
    const byFitGap = { Fit: 0, "Partial Fit": 0, Gap: 0 };
    all.forEach(d => {
      byScope[d.scopeItemId] = (byScope[d.scopeItemId] || 0) + 1;
      if (d.fitGap && d.fitGap in byFitGap) byFitGap[d.fitGap]++;
    });
    res.json({
      total:      all.length,
      scopeItems: Object.keys(byScope).length,
      projects:   [...new Set(all.map(d => d.project).filter(Boolean))].length,
      byFitGap,
      scopeBreakdown: Object.entries(byScope)
        .map(([id, count]) => ({ id, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 10)
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Clear all decisions for a client (or all clients if no slug given)
app.delete("/api/decisions", (req, res) => {
  try {
    const { clientSlug: cs } = req.query;
    if (cs) {
      const file = path.join(DECISIONS_DIR, `${cs}.json`);
      if (fs.existsSync(file)) fs.unlinkSync(file);
      return res.json({ ok: true, message: `Cleared decisions for ${cs}` });
    }
    // Clear all client files
    if (fs.existsSync(DECISIONS_DIR)) {
      fs.readdirSync(DECISIONS_DIR)
        .filter(f => f.endsWith(".json"))
        .forEach(f => fs.unlinkSync(path.join(DECISIONS_DIR, f)));
    }
    res.json({ ok: true, message: "Cleared all decisions" });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Gap Analysis Agent ────────────────────────────────────────────────────────
// Genuinely agentic: 3 sequential steps where step 2's output informs step 3.
// Step 1 — local stats: instant, no Claude needed
// Step 2 — Claude call 1: cross-scope integration risk identification
// Step 3 — Claude call 2: gap prioritisation + recommended actions
// Each step streams an SSE event so the UI renders live progress.

app.post("/api/kdd/gap-analysis", async (req, res) => {
  const jsonPath = path.join(ROOT, "output", "kdd-data.json");
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: "No KDD data yet. Generate KDDs first." });
  }

  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection",    "keep-alive");
  res.flushHeaders();

  const send = d => res.write(`data: ${JSON.stringify(d)}\n\n`);

  let data;
  try { data = JSON.parse(fs.readFileSync(jsonPath, "utf8")); }
  catch (e) { send({ type: "error", message: "Could not read KDD data: " + e.message }); res.end(); return; }

  const rows = data.rows || [];
  const meta = data.meta || {};

  if (!rows.length) {
    send({ type: "error", message: "KDD data is empty — regenerate first." });
    res.end(); return;
  }

  // ── Step 1: Local analytics — no Claude ───────────────────────────────────
  send({ type: "step", step: 1, total: 3, label: "📊 Analysing KDD data…" });

  const ragFn = r => {
    if (r.fitGap === "Gap" && (r.complexity === "High" || r.complexity === "Medium")) return "Red";
    if (r.fitGap === "Gap" || (r.fitGap === "Partial Fit" && r.complexity !== "Low"))  return "Amber";
    return "Green";
  };

  const byScope = {};
  rows.forEach(r => {
    if (!byScope[r.scopeItemId]) byScope[r.scopeItemId] = { name: r.scopeItemName, lob: r.lob || "", rows: [] };
    byScope[r.scopeItemId].rows.push(r);
  });

  const scopeSummary = Object.entries(byScope).map(([id, s]) => {
    const ir   = s.rows;
    const gaps = ir.filter(r => r.fitGap === "Gap").length;
    const highs= ir.filter(r => r.complexity === "High").length;
    const reds = ir.filter(r => ragFn(r) === "Red").length;
    const ricef= [...new Set(ir.filter(r => r.ricefType && r.ricefType !== "None").map(r => r.ricefType))];
    return { id, name: s.name, lob: s.lob, total: ir.length, gaps, highs, reds, ricef };
  }).sort((a, b) => b.reds - a.reds || b.gaps - a.gaps);

  const stats = {
    totalKDDs:     rows.length,
    totalRed:      rows.filter(r => ragFn(r) === "Red").length,
    totalAmber:    rows.filter(r => ragFn(r) === "Amber").length,
    totalGap:      rows.filter(r => r.fitGap === "Gap").length,
    totalRICEF:    rows.filter(r => r.ricefType && r.ricefType !== "None").length,
    openDecisions: rows.filter(r => !r.decisionMade || !r.decisionMade.trim()).length,
    approved:      rows.filter(r => r.signOffStatus === "Approved").length,
    scopeCount:    scopeSummary.length
  };

  send({ type: "step_done", step: 1, stats, scopeSummary });

  // ── Step 2: Claude — cross-scope integration risks ────────────────────────
  send({ type: "step", step: 2, total: 3, label: "🔗 Identifying cross-scope integration risks (Claude)…" });

  const scopeTable = scopeSummary.map(s =>
    `${s.id} | ${s.name} | ${s.lob} | ${s.total} KDDs | ${s.gaps} gaps | ${s.highs} High | ${s.reds} Red | RICEF:[${s.ricef.join(",")||"none"}]`
  ).join("\n");

  const riskPrompt = `You are a SAP S/4HANA Cloud Public Edition delivery risk analyst.
Project: ${meta.client||"?"} — ${meta.project||"?"}

Scope items (ID | Name | LOB | Total KDDs | Gaps | High Complexity | Red RAG | RICEF):
${scopeTable}

Identify the 5 most important cross-scope integration risks — gaps or complexity in one scope item that create downstream risk in another.

Return ONLY a JSON array of exactly 5 objects:
[{"title":"risk title max 60 chars","scopeItems":["XX","YY"],"description":"what the risk is — 1 sentence","integrationPoint":"specific interface e.g. FI-SD billing sync","action":"Cloud PE mitigation — 1 sentence naming Fiori app or BTP service if relevant","severity":"High"|"Medium"|"Low"}]
JSON only. No markdown fences.`;

  let integrationRisks = [];
  try {
    integrationRisks = await new Promise((resolve, reject) => {
      let timedOut = false;
      const proc  = _claudeSpawn(CLAUDE_MODEL);
      proc.stdin.write(riskPrompt, "utf8"); proc.stdin.end();
      const timer = setTimeout(() => { timedOut = true; proc.kill(); reject(new Error("Claude timed out")); }, 90_000);
      let out = "";
      proc.stdout.on("data", d => { out += d; });
      proc.on("close", () => {
        clearTimeout(timer); if (timedOut) return;
        const s = out.indexOf("["), e = out.lastIndexOf("]");
        if (s < 0 || e < 0) return reject(new Error("No JSON array in response"));
        try { resolve(JSON.parse(out.slice(s, e + 1))); } catch (err) { reject(err); }
      });
      proc.on("error", err => { clearTimeout(timer); reject(err); });
    });
    send({ type: "step_done", step: 2, risks: integrationRisks });
  } catch (riskErr) {
    send({ type: "step_warn", step: 2, message: `Step skipped: ${riskErr.message.slice(0, 80)}` });
  }

  // ── Step 3: Claude — gap prioritisation + recommended actions ─────────────
  // Input enriched with step 2 risk context (scope items at risk feed into framing)
  send({ type: "step", step: 3, total: 3, label: "🎯 Prioritising gaps and generating recommendations (Claude)…" });

  const topGaps = rows
    .filter(r => r.fitGap === "Gap")
    .sort((a, b) => (b.complexity === "High" ? 2 : b.complexity === "Medium" ? 1 : 0) -
                    (a.complexity === "High" ? 2 : a.complexity === "Medium" ? 1 : 0))
    .slice(0, 12);

  // Risk scope items from step 2 inform the framing in step 3
  const atRiskItems = [...new Set(integrationRisks.flatMap(r => r.scopeItems || []))].join(", ") || "unknown";

  const gapPrompt = `You are a SAP S/4HANA Cloud Public Edition project advisor.
Project: ${meta.client||"?"} — ${meta.project||"?"}
Scope: ${scopeSummary.map(s => s.id).join(", ")}
Stats: ${stats.totalRed} Red RAG · ${stats.totalGap} gaps · ${stats.totalRICEF} RICEF items · ${stats.openDecisions} open decisions
Integration risk areas identified: ${atRiskItems}

Top unresolved gaps (Gap fit, sorted High→Medium complexity):
${topGaps.map((r, i) => `${i+1}. [${r.scopeItemId}] [${r.complexity}] ${r.designQuestion}`).join("\n")}

Group into 3-4 themes, prioritise by business impact, and name the Cloud PE resolution for each.

Return ONLY a JSON object:
{"themes":[{"theme":"name","priority":"Critical|High|Medium","items":[{"kddId":"...","question":"max 80 chars","approach":"Cloud PE resolution — 1 sentence"}]}],"topAction":"single most important action right now — 1-2 sentences","workshopFocus":"which scope items need urgent workshop focus and why — 1-2 sentences","healthSummary":"overall go-live readiness in 1 sentence"}
JSON only. No markdown.`;

  let gapAnalysis = null;
  try {
    gapAnalysis = await new Promise((resolve, reject) => {
      let timedOut = false;
      const proc  = _claudeSpawn(CLAUDE_MODEL);
      proc.stdin.write(gapPrompt, "utf8"); proc.stdin.end();
      const timer = setTimeout(() => { timedOut = true; proc.kill(); reject(new Error("Claude timed out")); }, 90_000);
      let out = "";
      proc.stdout.on("data", d => { out += d; });
      proc.on("close", () => {
        clearTimeout(timer); if (timedOut) return;
        const s = out.indexOf("{"), e = out.lastIndexOf("}");
        if (s < 0 || e < 0) return reject(new Error("No JSON object in response"));
        try { resolve(JSON.parse(out.slice(s, e + 1))); } catch (err) { reject(err); }
      });
      proc.on("error", err => { clearTimeout(timer); reject(err); });
    });
    send({ type: "step_done", step: 3, analysis: gapAnalysis });
  } catch (gapErr) {
    send({ type: "step_warn", step: 3, message: `Step skipped: ${gapErr.message.slice(0, 80)}` });
  }

  send({ type: "complete", stats, risks: integrationRisks, analysis: gapAnalysis });
  res.end();
});

// ── MCP Server visibility ─────────────────────────────────────────────────────
// These endpoints let the UI show what the MCP server sees and test tools live.
// The actual mcp-server.js process is started by Claude CLI via .claude/settings.json.
// Here we re-implement the same read-only logic so the browser can preview results.

const CLAUDE_SETTINGS_PATH = path.join(ROOT, ".claude", "settings.json");

app.get("/api/mcp/config", (req, res) => {
  const settingsExists  = fs.existsSync(CLAUDE_SETTINGS_PATH);
  const mcpServerExists = fs.existsSync(path.join(ROOT, "mcp-server.js"));
  const decisionsCount  = readAllDecisions().length;
  const cache = getCache();

  let registeredServers = [];
  if (settingsExists) {
    try {
      const cfg = JSON.parse(fs.readFileSync(CLAUDE_SETTINGS_PATH, "utf8"));
      registeredServers = Object.entries(cfg.mcpServers || {}).map(([name, srv]) => ({
        name,
        command: `${srv.command} ${(srv.args || []).join(" ")}`,
        cwd: srv.cwd || ROOT
      }));
    } catch { /* malformed */ }
  }

  res.json({
    settingsFound:    settingsExists,
    mcpServerFound:   mcpServerExists,
    registeredServers,
    dataSnapshot: {
      catalogItems:  (readCatalog()?.processes || []).length,
      cachedKDDs:    cache ? Object.values(cache.items || {}).reduce((s, v) => s + (Array.isArray(v.rows) ? v.rows.length : 0), 0) : 0,
      savedDecisions: decisionsCount
    },
    tools: [
      { name: "get_process_context",    icon: "🔍", desc: "SAP catalog data for any scope item",                       hint: "Reads scope-catalog.json" },
      { name: "search_past_decisions",  icon: "📖", desc: "Query the decision learning store",                          hint: "Reads decisions.json" },
      { name: "save_approved_decision", icon: "💾", desc: "Write an approved decision to the library",                  hint: "Writes decisions.json" },

      { name: "list_decisions_summary", icon: "📊", desc: "Stats: totals, fit/gap breakdown, projects covered",         hint: "Reads decisions.json" }
    ]
  });
});

// Live tool invocation — same logic as mcp-server.js so the UI can "Try" any tool
app.post("/api/mcp/invoke", express.json({ limit: "512kb" }), (req, res) => {
  const { tool, args = {} } = req.body;
  if (!tool) return res.status(400).json({ error: "tool name required" });

  try {
    let result;
    const cat = readCatalog();

    if (tool === "get_process_context") {
      const id   = (args.scopeItemId || "").toUpperCase().trim();
      const item = (cat?.processes || []).find(p => p.id === id);
      if (!item) { result = { error: `${id} not found in catalog` }; }
      else {
        const lines = (item.description || "").split("\n").map(l => l.trim()).filter(Boolean);
        const sec   = { overview: [], flow: [], benefits: [] };
        let cur = null;
        for (const line of lines) {
          if (line === "Overview")          { cur = "overview";  continue; }
          if (line === "Key Process Flow")  { cur = "flow";      continue; }
          if (line === "Business Benefits") { cur = "benefits";  continue; }
          if (["Solution Capabilities","Industry Relevance","Diagrams","Accelerators"].includes(line)) { cur = null; continue; }
          if (cur) sec[cur].push(line);
        }
        result = {
          id: item.id, name: item.name, lob: item.lob,
          overview:        sec.overview.join(" ").slice(0, 400),
          keyProcessFlow:  sec.flow.slice(0, 12).map((s, i) => `${i+1}. ${s}`).join("\n"),
          businessBenefits:sec.benefits.slice(0, 6).join("; ")
        };
      }
    }

    else if (tool === "search_past_decisions") {
      const decisions = fs.existsSync(DECISIONS_PATH)
        ? JSON.parse(fs.readFileSync(DECISIONS_PATH, "utf8")) : [];
      const q = (args.query || "").toLowerCase();
      const results = decisions.filter(d => {
        const matchScope  = !args.scopeItemId || d.scopeItemId === args.scopeItemId.toUpperCase();
        const matchQuery  = !q ||
          (d.question  || "").toLowerCase().includes(q) ||
          (d.decision  || "").toLowerCase().includes(q);
        const matchFitGap = !args.fitGap || d.fitGap === args.fitGap;
        return (matchScope || matchQuery) && matchFitGap;
      });
      result = { found: results.length, totalInStore: decisions.length, decisions: results.slice(0, 10) };
    }

    else if (tool === "get_cached_kdds") {
      const cache = getCache();
      const id    = (args.scopeItemId || "").toUpperCase().trim();
      const entry = cache?.items?.[id];
      result = entry
        ? { found: entry.rows.length, kdds: entry.rows.slice(0, 15) }
        : { found: 0, note: `No cached KDDs for ${id}`, kdds: [] };
    }

    else if (tool === "list_decisions_summary") {
      const all = fs.existsSync(DECISIONS_PATH)
        ? JSON.parse(fs.readFileSync(DECISIONS_PATH, "utf8")) : [];
      const byScope  = {};
      const byFitGap = { Fit: 0, "Partial Fit": 0, Gap: 0 };
      all.forEach(d => {
        byScope[d.scopeItemId] = (byScope[d.scopeItemId] || 0) + 1;
        if (d.fitGap in byFitGap) byFitGap[d.fitGap]++;
      });
      result = { total: all.length, scopeItemsCovered: Object.keys(byScope).length,
                 byScope, fitGapBreakdown: byFitGap,
                 projects: [...new Set(all.map(d => d.project).filter(Boolean))] };
    }

    else {
      return res.status(400).json({ error: `Unknown tool: ${tool}` });
    }

    res.json({ ok: true, tool, args, result, invokedAt: new Date().toISOString() });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── BDCQ Agent routes ─────────────────────────────────────────────────────────
// All BDCQ logic lives in bdcq-agent/ — this is the only line needed here.
const agentRun = require("./agent-run");
app.use("/api/bdcq", require("./bdcq-agent/routes"));

/* Record a distilled lesson against a run, into the brain's shared experience
   store (L3). Inert unless S4PC_MCP_URL is set, so a host that has not been
   told where the brain lives behaves exactly as before.

   Deliberately a call someone makes, not something that fires on every run:
   see agent-run.js for why counts are not lessons. */
app.post("/api/experience", express.json({ limit: "64kb" }), async (req, res) => {
  const { topic, lesson, impact, tags, runId, category } = req.body || {};
  if (!topic || !lesson) {
    return res.status(400).json({ ok: false, error: "topic and lesson are required" });
  }
  const status = await agentRun.recordExperience({ topic, lesson, impact, tags, runId, category });
  // 200 even when the write failed: the caller asked us to try, and the status
  // says what happened. A 500 here would invite a retry loop against a brain
  // that is simply not configured.
  res.json({ ok: status === "recorded", status, agent: agentRun.AGENT_ID, runId: runId || null });
});
/* Self-description, so the dashboard registry can point at this service
   instead of keeping its own copy of what these agents are. Same contract
   S4PC Catalyst serves at /api/agent-manifest. */
app.use("/api/agent-manifest", require("./agent-manifest"));

// ── SPA fallback ──────────────────────────────────────────────────────────────

app.get("*", (req, res) => {
  res.sendFile(path.join(ROOT, "public", "index.html"));
});

// ── Global error handler — always returns JSON, never HTML ────────────────────
// Prevents "Unexpected token '<'" errors in the browser when a route crashes.
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  console.error("[server error]", err.message);
  const status = err.status || err.statusCode || 500;
  res.status(status).json({ error: err.message || "Internal server error" });
});

// ── start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, "127.0.0.1", () => {
  console.log(`\n╔═══════════════════════════════════════════════╗`);
  console.log(`║  Fulcrum · SAP S/4HANA Cloud PE               ║`);
  console.log(`║  http://127.0.0.1:${PORT}                      ║`);
  console.log(`╚═══════════════════════════════════════════════╝\n`);
});
