// app.js — Fulcrum · SAP S/4HANA Cloud PE
"use strict";

// ── Utility ───────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── API helpers ───────────────────────────────────────────────────────────────
const api = {
  get:    url       => fetch(url).then(r => r.json()),
  post:   (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json()),
  delete: url       => fetch(url, { method: "DELETE" }).then(r => r.json()),
};

// ── Router ────────────────────────────────────────────────────────────────────
const routes = {
  home:               pagHome,
  "bdcq-agent":       pagBDCQ,
  "kdd-generator":    pagKDD,
  catalog:            pagCatalog,
  reference:          pagReference,
  output:             pagOutput,
  editor:             pagEditor,
  "gap-analysis":     pagGapAnalysis,
  "decision-library": pagDecisionLibrary,
  "mcp-server":       pagMCPServer,
  settings:           pagSettings,
};

function getPage() {
  return location.hash.replace("#", "") || "home";
}

function navigate(page) {
  location.hash = page;
}

async function render() {
  const page = getPage();
  document.querySelectorAll(".nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.page === page);
  });
  const fn = routes[page] || pagHome;
  const el = document.getElementById("app");
  el.innerHTML = `<div class="loader"><div class="spinner"></div> Loading…</div>`;
  try {
    el.innerHTML = await fn();
    bindPage(page);
  } catch (e) {
    el.innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
  }
}

window.addEventListener("hashchange", render);
document.addEventListener("DOMContentLoaded", render);

// ── Bind page-specific logic ──────────────────────────────────────────────────
function bindPage(page) {
  if (page === "bdcq-agent")       bindBDCQ();
  if (page === "kdd-generator")    bindKDD();
  if (page === "catalog")          bindCatalog();
  if (page === "settings")         bindSettings();
  if (page === "output")           bindOutput();
  if (page === "editor")           bindEditor();
  if (page === "gap-analysis")     bindGapAnalysis();
  if (page === "decision-library") bindDecisionLibrary();
  if (page === "mcp-server")       bindMCPServer();
}

// ── Module short-code → full LOB name (global, used by editor + KDD Analysis) ──
const MODULE_NAMES = {
  FIN:"Finance", SD:"Sales", MM:"Sourcing and Procurement",
  PP:"Manufacturing", PM:"Asset Management", HCM:"Human Resources",
  SCM:"Supply Chain", CS:"Service", SM:"Service",
  IT:"IT Management", RE:"R&D and Engineering",
  PS:"Professional Services", LE:"Logistics",
  DM:"Data Management",
  CO:"Finance", TR:"Finance",   // Controlling / Treasury roll into Finance
  QM:"Manufacturing", WM:"Supply Chain", EWM:"Supply Chain",
  TM:"Supply Chain", IM:"Supply Chain",
  Cross:"Cross-Module", "Cross-Module":"Cross-Module"
};
// r.l1 is set by the generator (full name); fall back to the map, then raw value
const modName = r => (r && r.l1) || MODULE_NAMES[r && r.module] || (r && r.module) || "—";

// ── HOME page ─────────────────────────────────────────────────────────────────
async function pagHome() {
  const [s, lib] = await Promise.all([
    api.get("/api/status"),
    api.get("/api/decisions/summary").catch(() => ({ total: 0, scopeItems: 0, projects: 0, byFitGap: { Fit: 0, "Partial Fit": 0, Gap: 0 } }))
  ]);

  const outdatedAlert = s.catalogOutdated
    ? `<div class="alert alert-warning">⚠️ Catalog is version <strong>${s.catalogVersion}</strong> — expected <strong>${s.expectedRelease}</strong>. Update via the Chrome extension.</div>`
    : "";
  const cacheAlert = !s.cacheReady
    ? `<div class="alert alert-warning">⚠️ KDD cache not built yet. <a href="#settings" style="color:var(--accent);font-weight:600">Build it in Settings →</a> (takes ~2 seconds)</div>`
    : "";

  const libNote = lib.total > 0
    ? `${lib.scopeItems} scope items · ${lib.projects} project${lib.projects !== 1 ? "s" : ""}`
    : "No decisions saved yet";

  return `
    <div class="page-header">
      <div class="page-title">Home</div>
      <div class="page-sub">SAP S/4HANA Cloud Public Edition · Explore Phase</div>
    </div>

    ${outdatedAlert}
    ${cacheAlert}

    <div class="stats-grid">
      <div class="card">
        <div class="card-title">Catalog Version</div>
        <div class="card-value">${s.catalogVersion}</div>
        <div class="card-note">${s.catalogOutdated ? "⚠️ Update available" : "✅ Up to date"}</div>
      </div>
      <div class="card">
        <div class="card-title">Scope Items</div>
        <div class="card-value">${s.totalProcesses.toLocaleString()}</div>
        <div class="card-note">SAP processes in catalog</div>
      </div>

      <div class="card" style="cursor:pointer" onclick="navigate('decision-library')">
        <div class="card-title">📚 Decision Library</div>
        <div class="card-value">${lib.total.toLocaleString()}</div>
        <div class="card-note">${libNote}</div>
      </div>
    </div>

    <div class="section-divider">Quick Actions</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn btn-primary" onclick="navigate('kdd-generator')">◈ Generate KDD Log</button>
      <button class="btn btn-outline" onclick="navigate('catalog')">⊞ Browse Scope Catalog</button>
      <button class="btn btn-outline" onclick="navigate('decision-library')">📚 Decision Library</button>
      <button class="btn btn-outline" onclick="navigate('output')">↓ View Output Files</button>
    </div>

    <div class="section-divider" style="margin-top:28px">Explore Phase Agents</div>
    <div class="agent-list">
      ${agentCard(1,"BDCQ Agent","Automates retrieval and processing of BDCQ accelerator content from SAP Roadmap Viewer into structured outputs.","Active","Explore","navigate('bdcq-agent')")}
      ${agentCard(2,"Fit-to-Standard Material Generation","Auto-generates fit-to-standard workshop decks, process overviews, and attendee pre-read packs from the scope catalog.","Coming Soon","Explore",null)}
      ${agentCard(3,"KDD Creation","Generates 15 Cloud PE-compliant KDD questions per scope item and exports to a 5-sheet Excel file.","Active","SAP Activate","navigate('kdd-generator')")}
      ${agentCard(4,"Business Process Design L1 to L5","Builds a structured process hierarchy (L1–L5) from scope catalog data, ready for workshop facilitation.","Coming Soon","Explore",null)}
    </div>`;
}

function skillCard(icon, id, name, desc, status, tag, iconSize) {
  const isActive = status === "Active";
  const statusClass = isActive ? "badge-green" : "badge-gray";
  const dimClass   = isActive ? "" : " skill-card--dimmed";
  return `
    <div class="skill-card${dimClass}">
      <div class="skill-card-header">
        <div class="skill-card-icon" style="font-size:${iconSize}">${icon}</div>
        <div class="skill-card-name">${name}</div>
      </div>
      <div class="skill-card-desc">${desc}</div>
      <div class="skill-card-footer">
        <span class="badge ${statusClass}">${status}</span>
        <span class="badge badge-purple">${tag}</span>
        <span class="text-sm text-muted" style="margin-left:auto">${id}</span>
      </div>
    </div>`;
}

// ── Vertical agent card (home page & skills page) ─────────────────────────────
function agentCard(step, name, desc, status, tag, onclick) {
  const isActive = status === "Active";
  const dimClass = isActive ? "" : " agent-card--dimmed";
  const statusClass = isActive ? "badge-green" : "badge-gray";
  const action = isActive && onclick
    ? `<button class="btn btn-primary" style="font-size:12px;padding:6px 14px" onclick="${onclick}">Open →</button>`
    : `<span class="badge badge-gray" style="font-size:11px">Coming Soon</span>`;
  return `
    <div class="agent-card${dimClass}">
      <div class="agent-num">${step}</div>
      <div class="agent-card-body">
        <div class="agent-card-name">${name}</div>
        <div class="agent-card-desc">${desc}</div>
        <div class="agent-card-meta">
          <span class="badge ${statusClass}">${status}</span>
          <span class="badge badge-purple">${tag}</span>
        </div>
      </div>
      <div class="agent-card-action">${action}</div>
    </div>`;
}

// ── CATALOG page ──────────────────────────────────────────────────────────────
async function pagCatalog() {
  const lobs = await api.get("/api/catalog/lobs");
  const lobList = lobs.map(l =>
    `<button class="lob-btn" data-lob="${l.name}" onclick="selectLOB('${encodeURIComponent(l.name)}')">
      ${l.name}
      <span class="lob-count">${l.count} items</span>
    </button>`
  ).join("");

  return `
    <div class="page-header">
      <div class="page-title">Scope Catalog</div>
      <div class="page-sub">Browse ${lobs.reduce((s,l)=>s+l.count,0)} SAP processes · version from status</div>
    </div>

    <div class="search-wrap">
      <span class="search-icon">⌕</span>
      <input id="cat-search" class="form-input" placeholder="Search by scope item ID or name (e.g. BD6 or Accounts)…" oninput="doCatSearch(this.value)" />
    </div>

    <div id="search-results"></div>

    <div class="section-divider">Browse by Line of Business</div>
    <div class="lob-grid" id="lob-grid">${lobList}</div>

    <div id="lob-items"></div>`;
}

function bindCatalog() {
  window.selectLOB = async (lobEnc) => {
    const lob = decodeURIComponent(lobEnc);
    document.querySelectorAll(".lob-btn").forEach(b => b.classList.toggle("selected", b.dataset.lob === lob));
    const el  = document.getElementById("lob-items");
    el.innerHTML = `<div class="loader"><div class="spinner"></div> Loading ${lob}…</div>`;
    const items = await api.get(`/api/catalog/lob/${encodeURIComponent(lob)}`);
    el.innerHTML = `
      <div class="section-divider">${lob} — ${items.length} items</div>
      <div class="card" style="padding:0;overflow:hidden">
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Name</th><th>LOB</th></tr></thead>
            <tbody>${items.map(i => `
              <tr style="cursor:pointer" onclick="showItemDetail('${i.id}')">
                <td><span class="bold" style="color:var(--accent)">${i.id}</span></td>
                <td>${i.name}</td>
                <td><span class="badge badge-purple">${i.lob}</span></td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  };

  window.doCatSearch = async (q) => {
    const el = document.getElementById("search-results");
    if (!q.trim()) { el.innerHTML = ""; return; }
    const results = await api.get(`/api/catalog/search?q=${encodeURIComponent(q)}`);
    if (!results.length) { el.innerHTML = `<div class="alert alert-info">No results for "${q}"</div>`; return; }
    el.innerHTML = `
      <div class="card" style="padding:0;overflow:hidden;margin-bottom:16px">
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Name</th><th>LOB</th></tr></thead>
            <tbody>${results.map(i => `
              <tr style="cursor:pointer" onclick="showItemDetail('${i.id}')">
                <td><span class="bold" style="color:var(--accent)">${i.id}</span></td>
                <td>${i.name}</td>
                <td><span class="badge badge-purple">${i.lob}</span></td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  };

  window.showItemDetail = async (id) => {
    const item = await api.get(`/api/catalog/item/${id}`);
    const prev = document.getElementById("item-detail");
    if (prev) prev.remove();
    const div = document.createElement("div");
    div.id = "item-detail";
    div.className = "card";
    div.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <div><span class="bold" style="color:var(--accent);font-size:16px">${item.id}</span>
          <span style="font-size:16px;font-weight:700;margin-left:8px">${item.name}</span></div>
        <button class="btn btn-sm btn-outline" onclick="document.getElementById('item-detail').remove()">✕ Close</button>
      </div>
      <div class="mb-2"><span class="badge badge-purple">${item.lob}</span></div>
      <div style="font-size:13px;color:var(--text);line-height:1.7;white-space:pre-wrap">${item.description}</div>
      <div class="mt-3">
        <button class="btn btn-primary btn-sm" onclick="navigate('kdd-generator');(function tryPrefill(n){window.prefillKDD?window.prefillKDD('${item.id}'):n>0&&setTimeout(()=>tryPrefill(n-1),150);})(20)">
          ◈ Generate KDD for ${item.id}
        </button>
      </div>`;
    document.getElementById("app").appendChild(div);
    div.scrollIntoView({ behavior: "smooth" });
  };
}

// ── KDD GENERATOR page ────────────────────────────────────────────────────────
async function pagKDD() {
  const lobs = await api.get("/api/catalog/lobs");
  const lobOpts = lobs.map(l => `<option value="${l.name}">${l.name} (${l.count})</option>`).join("");

  return `
    <div class="page-header">
      <div class="page-title">KDD Generator</div>
      <div class="page-sub">Generate 15 Cloud PE-compliant Key Design Decision questions per scope item</div>
    </div>

    <div class="card" id="kdd-form-card">
      <div class="section-divider" style="margin-top:0">1 · Project Details</div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Client Name *</label>
          <input id="kdd-client" class="form-input" placeholder="e.g. Acme Corporation" />
        </div>
        <div class="form-group">
          <label class="form-label">Project Name *</label>
          <input id="kdd-project" class="form-input" placeholder="e.g. S4HANA Rollout Phase 1" />
        </div>
      </div>

      <div class="section-divider">2 · Select Scope Items</div>

      <div class="form-group">
        <label class="form-label">Quick Add by ID</label>
        <div style="display:flex;gap:8px">
          <div class="kdd-id-wrap">
            <input id="kdd-id-input" class="form-input" placeholder="e.g. BD6, J59, BFB — type to search" autocomplete="off" />
            <div id="kdd-ac-dropdown" class="ac-dropdown"></div>
          </div>
          <button class="btn btn-outline" onclick="kddAddByIds()">Add</button>
        </div>
        <div class="form-hint">Comma-separated IDs — or type to search by name, validated against all 657 SAP processes</div>
      </div>

      <div style="display:flex;gap:10px;align-items:flex-end;margin-bottom:10px;flex-wrap:wrap">
        <div class="form-group" style="margin-bottom:0;min-width:200px">
          <label class="form-label">Browse by LOB</label>
          <select id="kdd-lob" class="form-select" onchange="kddLoadLOB(this.value)">
            <option value="">— select LOB —</option>
            ${lobOpts}
          </select>
        </div>
        <div class="form-group" style="margin-bottom:0;flex:1;min-width:180px">
          <label class="form-label">Filter items</label>
          <input id="kdd-item-filter" class="form-input" placeholder="Type to filter…" oninput="kddFilter(this.value)" />
        </div>
      </div>

      <div id="kdd-item-list" class="item-list" style="display:none"></div>

      <div class="section-divider">3 · Selected Items</div>
      <div id="kdd-chips" class="chips mb-3">
        <span class="text-muted text-sm">No items selected yet</span>
      </div>

      <div id="kdd-alert"></div>

      <div style="margin-top:8px">
        <button id="kdd-ai-btn" class="btn btn-primary" style="width:100%" onclick="kddGenerate('ai')"
          title="Claude generates tailored KDDs — 3 items run in parallel">
          🤖 Generate KDDs with AI
        </button>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);text-align:center;margin-top:5px">
        Claude AI · 3 scope items in parallel · ~40s
      </div>
    </div>

    <div id="kdd-result"></div>`;
}

function bindKDD() {
  window._kddSelected = new Map(); // id → { id, name, lob }
  window._kddAllItems = [];

  window.prefillKDD = (id) => {
    document.getElementById("kdd-id-input").value = id;
    kddAddByIds();
  };

  window.kddAddByIds = async () => {
    hideAC();
    const raw  = document.getElementById("kdd-id-input").value;
    const ids  = raw.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    const errs = [];
    for (const id of ids) {
      if (_kddSelected.has(id)) continue;
      const item = await api.get(`/api/catalog/item/${id}`);
      if (item.error) { errs.push(id); continue; }
      _kddSelected.set(id, item);
    }
    document.getElementById("kdd-id-input").value = "";
    if (errs.length) {
      setKddAlert(`warning`, `❌ Not found in the 657-process catalog: <strong>${errs.join(", ")}</strong>. Use the search below or browse by LOB.`);
    } else {
      setKddAlert("", "");
    }
    renderChips();
  };

  // ── Autocomplete on the ID input ─────────────────────────────────────────────
  const _acInput = document.getElementById("kdd-id-input");
  const _acDrop  = document.getElementById("kdd-ac-dropdown");
  let   _acTimer;

  function hideAC() { if (_acDrop) _acDrop.style.display = "none"; }

  _acInput.addEventListener("input", () => {
    clearTimeout(_acTimer);
    const val       = _acInput.value;
    const lastToken = val.split(/[\s,]+/).pop().trim();
    if (lastToken.length < 1) { hideAC(); return; }
    _acTimer = setTimeout(async () => {
      const results = await api.get(`/api/catalog/search?q=${encodeURIComponent(lastToken)}`);
      if (!results || !results.length) { hideAC(); return; }
      _acDrop.innerHTML = results.slice(0, 8).map(p => `
        <div class="ac-item" onmousedown="kddAcPick('${p.id}')">
          <span class="ac-id">${p.id}</span>
          <span class="ac-name">${p.name}</span>
          <span class="ac-lob">${p.lob}</span>
        </div>`).join("");
      _acDrop.style.display = "block";
    }, 180);
  });

  _acInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideAC();
    if (e.key === "Enter")  { e.preventDefault(); kddAddByIds(); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".kdd-id-wrap")) hideAC();
  });

  window.kddAcPick = (id) => {
    const parts = _acInput.value.split(/[\s,]+/).filter(Boolean);
    parts[Math.max(0, parts.length - 1)] = id;
    _acInput.value = parts.join(", ") + ", ";
    hideAC();
    _acInput.focus();
  };

  window.kddLoadLOB = async (lob) => {
    if (!lob) return;
    const el    = document.getElementById("kdd-item-list");
    el.style.display = "block";
    el.innerHTML = `<div class="loader" style="padding:12px 14px"><div class="spinner"></div> Loading ${lob}…</div>`;
    const items = await api.get(`/api/catalog/lob/${encodeURIComponent(lob)}`);
    _kddAllItems = items;
    renderItemList(items);
  };

  window.kddFilter = (q) => {
    const filtered = q
      ? _kddAllItems.filter(i => i.id.toLowerCase().includes(q.toLowerCase()) || i.name.toLowerCase().includes(q.toLowerCase()))
      : _kddAllItems;
    renderItemList(filtered);
  };

  window.kddToggleItem = (id) => {
    const item = _kddAllItems.find(i => i.id === id);
    if (!item) return;
    if (_kddSelected.has(id)) _kddSelected.delete(id);
    else _kddSelected.set(id, item);
    // Update checkbox
    const cb = document.querySelector(`input[data-id="${id}"]`);
    if (cb) {
      cb.checked = _kddSelected.has(id);
      cb.closest(".item-row").classList.toggle("checked", _kddSelected.has(id));
    }
    renderChips();
  };

  window.kddRemoveChip = (id) => {
    _kddSelected.delete(id);
    // uncheck if visible
    const cb = document.querySelector(`input[data-id="${id}"]`);
    if (cb) { cb.checked = false; cb.closest(".item-row").classList.remove("checked"); }
    renderChips();
  };

  // ── Auto quality critique — runs immediately after generation, inline ────────
  window.autoRunCritique = async function autoRunCritique(scopeIds) {
    const section = document.getElementById("auto-critique-section");
    if (!section) { console.warn("[autoRunCritique] auto-critique-section not found"); return; }

    const log = msg => {
      const s = document.getElementById("auto-critique-section");
      if (s) s.innerHTML = msg;
    };

    // Show spinner immediately — before any async work
    log(`<div class="flex items-center gap-2" style="color:var(--text-muted);font-size:13px">
      <div class="spinner" style="width:13px;height:13px;flex-shrink:0"></div>
      <span>🔍 Loading KDD data…</span>
    </div>`);

    try {
      const data = await api.get("/api/kdd/data");
      if (data.error) {
        log(`<div style="font-size:13px;color:var(--danger);padding:4px 0">⚠️ ${data.error}</div>`);
        return;
      }
      if (!data.rows || !data.rows.length) {
        log(`<div class="text-muted text-sm" style="padding:4px 0">⚠️ No rows to review.</div>`);
        return;
      }

      log(`<div class="flex items-center gap-2" style="color:var(--text-muted);font-size:13px">
        <div class="spinner" style="width:13px;height:13px;flex-shrink:0"></div>
        <span>🔍 Starting quality review for ${data.rows.length} questions…</span>
      </div>`);

      const response = await fetch("/api/kdd/critique", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows: data.rows })
      });

      if (!response.ok) {
        const errText = await response.text().catch(() => response.statusText);
        throw new Error(`Server error ${response.status}: ${errText.slice(0, 100)}`);
      }

      const reader = response.body.getReader();
      const dec    = new TextDecoder();
      let   buf    = "";
      const results = [];     // { kddId, rating, reason }
      let   itemsDone = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n");
        buf = parts.pop();
        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          let evt; try { evt = JSON.parse(part.slice(6)); } catch { continue; }

          if (evt.type === "progress") {
            log(`<div class="flex items-center gap-2" style="color:var(--text-muted);font-size:13px">
              <div class="spinner" style="width:13px;height:13px;flex-shrink:0"></div>
              <span>🔍 Reviewing ${evt.item || ""}…</span>
            </div>`);
          } else if (evt.type === "item_done") {
            itemsDone++;
            log(`<div class="flex items-center gap-2" style="color:var(--text-muted);font-size:13px">
              <div class="spinner" style="width:13px;height:13px;flex-shrink:0"></div>
              <span>🔍 Reviewed ${itemsDone} / ${scopeIds.length} scope items…</span>
            </div>`);
          } else if (evt.type === "complete") {
            results.push(...(evt.results || []));
          } else if (evt.type === "item_error") {
            console.warn(`[critique] item error for ${evt.item}: ${evt.message}`);
          }
        }
      }

      if (!results.length) {
        log(`<div class="text-muted text-sm" style="padding:4px 0">🔍 Quality review finished — open editor to see badges.</div>`);
        return;
      }

      const generic  = results.filter(r => r.rating === "generic");
      const specific = results.filter(r => r.rating === "specific");
      const pct      = Math.round(specific.length / results.length * 100);
      const color    = pct >= 80 ? "var(--success)" : pct >= 60 ? "var(--warning)" : "var(--danger)";
      const emoji    = pct >= 80 ? "✅" : pct >= 60 ? "⚠️" : "🔴";

      const topWeak = generic.slice(0, 3).map(r =>
        `<li style="font-size:12px;color:var(--text-muted);margin:3px 0">${r.kddId} — ${r.reason || "too generic"}</li>`
      ).join("");

      log(`<div style="padding:4px 0">
        <div class="flex items-center gap-2 mb-2">
          <span>${emoji}</span>
          <span class="bold" style="font-size:13px">Quality Review Complete</span>
          <span style="margin-left:auto;font-size:12px;color:${color};font-weight:700">${pct}% specific</span>
        </div>
        <div style="font-size:12.5px;color:var(--text-muted)">
          ${specific.length} specific · ${generic.length} generic (out of ${results.length} questions reviewed)
        </div>
        ${topWeak ? `<ul style="margin:6px 0 6px 16px;padding:0">${topWeak}</ul>` : ""}
        <button class="btn btn-sm btn-outline" style="margin-top:6px" onclick="navigate('editor')">
          ✎ Open Editor to see quality badges →
        </button>
      </div>`);

    } catch (e) {
      console.error("[autoRunCritique] error:", e);
      log(`<div style="font-size:13px;color:var(--danger);padding:4px 0">
        ⚠️ Quality review failed: ${e.message.slice(0, 120)}
      </div>`);
    }
  }

  window.kddGenerate = async (mode = "ai") => {
    const client  = document.getElementById("kdd-client").value.trim();
    const project = document.getElementById("kdd-project").value.trim();
    const ids     = [..._kddSelected.keys()];

    if (!client || !project) { setKddAlert("warning", "Please enter client name and project name."); return; }
    if (!ids.length)         { setKddAlert("warning", "Select at least one scope item."); return; }
    setKddAlert("", "");

    const aiBtn = document.getElementById("kdd-ai-btn");
    if (aiBtn) { aiBtn.disabled = true; aiBtn.innerHTML = `<div class="spinner" style="width:16px;height:16px;display:inline-block"></div> Working…`; }

    // ── Live progress panel ──
    const res = document.getElementById("kdd-result");
    const modeLabel = `🤖 Claude generating KDDs — ${ids.length} scope item${ids.length === 1 ? "" : "s"}`;
    res.innerHTML = `
      <div class="card" id="kdd-progress-card">
        <div class="bold mb-3" style="font-size:14px">${modeLabel}</div>
        <div id="kdd-progress-rows"></div>
      </div>`;

    // Helper: add a row to the live panel
    const progressRows = () => document.getElementById("kdd-progress-rows");
    const addRow = (id, html) => {
      let row = document.getElementById(`pr-${id}`);
      if (!row) {
        row = document.createElement("div");
        row.id = `pr-${id}`;
        row.style.cssText = "display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid #f3f4f6;font-size:13px";
        progressRows().appendChild(row);
      }
      row.innerHTML = html;
    };

    // ── SSE stream reader ──
    // Server sends text/event-stream over the POST response body.
    // We read it chunk-by-chunk and parse events as they arrive.
    try {
      const response = await fetch("/api/kdd/generate", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ client, project, scopeIds: ids, mode })
      });

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let   buf        = "";
      let   totalKDDs  = 0;
      let   filename   = null;
      let   fatalMsg   = null;
      let   totalTokensIn  = 0;
      let   totalTokensOut = 0;
      const _timers    = {};   // itemId → { startMs, intervalId }

      const startItemTimer = (itemId, startedAt) => {
        if (_timers[itemId]) return;                 // already running
        const t0 = startedAt || Date.now();
        const intervalId = setInterval(() => {
          const el = document.getElementById(`elapsed-${itemId}`);
          if (el) el.textContent = ((Date.now() - t0) / 1000).toFixed(0) + "s";
        }, 500);
        _timers[itemId] = { startMs: t0, intervalId };
      };
      const stopItemTimer = (itemId) => {
        if (_timers[itemId]) { clearInterval(_timers[itemId].intervalId); delete _timers[itemId]; }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });

        // SSE events are separated by double newlines
        const parts = buf.split("\n\n");
        buf = parts.pop();                      // keep incomplete tail

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === "progress") {
            if (evt.item) {
              startItemTimer(evt.item, evt.startedAt);
              addRow(evt.item, `
                <div class="spinner" style="width:14px;height:14px;flex-shrink:0"></div>
                <span class="bold" style="color:var(--accent);min-width:42px">${evt.item}</span>
                <span style="flex:1">${evt.name || ""}</span>
                <span class="text-muted text-sm">${evt.message || "working…"}</span>
                <span id="elapsed-${evt.item}" class="text-muted text-sm" style="min-width:28px;text-align:right;font-variant-numeric:tabular-nums">0s</span>`);
            } else {
              addRow("__excel__", `
                <div class="spinner" style="width:14px;height:14px;flex-shrink:0"></div>
                <span>${evt.message}</span>`);
            }
          }

          else if (evt.type === "item_done") {
            stopItemTimer(evt.item);
            totalKDDs += evt.count || 15;
            totalTokensIn  += evt.inputTokens  || 0;
            totalTokensOut += evt.outputTokens || 0;

            const secs = evt.durationMs ? (evt.durationMs / 1000).toFixed(1) + "s" : "";
            const toks = (evt.inputTokens || evt.outputTokens)
              ? `<span class="text-muted text-sm" title="~${evt.inputTokens} input · ~${evt.outputTokens} output tokens">~${(evt.inputTokens||0)+(evt.outputTokens||0)} tok</span>`
              : "";
            const reason = evt.note ? evt.note.slice(0, 80) : "";
            const ricefBadge = (evt.ricef > 0)
              ? `<span class="badge badge-purple" title="${evt.ricef} RICEF objects identified">⚙ ${evt.ricef} RICEF</span>`
              : "";
            const badge = evt.source === "cache"
              ? `<span class="badge badge-yellow" title="${reason}">cache ⓘ</span>`
              : `<span class="badge badge-green">Claude ✓</span> ${ricefBadge}`;

            addRow(evt.item, `
              <span style="font-size:15px">✅</span>
              <span class="bold" style="color:var(--accent);min-width:42px">${evt.item}</span>
              <span style="flex:1">${evt.name}</span>
              <span class="text-muted text-sm">${evt.count} Qs</span>
              ${toks}
              <span class="text-muted text-sm" style="min-width:36px;text-align:right;font-variant-numeric:tabular-nums">${secs}</span>
              <span style="margin-left:4px">${badge}</span>`);
            const pr = document.getElementById(`pr-${evt.item}`);
            if (pr) { pr.dataset.source = evt.source; pr.dataset.name = evt.name || ""; }
          }

          else if (evt.type === "error") {
            const errId = evt.item || `err-${Date.now()}`;
            addRow(errId, `
              <span style="font-size:15px">⚠️</span>
              <span class="bold" style="color:var(--danger);min-width:42px">${evt.item || "?"}</span>
              <span class="text-muted text-sm">${evt.message}</span>`);
            const pr = document.getElementById(`pr-${errId}`);
            if (pr) { pr.dataset.source = "error"; pr.dataset.name = ""; }
          }

          else if (evt.type === "complete") {
            filename  = evt.filename;
            totalKDDs = evt.totalKDDs || totalKDDs;
            // Remove the "Building Excel workbook…" spinner row — Excel is done
            const excelRow = document.getElementById("pr-__excel__");
            if (excelRow) excelRow.remove();
          }

          else if (evt.type === "fatal") {
            fatalMsg = evt.message;
          }
        }
      }

      // ── Stream finished — reset both buttons ──
      if (aiBtn) { aiBtn.disabled = false; aiBtn.innerHTML = "🤖 Generate KDDs with AI"; }

      if (fatalMsg) {
        document.getElementById("kdd-progress-card").innerHTML +=
          `<div class="alert alert-danger mt-3">⚠️ ${fatalMsg}</div>`;
        return;
      }

      // Add ↻ Retry buttons to any cache-fallback or error rows
      document.querySelectorAll("#kdd-progress-rows [data-source='cache'], #kdd-progress-rows [data-source='error']").forEach(row => {
        const rowId   = row.id.replace("pr-", "");
        const rowName = row.dataset.name || rowId;
        const retryBtn = document.createElement("button");
        retryBtn.className = "btn btn-sm btn-outline";
        retryBtn.style.marginLeft = "8px";
        retryBtn.textContent = "↻ Retry with Claude";
        retryBtn.onclick = () => retryItem(rowId, rowName, retryBtn);
        row.appendChild(retryBtn);
      });

      document.getElementById("kdd-progress-card").innerHTML += `
        <div style="border-top:1px solid var(--border);margin-top:12px;padding-top:14px">
          <div class="flex items-center gap-2 mb-3">
            <span style="font-size:22px">✅</span>
            <div>
              <div class="bold" style="font-size:15px">KDD Log Ready</div>
              <div class="text-muted text-sm">${totalKDDs} questions · ${ids.length} scope items · generated by Claude using SAP for Me process data</div>
              ${(totalTokensIn + totalTokensOut) > 0 ? `<div class="text-muted text-sm" style="margin-top:3px;font-size:11px">🧮 ~${(totalTokensIn+totalTokensOut).toLocaleString()} tokens total · ~${totalTokensIn.toLocaleString()} in · ~${totalTokensOut.toLocaleString()} out</div>` : ""}
            </div>
          </div>
          ${filename ? `
            <div class="alert alert-success">
              📥 <strong>${filename}</strong><br>
              Sheets: Cover · Instructions · Scope Overview · KDD Master Log · Summary
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn btn-primary" onclick="navigate('editor')">✎ Open in KDD Editor</button>
              <a href="/api/output/download/${encodeURIComponent(filename)}" class="btn btn-outline" download>⬇ Download Excel</a>
              <button class="btn btn-outline" onclick="navigate('output')">↓ All Output Files</button>
            </div>` : ""}
        </div>
        <div id="auto-critique-section" style="border-top:1px solid var(--border);margin-top:12px;padding-top:12px">
          ${mode === "ai"
            ? `<div class="flex items-center gap-2" style="color:var(--text-muted);font-size:13px">
                <div class="spinner" style="width:13px;height:13px;flex-shrink:0"></div>
                <span>🔍 Auto-reviewing question quality…</span>
               </div>`
            : `<div class="flex items-center gap-8" style="font-size:13px;color:var(--text-muted)">
                <span>Quality review not run — cache questions are pre-validated.</span>
                <button class="btn btn-sm btn-outline" style="font-size:12px" onclick="autoRunCritique([${ids.map(i => `'${i}'`).join(",")}])">
                  🔍 Run Quality Check
                </button>
               </div>`
          }
        </div>`;

      // AI mode: auto-run critique. Cache mode: show opt-in button instead.
      if (mode === "ai") autoRunCritique(ids);

    } catch (e) {
      if (aiBtn) { aiBtn.disabled = false; aiBtn.innerHTML = "🤖 Generate KDDs with AI"; }
      if (res) res.innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
    }
  };

  // ── Retry a single scope item without re-running everything ──
  window.retryItem = async (id, name, btn) => {
    const row = document.getElementById(`pr-${id}`);
    if (btn) btn.remove();
    if (row) {
      row.innerHTML = `
        <div class="spinner" style="width:14px;height:14px;flex-shrink:0"></div>
        <span class="bold" style="color:var(--accent);min-width:42px">${id}</span>
        <span>${name}</span>
        <span class="text-muted text-sm" style="margin-left:auto">retrying Claude…</span>`;
    }
    try {
      const response = await fetch(`/api/kdd/retry/${encodeURIComponent(id)}`, { method: "POST" });
      const reader   = response.body.getReader();
      const decoder  = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n"); buf = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          let evt; try { evt = JSON.parse(line.slice(6)); } catch { continue; }
          if (evt.type === "item_done" && row) {
            const badge = evt.source === "cache"
              ? `<span class="badge badge-yellow">cache fallback</span>`
              : `<span class="badge badge-green">Claude ✓</span>`;
            row.innerHTML = `
              <span style="font-size:15px">✅</span>
              <span class="bold" style="color:var(--accent);min-width:42px">${id}</span>
              <span>${evt.name}</span>
              <span class="text-muted text-sm">${evt.count} questions regenerated</span>
              <span style="margin-left:auto">${badge}</span>`;
            row.dataset.source = evt.source;
            if (evt.source === "cache") {
              const retryBtn2 = document.createElement("button");
              retryBtn2.className = "btn btn-sm btn-outline";
              retryBtn2.style.marginLeft = "8px";
              retryBtn2.textContent = "↻ Retry again";
              retryBtn2.onclick = () => retryItem(id, name, retryBtn2);
              row.appendChild(retryBtn2);
            }
          } else if (evt.type === "error" && row) {
            row.innerHTML = `
              <span style="font-size:15px">⚠️</span>
              <span class="bold" style="color:var(--danger);min-width:42px">${id}</span>
              <span class="text-muted text-sm">${evt.message}</span>`;
            const retryBtn3 = document.createElement("button");
            retryBtn3.className = "btn btn-sm btn-outline";
            retryBtn3.style.marginLeft = "8px";
            retryBtn3.textContent = "↻ Retry";
            retryBtn3.onclick = () => retryItem(id, name, retryBtn3);
            row.appendChild(retryBtn3);
          }
        }
      }
    } catch (e) {
      if (row) row.innerHTML += `<span style="color:var(--danger);font-size:12px;margin-left:8px">Error: ${e.message}</span>`;
    }
  };

  function renderItemList(items) {
    const el = document.getElementById("kdd-item-list");
    if (!items.length) { el.innerHTML = `<div style="padding:12px 14px;color:var(--text-muted);font-size:13px">No items found</div>`; return; }
    el.innerHTML = items.map(i => `
      <div class="item-row ${_kddSelected.has(i.id) ? "checked" : ""}" onclick="kddToggleItem('${i.id}')">
        <input type="checkbox" data-id="${i.id}" ${_kddSelected.has(i.id) ? "checked" : ""} onclick="event.stopPropagation();kddToggleItem('${i.id}')" />
        <span class="item-id">${i.id}</span>
        <span class="item-name">${i.name}</span>
        <span class="item-lob">${i.lob}</span>
      </div>`).join("");
  }

  function renderChips() {
    const el = document.getElementById("kdd-chips");
    if (!_kddSelected.size) {
      el.innerHTML = `<span class="text-muted text-sm">No items selected yet</span>`;
      return;
    }
    el.innerHTML = [..._kddSelected.values()].map(i =>
      `<span class="chip">${i.id} <span class="chip-remove" onclick="kddRemoveChip('${i.id}')">✕</span></span>`
    ).join("");
  }

  function setKddAlert(type, msg) {
    const el = document.getElementById("kdd-alert");
    if (!type) { el.innerHTML = ""; return; }
    el.innerHTML = `<div class="alert alert-${type}">${msg}</div>`;
  }
}

// ── REFERENCE page ────────────────────────────────────────────────────────────
async function pagReference() {
  return `
    <div class="page-header">
      <div class="page-title">Explore Phase Agents</div>
      <div class="page-sub">Agents and workflows for SAP S/4HANA Cloud Public Edition — in project execution order</div>
    </div>
    <div class="agent-list">
      ${agentCard(1,"BDCQ Agent","Automates retrieval and processing of BDCQ accelerator content from SAP Roadmap Viewer into structured outputs.","Active","Explore","navigate('bdcq-agent')")}
      ${agentCard(2,"Fit-to-Standard Material Generation","Auto-generates fit-to-standard workshop decks, process overviews, and attendee pre-read packs from the scope catalog.","Coming Soon","Explore",null)}
      ${agentCard(3,"KDD Creation","Generates 15 Cloud PE-compliant KDD questions per scope item and exports a 5-sheet Excel. Reads scope-catalog.json for real SAP process context.","Active","SAP Activate","navigate('kdd-generator')")}
      ${agentCard(4,"Business Process Design L1 to L5","Builds a structured process hierarchy (L1–L5) from scope catalog data, ready for workshop facilitation.","Coming Soon","Explore",null)}
    </div>`;
}

// ── OUTPUT page ───────────────────────────────────────────────────────────────
async function pagOutput() {
  const data = await api.get("/api/output");
  const files  = data.files  || [];
  const folder = data.folder || "output/";

  const rows = files.length
    ? files.map(f => {
        const kb   = (f.size / 1024).toFixed(1);
        const date = new Date(f.modified).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
        const enc  = encodeURIComponent(f.name);
        return `<tr id="row-${enc}">
          <td>
            <div class="bold" style="font-size:13px">${f.name}</div>
          </td>
          <td style="white-space:nowrap">${kb} KB</td>
          <td style="white-space:nowrap">${date}</td>
          <td style="white-space:nowrap">
            <div style="display:flex;gap:6px">
              <a href="/api/output/download/${enc}" class="btn btn-sm btn-outline" download>⬇ Download</a>
              <button class="btn btn-sm" style="color:var(--danger);border:1px solid var(--danger);background:#fff;padding:5px 10px;border-radius:6px;font-size:12px;font-weight:600"
                onclick="deleteFile('${enc}', '${f.name}')">🗑 Delete</button>
            </div>
          </td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="4" style="text-align:center;padding:32px;color:var(--text-muted)">
         No Excel files yet — <a href="#kdd-generator" style="color:var(--accent)">generate a KDD log</a> first.
       </td></tr>`;

  const deleteAllBtn = files.length
    ? `<button class="btn btn-sm" style="color:var(--danger);border:1px solid #fca5a5;background:#fff2f2;padding:5px 14px;border-radius:6px;font-size:12.5px;font-weight:600"
         onclick="deleteAll()">🗑 Delete All Excel Files</button>`
    : "";

  return `
    <div class="page-header flex items-center justify-between">
      <div>
        <div class="page-title">Output Files</div>
        <div class="page-sub">
          ${files.length} Excel file${files.length !== 1 ? "s" : ""} ·
          <span style="font-family:monospace;font-size:12px;background:#f3f4f6;padding:1px 7px;border-radius:4px">${folder}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${deleteAllBtn}
        <!-- Export Full Cache hidden — cache quality is template-only (656/657 items generic) -->
        <!-- <button class="btn btn-outline" id="fullCacheBtn" onclick="exportFullCache()">⬇ Export Full Cache</button> -->
        <button class="btn btn-primary" onclick="navigate('kdd-generator')">◈ New KDD Log</button>
      </div>
    </div>

    <div id="full-cache-msg"></div>

    <div class="alert alert-info" style="font-size:12.5px">
      📁 Files are saved to <code style="background:rgba(0,0,0,.06);padding:1px 6px;border-radius:3px">${folder}</code>
      &nbsp;·&nbsp; <strong>kdd-data.json</strong> (raw KDD data) is kept separately and is never deleted here.
    </div>

    <div id="output-msg"></div>

    <div class="card" style="padding:0;overflow:hidden">
      <div class="table-wrap">
        <table id="output-table">
          <thead><tr><th>Filename</th><th>Size</th><th>Generated</th><th>Actions</th></tr></thead>
          <tbody id="output-body">${rows}</tbody>
        </table>
      </div>
    </div>`;
}

function bindOutput() {
  window.deleteFile = async (enc, name) => {
    if (!confirm(`Delete "${name}"?\n\nkdd-data.json will not be affected.`)) return;
    const res = await fetch(`/api/output/${enc}`, { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      const row = document.getElementById(`row-${enc}`);
      if (row) {
        row.style.transition = "opacity .3s";
        row.style.opacity = "0";
        setTimeout(() => {
          row.remove();
          const tbody = document.getElementById("output-body");
          if (!tbody.children.length) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:32px;color:var(--text-muted)">
              No Excel files — <a href="#kdd-generator" style="color:var(--accent)">generate a KDD log</a> first.
            </td></tr>`;
          }
        }, 300);
      }
      showOutputMsg(`✅ "${name}" deleted.`, "success");
    } else {
      showOutputMsg(`⚠️ ${data.error}`, "danger");
    }
  };

  window.deleteAll = async () => {
    const rows = document.querySelectorAll("#output-body tr[id]").length;
    if (!rows) return;
    if (!confirm(`Delete all ${rows} Excel file${rows !== 1 ? "s" : ""}?\n\nkdd-data.json will not be affected.`)) return;
    const res  = await fetch("/api/output", { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      document.getElementById("output-body").innerHTML =
        `<tr><td colspan="4" style="text-align:center;padding:32px;color:var(--text-muted)">
          No Excel files — <a href="#kdd-generator" style="color:var(--accent)">generate a KDD log</a> first.
        </td></tr>`;
      showOutputMsg(`✅ ${data.deleted} file${data.deleted !== 1 ? "s" : ""} deleted. kdd-data.json kept.`, "success");
      // Hide the "Delete All" button
      const btn = document.querySelector("[onclick='deleteAll()']");
      if (btn) btn.remove();
    } else {
      showOutputMsg(`⚠️ ${data.error}`, "danger");
    }
  };

  function showOutputMsg(text, type) {
    const el = document.getElementById("output-msg");
    el.innerHTML = `<div class="alert alert-${type}" style="margin-bottom:12px">${text}</div>`;
    setTimeout(() => { el.innerHTML = ""; }, 4000);
  }

  window.exportFullCache = async () => {
    const btn = document.getElementById("fullCacheBtn");
    const msg = document.getElementById("full-cache-msg");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Building Excel…"; }
    msg.innerHTML = `<div class="alert alert-info" style="margin-bottom:12px">
      ⏳ Building full cache Excel — 9,855 KDDs across 657 scope items. This takes ~10 seconds…
    </div>`;
    try {
      const res  = await fetch("/api/kdd/export-full-cache", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client: "All Scope Items", project: "Full Cache" })
      });
      const data = await res.json();
      if (data.ok) {
        const enc = encodeURIComponent(data.filename);
        msg.innerHTML = `<div class="alert alert-success" style="margin-bottom:12px">
          ✅ Full cache exported — <strong>${data.rows} KDDs</strong> across <strong>${data.scopeItems} scope items</strong>.
          &nbsp;<a href="/api/output/download/${enc}" class="btn btn-sm btn-outline" download style="margin-left:8px">⬇ Download ${data.filename}</a>
        </div>`;
        // Add file to table
        navigate("output");
      } else {
        msg.innerHTML = `<div class="alert alert-danger" style="margin-bottom:12px">⚠️ ${data.error}</div>`;
      }
    } catch (e) {
      msg.innerHTML = `<div class="alert alert-danger" style="margin-bottom:12px">⚠️ ${e.message}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "⬇ Export Full Cache (9,855 KDDs)"; }
      setTimeout(() => { msg.innerHTML = ""; }, 8000);
    }
  };
}

// ── SETTINGS page ─────────────────────────────────────────────────────────────
async function pagSettings() {
  const s    = await api.get("/api/status");
  const info = await api.get("/api/catalog/info");

  const loadedAt = info.extractedAt
    ? new Date(info.extractedAt).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })
    : "Unknown";

  const changedBanner = s.catalogChanged
    ? `<div class="alert alert-info" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
        <span>✨ <strong>New catalog detected</strong> — the Chrome extension just saved fresh SAP data.</span>
        <button class="btn btn-primary btn-sm" onclick="reloadCatalog()">Reload Now</button>
       </div>`
    : "";

  const catalogCard = info.exists ? `
    <table style="font-size:13.5px;width:100%">
      <tbody>
        <tr><td class="bold" style="width:200px;padding:7px 0;color:var(--text-muted)">Version</td>
            <td><strong>${info.version}</strong>
              ${s.catalogOutdated
                ? `<span class="badge badge-yellow" style="margin-left:8px">SAP ${s.expectedRelease} available</span>`
                : `<span class="badge badge-green" style="margin-left:8px">Current</span>`}
            </td></tr>
        <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">Processes</td>
            <td><strong>${info.processes.toLocaleString()}</strong> across ${info.lobs} Lines of Business</td></tr>
        <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">Country</td>
            <td>${info.country || "—"}</td></tr>
        <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">Loaded from SAP</td>
            <td>${loadedAt}</td></tr>
        <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">File size</td>
            <td>${(info.fileSize / 1024 / 1024).toFixed(1)} MB</td></tr>
      </tbody>
    </table>` : `<div class="alert alert-warning">⚠️ No catalog loaded. Use the Chrome extension to load data from SAP for Me.</div>`;

  return `
    <div class="page-header">
      <div class="page-title">Settings</div>
      <div class="page-sub">Catalog data management and system status</div>
    </div>

    ${changedBanner}

    <!-- ── Catalog Data ── -->
    <div class="card mb-4">
      <div class="section-divider" style="margin-top:0">Scope Catalog Data</div>
      ${catalogCard}

      <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
        <div class="bold text-sm mb-2" style="color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">How to reload fresh data from SAP for Me</div>
        <ol style="font-size:13px;color:var(--text);line-height:2.1;padding-left:18px;margin-bottom:16px">
          <li>Open Chrome → go to <code style="background:#f3f4f6;padding:1px 6px;border-radius:3px">me.sap.com/processnavigator</code></li>
          <li>Click the <strong>SAP Deck Agent</strong> extension icon</li>
          <li>Click <strong>Load Full Catalog</strong> <span class="text-muted text-sm">(this refreshes all 657 processes from SAP)</span></li>
          <li>Click <strong>📁 Save to Cartridge</strong> → select this project folder</li>
          <li>Come back here and click <strong>Reload Catalog</strong> below</li>
        </ol>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <button class="btn btn-primary" id="reload-btn" onclick="reloadCatalog()">
            🔄 Reload Catalog
          </button>
          <button class="btn btn-outline" id="clear-btn" onclick="clearCatalog()" style="color:var(--danger);border-color:var(--danger)">
            🗑 Clear Catalog Data
          </button>
          <span class="text-sm text-muted">You can reload at any time — not just at SAP releases.</span>
        </div>
        <div id="reload-result" class="mt-3"></div>
      </div>

      <!-- ── Upload catalog (no Chrome extension needed) ── -->
      <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
        <div class="bold text-sm mb-1" style="color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">
          📤 Upload catalog file <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--text-muted)">(alternative — no Chrome extension needed)</span>
        </div>
        <p class="text-sm text-muted" style="margin:4px 0 10px">
          If your project team sends you an updated <code>scope-catalog.json</code>, upload it here directly.
          Your current catalog is backed up automatically before replacement.
        </p>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <label class="btn btn-outline btn-sm" style="cursor:pointer;margin:0">
            📂 Choose scope-catalog.json
            <input type="file" id="catalog-file-input" accept=".json" style="display:none" onchange="uploadCatalog(this)">
          </label>
          <span id="upload-filename" class="text-muted text-sm">No file selected</span>
        </div>
        <div id="upload-result" class="mt-3"></div>
      </div>
    </div>


    <!-- ── System ── -->
    <div class="card">
      <div class="section-divider" style="margin-top:0">System</div>
      <table style="font-size:13.5px">
        <tbody>
          <tr><td class="bold" style="width:200px;padding:7px 0;color:var(--text-muted)">Mode</td>
              <td><span class="dot dot-gray" style="margin-right:6px"></span>offline</td></tr>
          <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">Writes to SAP</td>
              <td><span class="dot dot-green" style="margin-right:6px"></span>disabled (double-locked)</td></tr>
          <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">LLM API key</td>
              <td><span class="dot dot-green" style="margin-right:6px"></span>none needed — Claude Code is the runtime</td></tr>
          <tr><td class="bold" style="padding:7px 0;color:var(--text-muted)">Output files</td>
              <td>${s.outputCount} Excel file${s.outputCount !== 1 ? "s" : ""} generated</td></tr>
        </tbody>
      </table>
    </div>`;
}

function bindSettings() {

  // ── Reload catalog (SSE stream — reads progress live) ──
  window.reloadCatalog = async () => {
    const btn = document.getElementById("reload-btn");
    const el  = document.getElementById("reload-result");
    btn.disabled = true;
    btn.innerHTML = `<div class="spinner" style="width:14px;height:14px;display:inline-block;margin-right:6px"></div> Reloading…`;
    el.innerHTML  = "";

    try {
      const response = await fetch("/api/catalog/reload", { method: "POST" });
      const reader   = response.body.getReader();
      const decoder  = new TextDecoder();
      let buf = "";

      const log = (html) => {
        el.innerHTML += `<div style="font-size:13px;padding:3px 0;color:var(--text)">${html}</div>`;
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === "progress") {
            log(`⟳ ${evt.message}`);
          } else if (evt.type === "complete") {
            log(`✅ Done — version <strong>${evt.version}</strong> · ${evt.processes.toLocaleString()} processes · ${evt.totalKDDs.toLocaleString()} KDDs cached`);
            el.innerHTML += `<div class="alert alert-success mt-2">Catalog and cache are up to date. <button class="btn btn-outline btn-sm" onclick="navigate('home')">Go to Home</button></div>`;
          } else if (evt.type === "error") {
            log(`⚠️ ${evt.message}`);
            el.innerHTML += `<div class="alert alert-danger mt-2">${evt.message}</div>`;
          }
        }
      }
    } catch (e) {
      document.getElementById("reload-result").innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
    }

    btn.disabled = false;
    btn.innerHTML = "🔄 Reload Catalog";
  };

  // ── Upload catalog JSON (no Chrome extension needed) ──
  window.uploadCatalog = async (input) => {
    const file = input.files[0];
    if (!file) return;
    document.getElementById("upload-filename").textContent = file.name;
    const resultEl = document.getElementById("upload-result");
    resultEl.innerHTML = `<div class="text-muted text-sm"><div class="spinner" style="width:13px;height:13px;display:inline-block;margin-right:6px"></div> Reading file…</div>`;

    let data;
    try {
      const text = await file.text();
      data = JSON.parse(text);
    } catch (e) {
      resultEl.innerHTML = `<div class="alert alert-danger">❌ File is not valid JSON: ${e.message}</div>`;
      return;
    }

    if (!Array.isArray(data.processes) || data.processes.length === 0) {
      resultEl.innerHTML = `<div class="alert alert-danger">❌ Not a valid scope-catalog.json — missing processes array.</div>`;
      return;
    }

    resultEl.innerHTML = `<div class="text-muted text-sm"><div class="spinner" style="width:13px;height:13px;display:inline-block;margin-right:6px"></div> Uploading…</div>`;
    const res = await api.post("/api/catalog/upload", data);
    if (res.ok) {
      resultEl.innerHTML = `<div class="alert alert-success">
        ✅ Catalog updated — <strong>${res.processes} processes</strong> · version <strong>${res.version}</strong><br>
        <span class="text-sm">Previous catalog backed up automatically.</span>
      </div>`;
      setTimeout(() => navigate("settings"), 2000);
    } else {
      resultEl.innerHTML = `<div class="alert alert-danger">❌ Upload failed: ${res.error || "unknown error"}</div>`;
    }
  };

  // ── Clear catalog ──
  window.clearCatalog = async () => {
    if (!confirm("This will remove scope-catalog.json (a backup copy is kept).\n\nYou will need to reload from SAP for Me before generating KDDs.\n\nContinue?")) return;
    const el  = document.getElementById("reload-result");
    const res = await api.post("/api/catalog/clear", {});
    if (res.ok) {
      el.innerHTML = `<div class="alert alert-warning">
        🗑 Catalog cleared${res.backup ? ` (backup saved as <code>${res.backup}</code>)` : ""}.<br>
        Use the Chrome extension to load fresh data, then click <strong>Reload Catalog</strong>.
      </div>`;
      // Re-render to show empty state
      setTimeout(() => navigate("settings"), 1500);
    } else {
      el.innerHTML = `<div class="alert alert-danger">⚠️ ${res.error || "Clear failed"}</div>`;
    }
  };

  // ── Rebuild cache only ──
  window.rebuildCache = async () => {
    const btn = document.getElementById("cache-btn");
    const el  = document.getElementById("cache-result");
    btn.disabled = true;
    btn.innerHTML = `<div class="spinner" style="width:14px;height:14px;display:inline-block;margin-right:5px"></div> Building…`;
    const res = await api.post("/api/cache/rebuild", {});
    btn.disabled  = false;
    btn.innerHTML = "🔄 Rebuild Cache Only";
    el.innerHTML  = res.ok
      ? `<div class="alert alert-success">✅ ${res.cachedItems.toLocaleString()} items · ${res.totalKDDs.toLocaleString()} KDD questions cached.</div>`
      : `<div class="alert alert-danger">⚠️ ${res.error}</div>`;
  };

  // ── Poll for new catalog every 8s (file watcher sets a flag on server) ──
  const poller = setInterval(async () => {
    if (document.getElementById("reload-btn")) {
      const s = await api.get("/api/status").catch(() => null);
      if (s && s.catalogChanged) {
        const banner = document.querySelector(".alert-info");
        if (!banner) {
          const el = document.createElement("div");
          el.className = "alert alert-info";
          el.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px";
          el.innerHTML = `<span>✨ <strong>New catalog detected</strong> — the Chrome extension just saved fresh data.</span>
            <button class="btn btn-primary btn-sm" onclick="reloadCatalog()">Reload Now</button>`;
          document.getElementById("app").prepend(el);
        }
      }
    } else {
      clearInterval(poller); // left the settings page
    }
  }, 8000);
}

// ── GAP ANALYSIS page ─────────────────────────────────────────────────────────
async function pagGapAnalysis() {
  let data;
  try { data = await api.get("/api/kdd/data"); } catch (e) { data = { error: e.message }; }

  if (data.error) {
    return `
      <div class="page-header"><div class="page-title">KDD Analysis</div></div>
      <div class="alert alert-warning">📋 No KDD data yet.
        <a href="#kdd-generator" style="color:var(--accent);font-weight:600">Generate KDDs first →</a>
      </div>`;
  }

  const rows = data.rows || [];
  const meta = data.meta || {};

  // ── RAG ──────────────────────────────────────────────────────────────────────
  const rag = r => {
    if (r.fitGap === "Gap" && (r.complexity === "High" || r.complexity === "Medium")) return "Red";
    if (r.fitGap === "Gap" || (r.fitGap === "Partial Fit" && r.complexity !== "Low")) return "Amber";
    return "Green";
  };

  const fitRows      = rows.filter(r => r.fitGap === "Fit");
  const partRows     = rows.filter(r => r.fitGap === "Partial Fit");
  const gapRows      = rows.filter(r => r.fitGap === "Gap");
  const redRows      = rows.filter(r => rag(r) === "Red");
  const amberRows    = rows.filter(r => rag(r) === "Amber");
  const greenRows    = rows.filter(r => rag(r) === "Green");
  const approvedRows = rows.filter(r => r.signOffStatus === "Approved");
  const decidedRows  = rows.filter(r => r.decisionMade && r.decisionMade.trim());

  // ── WRICEF data ───────────────────────────────────────────────────────────────
  const ricefRows  = rows.filter(r => r.ricefType && r.ricefType !== "None");
  const ricefTypes = ["W","R","I","C","E_RAP","E_CAPM","E_RAP_CAPM","F"];
  const ricefLabels = {W:"Workflow",R:"Report",I:"Interface",C:"Conversion",E_RAP:"Extension BTP–RAP",E_CAPM:"Extension BTP–CAPM",E_RAP_CAPM:"Extension BTP–RAP & CAPM & UI",F:"Form"};
  const effortOrder = {S:0,M:1,L:2,XL:3,None:4};

  const ricefByType   = Object.fromEntries(ricefTypes.map(t => [t, ricefRows.filter(r => r.ricefType === t).length]));
  const effortCounts  = {S:0,M:0,L:0,XL:0};
  ricefRows.forEach(r => { if (effortCounts[r.effortEstimate] !== undefined) effortCounts[r.effortEstimate]++; });
  const btpCount      = ricefRows.filter(r => r.btpRequired === "Yes").length;
  const btpPossible   = ricefRows.filter(r => r.btpRequired === "Possible").length;

  // ── Phase distribution ────────────────────────────────────────────────────────
  const phaseCounts = { Explore: 0, Realize: 0, Deploy: 0 };
  rows.forEach(r => { if (phaseCounts[r.sapActivatePhase] !== undefined) phaseCounts[r.sapActivatePhase]++; });

  // ── Per-scope-item ────────────────────────────────────────────────────────────
  const scopeIds = [...new Set(rows.map(r => r.scopeItemId))];
  const byItem   = scopeIds.map(id => {
    const ir = rows.filter(r => r.scopeItemId === id);
    return {
      id, name: (ir[0]||{}).scopeItemName || id,
      module:       ir[0] ? modName(ir[0]) : "—",
      complexities: [...new Set(ir.map(r => r.complexity).filter(Boolean))].join(","),
      total:    ir.length,
      fit:      ir.filter(r => r.fitGap === "Fit").length,
      part:     ir.filter(r => r.fitGap === "Partial Fit").length,
      gap:      ir.filter(r => r.fitGap === "Gap").length,
      red:      ir.filter(r => rag(r) === "Red").length,
      amber:    ir.filter(r => rag(r) === "Amber").length,
      green:    ir.filter(r => rag(r) === "Green").length,
      approved: ir.filter(r => r.signOffStatus === "Approved").length,
      decided:  ir.filter(r => r.decisionMade && r.decisionMade.trim()).length,
      ricef:    ir.filter(r => r.ricefType && r.ricefType !== "None").length,
    };
  }).sort((a, b) => b.gap - a.gap || b.red - a.red);

  // ── Module pivot — group by full LOB name ─────────────────────────────────────
  const modules = [...new Set(rows.map(r => modName(r)))].filter(Boolean).sort();
  const byModule = modules.map(m => {
    const mr = rows.filter(r => modName(r) === m);
    return {
      m, total: mr.length,
      fit:   mr.filter(r => r.fitGap === "Fit").length,
      part:  mr.filter(r => r.fitGap === "Partial Fit").length,
      gap:   mr.filter(r => r.fitGap === "Gap").length,
      ricef: mr.filter(r => r.ricefType && r.ricefType !== "None").length,
      decided: mr.filter(r => r.decisionMade && r.decisionMade.trim()).length,
    };
  }).sort((a,b) => b.gap - a.gap || b.total - a.total);

  // ── Complexity × Fit/Gap matrix ───────────────────────────────────────────────
  const cx = ["High","Medium","Low"];
  const fg = ["Fit","Partial Fit","Gap"];
  const matrix = cx.map(c => fg.map(f => rows.filter(r => r.complexity===c && r.fitGap===f).length));
  const matrixCell = (c, f, n) => {
    const isRed   = f==="Gap"          && c!=="Low";
    const isAmber = f==="Partial Fit"  && c==="High";
    const bg = isRed ? "#fee2e2" : isAmber ? "#fef3c7" : n ? "#f0fdf4" : "#f9fafb";
    const col= isRed ? "#dc2626" : isAmber ? "#d97706" : n ? "#059669" : "#9ca3af";
    return `<div onclick="gaFilter('complexity_fitgap','${c}|${f}')"
      style="background:${bg};color:${col};font-size:18px;font-weight:800;
             padding:14px 6px;text-align:center;border-radius:8px;cursor:${n?"pointer":"default"};
             border:1.5px solid ${isRed?"#fca5a5":isAmber?"#fcd34d":"#e5e7eb"};
             transition:transform 0.1s" onmouseenter="if(${n})this.style.transform='scale(1.05)'" onmouseleave="this.style.transform=''">
      ${n||"—"}
    </div>`;
  };

  // ── Stacked bar SVG ───────────────────────────────────────────────────────────
  const ragBar = (fit, part, gap, total) => {
    if (!total) return "";
    const W = 120, fw = Math.round(fit/total*W), pw = Math.round(part/total*W), gw = W-fw-pw;
    return `<svg width="${W}" height="10" style="border-radius:4px;overflow:hidden">
      <rect x="0" y="0" width="${fw}" height="10" fill="#059669"/>
      <rect x="${fw}" y="0" width="${pw}" height="10" fill="#d97706"/>
      <rect x="${fw+pw}" y="0" width="${gw}" height="10" fill="#dc2626"/>
    </svg>`;
  };

  // ── Progress bar ──────────────────────────────────────────────────────────────
  const progBar = (done, total) => {
    if (!total) return "";
    const pct = Math.round(done/total*100);
    const col = pct >= 70 ? "#059669" : pct >= 40 ? "#d97706" : "#dc2626";
    return `<div style="display:flex;align-items:center;gap:6px">
      <div style="flex:1;height:6px;background:#e5e7eb;border-radius:3px;min-width:50px">
        <div style="width:${pct}%;height:6px;background:${col};border-radius:3px"></div>
      </div>
      <span style="font-size:11px;color:${col};font-weight:600;white-space:nowrap">${done}/${total}</span>
    </div>`;
  };

  // ── Scope item tbody HTML ─────────────────────────────────────────────────────
  const itemRowHTML = it =>
    `<tr data-fit="${it.fit}" data-part="${it.part}" data-gap="${it.gap}"
         data-red="${it.red}" data-amber="${it.amber}"
         data-approved="${it.approved}" data-ricef="${it.ricef||0}"
         data-complexities="${it.complexities}"
         data-module="${it.module}" data-id="${it.id}">
      <td><span style="color:var(--accent);font-family:monospace;font-weight:700">${it.id}</span></td>
      <td style="font-size:12px;max-width:200px">${it.name}</td>
      <td style="font-size:11.5px;color:var(--text-muted)">${it.module}</td>
      <td style="text-align:center">${it.total}</td>
      <td style="text-align:center;color:var(--success);font-weight:700">${it.fit}</td>
      <td style="text-align:center;color:var(--warning);font-weight:700">${it.part}</td>
      <td style="text-align:center;color:var(--danger);font-weight:700">${it.gap}</td>
      <td style="text-align:center;color:#4f46e5;font-weight:700">${it.ricef||"—"}</td>
      <td>${ragBar(it.fit,it.part,it.gap,it.total)}</td>
      <td style="min-width:120px">${progBar(it.decided,it.total)}</td>
    </tr>`;

  // ── Top risks — Gap + High or Medium complexity, not yet approved ────────────
  const topRisks = rows
    .filter(r => r.fitGap === "Gap" && (r.complexity === "High" || r.complexity === "Medium") && r.signOffStatus !== "Approved")
    .sort((a,b) => (b.complexity==="High"?1:0)-(a.complexity==="High"?1:0) || (a.scopeItemId||"").localeCompare(b.scopeItemId||""))
    .slice(0, 15);
  const riskRows = topRisks.length
    ? topRisks.map((r,i) => {
        const cxColor = r.complexity==="High" ? "#dc2626" : "#d97706";
        return `<tr>
          <td style="font-size:11px;color:var(--text-muted);text-align:center">${i+1}</td>
          <td><span class="badge badge-purple" style="font-size:10px">${r.scopeItemId}</span></td>
          <td style="font-size:12px">${r.designQuestion}</td>
          <td><span style="background:${cxColor}15;color:${cxColor};padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap">Gap · ${r.complexity}</span></td>
          <td style="font-size:11.5px;color:var(--text-muted)">${r.decisionOwner||"—"}</td>
          <td><button class="btn btn-sm btn-outline"
            onclick="navigate('editor');setTimeout(()=>{ window._editorJumpTo && window._editorJumpTo('${r.kddId}'); },500)">
            Edit</button></td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="6" style="text-align:center;padding:28px;color:var(--text-muted)">✅ No high/medium-complexity gaps without a sign-off</td></tr>`;

  // ── RICEF Register ────────────────────────────────────────────────────────────
  const ricefRegister = ricefRows.length
    ? [...ricefRows].sort((a,b) => (effortOrder[b.effortEstimate]||4)-(effortOrder[a.effortEstimate]||4) || (a.ricefType||"").localeCompare(b.ricefType||""))
        .map((r,i) => {
          const effortColor = r.effortEstimate==="XL"?"#dc2626":r.effortEstimate==="L"?"#d97706":r.effortEstimate==="M"?"#0369a1":"#059669";
          const fgBadge = r.fitGap==="Gap"?`<span class="badge badge-red" style="font-size:10px">Gap</span>`:
                          r.fitGap==="Partial Fit"?`<span style="background:#fef3c7;color:#d97706;padding:2px 6px;border-radius:6px;font-size:10px;font-weight:700">Partial</span>`:"";
          return `<tr data-fitgap="${r.fitGap||""}" data-module="${modName(r)}" data-riceftype="${r.ricefType||""}">
            <td style="text-align:center;color:var(--text-muted);font-size:11px">${i+1}</td>
            <td style="font-family:monospace;font-size:11px;color:var(--accent)">${r.ricefId||"—"}</td>
            <td><span style="background:#ede9fe;color:#4f46e5;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:700">${ricefLabels[r.ricefType]||r.ricefType}</span></td>
            <td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.ricefTitle||""}">${r.ricefTitle||"—"}</td>
            <td><span class="badge badge-purple" style="font-size:10px">${r.scopeItemId}</span> ${fgBadge}</td>
            <td style="font-size:11.5px;color:var(--text-muted)">${modName(r)}</td>
            <td style="font-size:11px">${r.extensibilityType||"—"}</td>
            <td style="text-align:center">${r.btpRequired==="Yes"?`<span style="color:#dc2626;font-weight:700">Yes</span>`:r.btpRequired==="Possible"?`<span style="color:#d97706">Possible</span>`:"No"}</td>
            <td style="text-align:center;font-weight:700;color:${effortColor}">${r.effortEstimate||"—"}</td>
            <td><button class="btn btn-sm btn-outline" style="font-size:11px"
              onclick="navigate('editor');setTimeout(()=>{ window._editorJumpTo && window._editorJumpTo('${r.kddId}'); },400)">Edit</button></td>
          </tr>`;
        }).join("")
    : `<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-muted)">No WRICEF items — all rows are Fit. Set WRICEF Type on Gap rows in the editor.</td></tr>`;

  const healthPct   = rows.length ? Math.round(greenRows.length/rows.length*100) : 0;
  const healthColor = healthPct >= 70 ? "var(--success)" : healthPct >= 40 ? "var(--warning)" : "var(--danger)";
  const decidedPct  = rows.length ? Math.round(decidedRows.length/rows.length*100) : 0;

  // ── Tile click helper (injected as onclick string — defined in bindGapAnalysis) ─
  const tile = (label, value, note, color, filterKey, filterVal, tooltip) => {
    const hasFilter = !!(filterKey && filterVal !== undefined && filterVal !== "");
    return `<div class="card" data-ga-tile="${filterKey}||${filterVal}"
      ${tooltip ? `title="${tooltip}"` : ""}
      style="cursor:${hasFilter?"pointer":"default"};transition:box-shadow 0.15s,border-color 0.15s;
             padding:10px 12px;border:1.5px solid transparent"
      ${hasFilter ? `onclick="gaFilter('${filterKey}','${filterVal}')"
        onmouseenter="this.style.boxShadow='0 4px 16px rgba(0,0,0,0.12)'"
        onmouseleave="this.style.boxShadow=''"` : ""}>
      <div style="color:${color||"var(--text-muted)"};font-size:10px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em;font-weight:700">${label}</div>
      <div style="color:${color||"var(--text)"};font-size:22px;line-height:1.1;margin-bottom:2px;font-weight:800">${value}</div>
      ${note ? `<div style="font-size:10px;color:var(--text-muted)">${note}</div>` : ""}
    </div>`;
  };

  return `
    <div class="page-header flex items-center justify-between" style="flex-wrap:wrap;gap:10px">
      <div>
        <div class="page-title">KDD Analysis</div>
        <div class="page-sub">High Level Summary · ${meta.client||"—"} · ${meta.project||"—"} ·
          ${rows.length} KDDs · ${scopeIds.length} scope item${scopeIds.length!==1?"s":""}
          ${meta.generatedAt ? ` · Generated ${new Date(meta.generatedAt).toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"})}` : ""}
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-outline btn-sm" onclick="gaFilter('','')">⊘ Clear Filter</button>
        <button class="btn btn-outline" onclick="navigate('editor')">✎ Open Editor</button>
        <button class="btn btn-outline" onclick="navigate('kdd-generator')">◈ Add Scope Items</button>
        <button class="btn btn-primary" id="run-ai-btn" onclick="window.runAIAnalysis()">🤖 Run AI Analysis</button>
      </div>
    </div>

    <div id="ga-filter-bar" style="display:none;background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-size:12.5px;align-items:center;gap:10px">
      <span>🔍 Filtered: <strong id="ga-filter-label"></strong></span>
      <button onclick="gaFilter('','')" style="background:none;border:none;color:#d97706;font-size:13px;cursor:pointer;font-weight:700">✕ Clear</button>
    </div>

    <!-- ── Summary tiles ── -->
    <div class="stats-grid" style="grid-template-columns:repeat(auto-fill,minmax(90px,1fr));margin-bottom:12px">
      ${tile("Health Score",  healthPct+"%",           `${greenRows.length}/${rows.length} · Fit or low-risk`,  healthColor,  "",  "",  "% of KDDs that are Green RAG. Green = Fit rows, plus Gap or Partial Fit rows where complexity is Low. Red = Gap + High/Medium complexity. Amber = everything else.")}
      ${tile("Total KDDs",    rows.length,             scopeIds.length+" scope items",         "",                                                    "",        "")}
      ${tile("✔ Fit",         fitRows.length,          rows.length?Math.round(fitRows.length/rows.length*100)+"%":"—",  "var(--success)",             "fitgap",  "Fit")}
      ${tile("≈ Partial Fit", partRows.length,         rows.length?Math.round(partRows.length/rows.length*100)+"%":"—","var(--warning)",              "fitgap",  "Partial Fit")}
      ${tile("✗ Gap",         gapRows.length,          rows.length?Math.round(gapRows.length/rows.length*100)+"%":"—",  "var(--danger)",              "fitgap",  "Gap")}
      ${tile("🔴 Red RAG",    redRows.length,          "Gap + High/Med",                      "var(--danger)",                                        "rag",     "Red")}
      ${tile("🟡 Amber RAG",  amberRows.length,        "",                                    "var(--warning)",                                       "rag",     "Amber")}
      ${tile("✅ Approved",   approvedRows.length,     "of "+rows.length,                     "var(--success)",                                       "approved","yes")}
      ${tile("📝 Decisions",  decidedPct+"%",          decidedRows.length+" of "+rows.length, decidedPct>=70?"var(--success)":decidedPct>=40?"var(--warning)":"var(--danger)","decisions","yes")}
      ${tile("🔧 RICEF",      ricefRows.length,        "items to build",                      "#4f46e5",                                              "ricef",   "yes")}
    </div>

    <!-- ── Phase distribution strip ── -->
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
      <span style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">Phase:</span>
      ${[["Explore","#059669"],["Realize","#0369a1"],["Deploy","#7c3aed"]].map(([p,c]) =>
        phaseCounts[p] ? `<span style="background:${c}15;color:${c};border:1px solid ${c}30;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:700">${p}: ${phaseCounts[p]}</span>` : ""
      ).join("")}
      <span style="font-size:11px;color:var(--text-muted);margin-left:4px">(SAP Activate phase of each KDD)</span>
    </div>

    <!-- ── Two-column: Matrix + RICEF effort ── -->
    <div style="display:grid;grid-template-columns:auto 1fr;gap:16px;margin-bottom:20px;align-items:start">

      <!-- Complexity × Fit/Gap matrix -->
      <div class="card" style="padding:16px;min-width:280px">
        <div style="font-size:12px;font-weight:700;color:var(--text-muted);margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em">Risk Matrix — click to filter ↓</div>
        <div style="display:grid;grid-template-columns:60px 1fr 1fr 1fr;gap:6px;align-items:center">
          <div></div>
          ${fg.map(f => `<div style="text-align:center;font-size:11px;font-weight:700;color:${f==="Gap"?"var(--danger)":f==="Partial Fit"?"var(--warning)":"var(--success)"}">${f}</div>`).join("")}
          ${cx.map((c,ci) => `
            <div style="font-size:11px;font-weight:700;color:var(--text-muted)">${c}</div>
            ${fg.map((f,fi) => matrixCell(c,f,matrix[ci][fi])).join("")}
          `).join("")}
        </div>
      </div>

      <!-- RICEF effort summary -->
      <div class="card" style="padding:16px">
        <div style="font-size:12px;font-weight:700;color:var(--text-muted);margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em">🔧 WRICEF Effort Summary — ${ricefRows.length} items total</div>
        ${ricefRows.length ? `
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:14px">
          ${ricefTypes.filter(t => ricefByType[t]).map(t => `
            <div style="background:#f5f3ff;border-radius:8px;padding:10px 12px;border-left:3px solid #7c3aed">
              <div style="font-size:11px;color:#6d28d9;font-weight:700">${ricefLabels[t]||t}</div>
              <div style="font-size:22px;font-weight:800;color:#4f46e5">${ricefByType[t]}</div>
            </div>`).join("")}
        </div>
        <div style="border-top:1px solid var(--border);padding-top:12px">
          <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:8px">EFFORT DISTRIBUTION</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            ${[["S","#059669"],["M","#0369a1"],["L","#d97706"],["XL","#dc2626"]].filter(([e])=>effortCounts[e]).map(([e,c]) =>
              `<span style="background:${c}1a;color:${c};border:1px solid ${c}33;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700">${e}: ${effortCounts[e]}</span>`
            ).join("")}
            <span style="margin-left:8px;font-size:12px;color:var(--text-muted)">|</span>
            <span style="font-size:12px;font-weight:600;color:#dc2626">BTP Required: ${btpCount}</span>
            ${btpPossible ? `<span style="font-size:12px;color:#d97706">Possible: ${btpPossible}</span>` : ""}
          </div>
        </div>` : `<div style="color:var(--text-muted);font-size:13px;padding:20px 0">No WRICEF items yet — set WRICEF Type on Gap rows in the editor.</div>`}
      </div>
    </div>

    <!-- ── Top Risks — moved near top for action visibility ── -->
    <div class="section-divider">🔴 Top Risks — Gap + High/Medium Complexity, not yet approved</div>
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:20px">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th style="width:32px">#</th><th style="width:70px">Item</th>
            <th>Design Question</th>
            <th style="width:75px">Complexity</th>
            <th style="width:70px">Owner</th><th style="width:70px">Action</th>
          </tr></thead>
          <tbody>${riskRows}</tbody>
        </table>
      </div>
    </div>

    <!-- ── Scope item table ── -->
    <div class="section-divider" style="display:flex;align-items:center;justify-content:space-between">
      <span>Fit / Gap by Scope Item <span id="ga-scope-count" style="color:var(--text-muted);font-weight:400;font-size:12px">(${byItem.length} items)</span></span>
      <span style="font-size:11px;color:var(--text-muted)">Click a tile above to filter ↑</span>
    </div>
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:20px">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>ID</th><th>Process</th><th>Module</th>
            <th style="text-align:center">Total</th>
            <th style="text-align:center;color:var(--success)">Fit</th>
            <th style="text-align:center;color:var(--warning)">Partial</th>
            <th style="text-align:center;color:var(--danger)">Gap</th>
            <th style="text-align:center;color:#4f46e5">RICEF</th>
            <th style="width:130px">Distribution</th>
            <th style="width:140px">Decisions Made</th>
          </tr></thead>
          <tbody id="ga-scope-tbody">${byItem.map(itemRowHTML).join("")}</tbody>
        </table>
      </div>
    </div>

    <!-- ── Module pivot ── -->
    <div class="section-divider">Fit / Gap by Line of Business / Module</div>
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:20px">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Module / LoB</th>
            <th style="text-align:center">Total</th>
            <th style="text-align:center;color:var(--success)">Fit</th>
            <th style="text-align:center;color:var(--warning)">Partial</th>
            <th style="text-align:center;color:var(--danger)">Gap</th>
            <th style="text-align:center;color:#4f46e5">RICEF</th>
            <th style="width:160px">Distribution</th>
            <th style="width:160px">Decisions Made</th>
          </tr></thead>
          <tbody id="ga-module-tbody">
            ${byModule.map(m => `<tr data-fit="${m.fit}" data-part="${m.part}" data-gap="${m.gap}" data-ricef="${m.ricef}" data-module="${m.m}">
              <td style="font-weight:600;font-size:12.5px">${m.m}</td>
              <td style="text-align:center">${m.total}</td>
              <td style="text-align:center;color:var(--success);font-weight:700">${m.fit}</td>
              <td style="text-align:center;color:var(--warning);font-weight:700">${m.part}</td>
              <td style="text-align:center;color:var(--danger);font-weight:700">${m.gap}</td>
              <td style="text-align:center;color:#4f46e5;font-weight:700">${m.ricef||"—"}</td>
              <td>${ragBar(m.fit,m.part,m.gap,m.total)}</td>
              <td>${progBar(m.decided,m.total)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>

    <!-- ── WRICEF Inventory ── -->
    <div class="section-divider">🔧 WRICEF Inventory — ${ricefRows.length} items</div>
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:20px">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th style="width:32px">#</th>
            <th style="width:80px">RICEF ID</th>
            <th style="width:130px">Type</th>
            <th style="max-width:180px">Title</th>
            <th style="width:100px">Scope Item</th>
            <th>Module</th>
            <th>Extensibility</th>
            <th style="width:60px;text-align:center">BTP</th>
            <th style="width:55px;text-align:center">Effort</th>
            <th style="width:55px">Action</th>
          </tr></thead>
          <tbody id="ga-ricef-tbody">${ricefRegister}</tbody>
        </table>
      </div>
    </div>

    <!-- AI Analysis panel -->
    <div id="ai-analysis-panel" style="margin-top:24px"></div>`;
}

function bindGapAnalysis() {
  // "Edit →" buttons in top-risks table jump to that KDD in the editor
  window._editorJumpTo = (kddId) => {
    if (window.toggleExpand) window.toggleExpand(kddId);
    const row = document.getElementById(`row-${kddId}`);
    if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  // ── Global filter — tiles, matrix, or clear ──────────────────────────────
  const FIELD_LABELS = {
    fitgap:           "Fit/Gap",
    rag:              "RAG",
    approved:         "Sign-off",
    decisions:        "Decisions",
    ricef:            "RICEF",
    complexity_fitgap:"Risk Matrix"
  };

  window.gaFilter = (field, value) => {
    const scopeTbody  = document.getElementById("ga-scope-tbody");
    const moduleTbody = document.getElementById("ga-module-tbody");
    const ricefTbody  = document.getElementById("ga-ricef-tbody");
    const filterBar   = document.getElementById("ga-filter-bar");
    const filterLbl   = document.getElementById("ga-filter-label");
    const countEl     = document.getElementById("ga-scope-count");
    if (!scopeTbody) return;

    const scopeRows = Array.from(scopeTbody.querySelectorAll("tr"));

    // ── Highlight active tile ──────────────────────────────────────────────
    document.querySelectorAll("[data-ga-tile]").forEach(el => {
      el.style.border = "1.5px solid transparent";
      el.style.boxShadow = "";
    });
    if (field && value) {
      const active = document.querySelector(`[data-ga-tile="${field}||${value}"]`);
      if (active) active.style.border = "1.5px solid var(--accent)";
    }

    if (!field || !value) {
      // Clear all filters
      scopeRows.forEach(tr => tr.style.display = "");
      if (moduleTbody) Array.from(moduleTbody.querySelectorAll("tr")).forEach(tr => tr.style.display = "");
      if (ricefTbody)  Array.from(ricefTbody.querySelectorAll("tr")).forEach(tr => tr.style.display = "");
      if (filterBar) filterBar.style.display = "none";
      if (countEl)   countEl.textContent = `(${scopeRows.length} items)`;
      return;
    }

    // ── Build friendly label ───────────────────────────────────────────────
    let labelText = `${FIELD_LABELS[field] || field}: ${value}`;

    // ── Row matcher — returns true if scope-item TR matches the filter ─────
    const matchRow = tr => {
      const fit  = parseInt(tr.dataset.fit  ||"0",10);
      const part = parseInt(tr.dataset.part ||"0",10);
      const gap  = parseInt(tr.dataset.gap  ||"0",10);
      switch (field) {
        case "fitgap":
          if (value==="Fit")         return fit  > 0;
          if (value==="Partial Fit") return part > 0;
          if (value==="Gap")         return gap  > 0;
          return false;
        case "rag":
          if (value==="Red")   return parseInt(tr.dataset.red  ||"0",10) > 0;
          if (value==="Amber") return parseInt(tr.dataset.amber||"0",10) > 0;
          return false;
        case "approved":
          return parseInt(tr.dataset.approved||"0",10) > 0;
        case "decisions":
          return parseInt(tr.dataset.approved||"0",10) > 0; // same proxy
        case "ricef":
          return parseInt(tr.dataset.ricef||"0",10) > 0;
        case "complexity_fitgap": {
          const [cxFilter, fgFilter] = value.split("|");
          // Filter by fit/gap AND by whether the scope item has rows of that complexity
          const cxList = (tr.dataset.complexities || "").split(",");
          let fgMatch = false;
          if (fgFilter==="Fit")         fgMatch = fit  > 0;
          if (fgFilter==="Partial Fit") fgMatch = part > 0;
          if (fgFilter==="Gap")         fgMatch = gap  > 0;
          labelText = `Risk Matrix: ${cxFilter} complexity, ${fgFilter}`;
          return fgMatch && cxList.includes(cxFilter);
        }
        default: return true;
      }
    };

    // ── Filter scope item table ────────────────────────────────────────────
    let shown = 0;
    scopeRows.forEach(tr => {
      const m = matchRow(tr);
      tr.style.display = m ? "" : "none";
      if (m) shown++;
    });
    if (filterBar) filterBar.style.display = "flex";
    if (filterLbl) filterLbl.textContent = labelText;
    if (countEl)   countEl.textContent = `(${shown} of ${scopeRows.length} items)`;

    // ── Filter module pivot table ──────────────────────────────────────────
    if (moduleTbody) {
      Array.from(moduleTbody.querySelectorAll("tr")).forEach(tr => {
        const fit  = parseInt(tr.dataset.fit  ||"0",10);
        const part = parseInt(tr.dataset.part ||"0",10);
        const gap  = parseInt(tr.dataset.gap  ||"0",10);
        let m = true;
        switch (field) {
          case "fitgap":
            if (value==="Fit")         m = fit  > 0; break;
            if (value==="Partial Fit") m = part > 0; break;
            if (value==="Gap")         m = gap  > 0; break;
          case "rag":
            if (value==="Red")   m = gap  > 0; break;  // red = gap rows exist
            if (value==="Amber") m = part > 0; break;
          case "ricef":
            m = parseInt(tr.dataset.ricef||"0",10) > 0; break;
          default: m = true;
        }
        tr.style.display = m ? "" : "none";
      });
    }

    // ── Filter RICEF register table ────────────────────────────────────────
    if (ricefTbody) {
      Array.from(ricefTbody.querySelectorAll("tr")).forEach(tr => {
        let m = true;
        switch (field) {
          case "fitgap":
            m = (tr.dataset.fitgap || "") === value; break;
          case "rag":
            if (value==="Red")   m = tr.dataset.fitgap === "Gap"; break;
            if (value==="Amber") m = tr.dataset.fitgap === "Partial Fit" || tr.dataset.fitgap === "Gap"; break;
          case "ricef":
            m = true; break; // register only shows RICEF items, all match
          default: m = true;
        }
        tr.style.display = m ? "" : "none";
      });
    }
  };

  // ── AI Gap Analysis — 3-step agentic SSE ─────────────────────────────────
  // Step 1: local stats (instant)
  // Step 2: Claude — cross-scope integration risks
  // Step 3: Claude — gap themes + prioritised recommendations
  // Step 2 result (risk scope items) feeds into the Step 3 prompt — this is the agentic chain.
  window.runAIAnalysis = async () => {
    const btn   = document.getElementById("run-ai-btn");
    const panel = document.getElementById("ai-analysis-panel");
    if (!panel) return;

    if (btn) { btn.disabled = true; btn.textContent = "⏳ Analysing…"; }

    panel.innerHTML = `
      <div class="section-divider">🤖 AI Gap Analysis — Powered by Claude</div>
      <div style="margin-bottom:16px">
        <div class="ai-step" id="ai-step-1" style="padding:8px 0;font-size:13px;color:var(--text-muted)">⬜ Step 1: Analysing KDD data…</div>
        <div class="ai-step" id="ai-step-2" style="padding:8px 0;font-size:13px;color:var(--text-muted)">⬜ Step 2: Identifying cross-scope integration risks (Claude)…</div>
        <div class="ai-step" id="ai-step-3" style="padding:8px 0;font-size:13px;color:var(--text-muted)">⬜ Step 3: Prioritising gaps and generating recommendations (Claude)…</div>
      </div>
      <div id="ai-output"></div>`;

    const STEP_LABELS = [
      "📊 Step 1: KDD data analysed",
      "🔗 Step 2: Integration risks identified",
      "🎯 Step 3: Gaps prioritised"
    ];

    const setStep = (n, state, warn) => {
      const el = document.getElementById(`ai-step-${n}`);
      if (!el) return;
      const icon = state === "done" ? "✅" : state === "warn" ? "⚠️" : "⏳";
      const color = state === "done" ? "var(--success)" : state === "warn" ? "var(--warning)" : "var(--accent)";
      el.style.color = color;
      el.textContent = `${icon} ${STEP_LABELS[n-1]}${warn ? " — " + warn : ""}`;
    };

    try {
      const resp = await fetch("/api/kdd/gap-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: "Server error " + resp.status }));
        throw new Error(err.error || "Server error " + resp.status);
      }

      const reader  = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();  // keep incomplete line for next chunk

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

          if (evt.type === "step")      { setStep(evt.step, "active"); }
          if (evt.type === "step_done") { setStep(evt.step, "done"); }
          if (evt.type === "step_warn") { setStep(evt.step, "warn", evt.message); }
          if (evt.type === "error")     {
            document.getElementById("ai-output").innerHTML =
              `<div class="alert alert-warning">⚠️ ${evt.message}</div>`;
          }
          if (evt.type === "complete")  {
            renderAIResults(evt, document.getElementById("ai-output"));
          }
        }
      }
    } catch (e) {
      const out = document.getElementById("ai-output");
      if (out) out.innerHTML = `<div class="alert alert-warning">⚠️ ${e.message}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "🤖 Run AI Analysis"; }
    }
  };

  // ── Render AI results into the output div ─────────────────────────────────
  function renderAIResults(evt, container) {
    if (!container) return;
    const { stats, risks, analysis } = evt;
    let html = "";

    // Stats strip
    if (stats) {
      html += `<div class="stats-grid" style="grid-template-columns:repeat(auto-fill,minmax(110px,1fr));margin-bottom:20px">
        <div class="card"><div class="card-title">🔴 Red RAG</div>
          <div class="card-value" style="color:var(--danger)">${stats.totalRed}</div></div>
        <div class="card"><div class="card-title">Total Gaps</div>
          <div class="card-value">${stats.totalGap}</div></div>
        <div class="card"><div class="card-title">RICEF Items</div>
          <div class="card-value">${stats.totalRICEF}</div></div>
        <div class="card"><div class="card-title">Open Decisions</div>
          <div class="card-value" style="color:var(--warning)">${stats.openDecisions}</div></div>
        <div class="card"><div class="card-title">✅ Approved</div>
          <div class="card-value" style="color:var(--success)">${stats.approved}</div></div>
      </div>`;
    }

    // Integration risks from Step 2
    if (risks && risks.length) {
      html += `<div class="section-divider" style="margin-top:0">🔗 Cross-Scope Integration Risks</div>
        <div style="display:grid;gap:10px;margin-bottom:20px">`;
      risks.forEach(r => {
        const sev = r.severity === "High" ? "var(--danger)" : r.severity === "Medium" ? "var(--warning)" : "var(--success)";
        html += `<div class="card" style="border-left:3px solid ${sev};padding:12px 16px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
            <span class="badge" style="background:${sev};color:#fff;min-width:54px;text-align:center">${r.severity}</span>
            <strong style="font-size:13.5px">${r.title || ""}</strong>
            ${(r.scopeItems||[]).map(s=>`<span class="badge badge-purple">${s}</span>`).join("")}
          </div>
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:5px">⚙ ${r.integrationPoint || ""}</div>
          <div style="font-size:13px;margin-bottom:6px">${r.description || ""}</div>
          <div style="font-size:12.5px;color:var(--accent)">→ ${r.action || ""}</div>
        </div>`;
      });
      html += `</div>`;
    }

    // Gap themes + recommendations from Step 3
    if (analysis) {
      if (analysis.healthSummary) {
        html += `<div style="background:rgba(139,92,246,0.12);border:1px solid var(--accent);border-radius:8px;
                   padding:12px 16px;margin-bottom:14px;font-size:13.5px">
          💡 <strong>Health Summary:</strong> ${analysis.healthSummary}</div>`;
      }
      if (analysis.topAction) {
        html += `<div class="alert alert-warning" style="margin-bottom:14px">
          🎯 <strong>Top Priority Action:</strong> ${analysis.topAction}</div>`;
      }
      if (analysis.workshopFocus) {
        html += `<div style="background:rgba(5,150,105,0.1);border:1px solid var(--success);border-radius:8px;
                   padding:12px 16px;margin-bottom:20px;font-size:13px">
          📋 <strong>Workshop Focus:</strong> ${analysis.workshopFocus}</div>`;
      }
      if (analysis.themes && analysis.themes.length) {
        html += `<div class="section-divider" style="margin-top:0">🎯 Gap Themes — Prioritised by Business Impact</div>`;
        analysis.themes.forEach(theme => {
          const pClr = theme.priority === "Critical" ? "var(--danger)"
                     : theme.priority === "High"     ? "var(--warning)"
                     : "var(--accent)";
          html += `<div class="card" style="margin-bottom:12px;padding:0;overflow:hidden">
            <div style="padding:10px 16px;background:rgba(255,255,255,0.04);border-bottom:1px solid rgba(255,255,255,0.06);
                        display:flex;align-items:center;gap:10px">
              <span class="badge" style="background:${pClr};color:#fff">${theme.priority}</span>
              <strong>${theme.theme}</strong>
              <span style="color:var(--text-muted);font-size:12px">${(theme.items||[]).length} items</span>
            </div>
            <div>
              ${(theme.items||[]).map(it=>`
                <div style="display:grid;grid-template-columns:130px 1fr;gap:8px;padding:10px 16px;
                            border-bottom:1px solid rgba(255,255,255,0.04)">
                  <div><span class="badge badge-purple" style="font-size:10.5px">${it.kddId||""}</span></div>
                  <div>
                    <div style="font-size:12.5px;margin-bottom:4px">${it.question||""}</div>
                    <div style="font-size:12px;color:var(--accent)">→ ${it.approach||""}</div>
                  </div>
                </div>`).join("")}
            </div>
          </div>`;
        });
      }
    }

    container.innerHTML = html ||
      `<div class="alert alert-warning">No AI results returned — Claude may be unavailable, try again.</div>`;
  }
}

// ── KDD EDITOR page ───────────────────────────────────────────────────────────
// Loads kdd-data.json, renders an editable table, saves changes back,
// runs Claude quality critique, exports final Excel, marks as ready.

async function pagEditor() {
  let data;
  try {
    data = await api.get("/api/kdd/data");
  } catch (e) {
    data = { error: e.message };
  }

  if (data.error) {
    return `
      <div class="page-header">
        <div class="page-title">KDD Editor</div>
        <div class="page-sub">Review · Edit · Export</div>
      </div>
      <div class="alert alert-warning" style="margin-top:8px">
        📋 No KDD data yet.
        <a href="#kdd-generator" style="color:var(--accent);font-weight:600">Generate a KDD log first →</a>
      </div>`;
  }

  const rows = data.rows || [];
  const meta = data.meta || {};

  // Stats
  const fitCount      = rows.filter(r => r.fitGap === "Fit").length;
  const gapCount      = rows.filter(r => r.fitGap === "Gap").length;
  const partialCount  = rows.filter(r => r.fitGap === "Partial Fit").length;
  const doneCount     = rows.filter(r => r.decisionMade && r.decisionMade.trim()).length;
  const approvedCount = rows.filter(r => r.signOffStatus === "Approved").length;
  const deferredCount = rows.filter(r => r.signOffStatus === "Deferred").length;
  const rejectedCount = rows.filter(r => r.signOffStatus === "Rejected").length;
  const bpdCount      = rows.filter(r => r.bpdRef && r.bpdRef.trim()).length;

  // Scope item filter options
  const scopeIds = [...new Set(rows.map(r => r.scopeItemId))];

  const ricefRows   = rows.filter(r => r.ricefType && r.ricefType !== "None");
  const ricefByMod  = {};  ricefRows.forEach(r => { const m = modName(r); ricefByMod[m] = (ricefByMod[m]||0)+1; });
  const ricefByType = {};  ricefRows.forEach(r => { ricefByType[r.ricefType] = (ricefByType[r.ricefType]||0)+1; });

  return `
    <!-- ── Page header (always visible above tabs) ──────────────────────────── -->
    <div class="page-header flex items-center justify-between" style="flex-wrap:wrap;gap:10px;margin-bottom:0">
      <div>
        <div class="page-title">KDD Editor</div>
        <div class="page-sub" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span>${meta.client || "—"} · ${meta.project || "—"} · ${rows.length} KDDs · ${scopeIds.length} scope items
          ${meta.lastEditedAt ? `· last saved ${new Date(meta.lastEditedAt).toLocaleTimeString("en-GB")}` : ""}</span>
          <button id="proj-switch-btn" onclick="toggleProjectPanel()"
            style="background:#ede9fe;color:#6d28d9;border:1px solid #ddd6fe;border-radius:6px;
                   padding:3px 10px;font-size:11.5px;font-weight:600;cursor:pointer">
            📁 Projects
          </button>
        </div>
      </div>
      <div class="editor-toolbar">
        <span id="save-indicator" class="save-indicator clean" title="Ctrl+S to save">✔ Saved</span>
        <button class="btn btn-outline btn-sm" id="view-toggle-btn" onclick="toggleViewMode()">⊞ Card View</button>
        <button class="btn btn-outline btn-sm" onclick="openWorkshopModal()">📋 Workshop</button>
        <button class="btn btn-outline btn-sm" id="critique-btn" onclick="runCritique()">⚡ Quality Check</button>
        <button class="btn btn-outline btn-sm" id="save-btn" onclick="saveEdits()">💾 Save</button>
        <button class="btn btn-primary btn-sm" id="export-btn" onclick="exportExcel()">⬇ Export Excel</button>
        <button class="btn btn-outline btn-sm" id="ppt-btn" onclick="exportPPT()">📊 PPT</button>
        <button class="btn btn-sm" style="background:#0f766e;color:#fff;padding:6px 14px;border-radius:6px;font-size:12.5px;font-weight:600"
          onclick="openPresenter()">🎭 Presenter</button>
      </div>
    </div>

    <!-- ── Tab bar ────────────────────────────────────────────────────────────── -->
    <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin:14px 0 18px">
      <button id="kdd-tab-overview" onclick="kddSwitchTab('overview')"
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:pointer;
               background:none;border-bottom:2px solid var(--border);color:var(--text-muted);
               margin-bottom:-2px;transition:color .15s">
        📊 Overview
      </button>
      <button id="kdd-tab-editor" onclick="kddSwitchTab('editor')"
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:pointer;
               background:none;border-bottom:2px solid var(--accent);color:var(--accent);
               margin-bottom:-2px;transition:color .15s">
        ✏️ Editor
      </button>
      <button id="kdd-tab-wricef" onclick="kddSwitchTab('wricef')"
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:pointer;
               background:none;border-bottom:2px solid transparent;color:var(--text-muted);
               margin-bottom:-2px;transition:color .15s">
        🔧 WRICEF Inventory
      </button>
    </div>

    <!-- ══ OVERVIEW TAB ══════════════════════════════════════════════════════════ -->
    <div id="kdd-panel-overview" style="display:none">

      <div class="editor-stats">
        <div class="editor-stat"><div class="editor-stat-val">${rows.length}</div><div class="editor-stat-lbl">Total KDDs</div></div>
        <div class="editor-stat"><div class="editor-stat-val" style="color:var(--success)">${fitCount}</div><div class="editor-stat-lbl">Fit</div></div>
        <div class="editor-stat"><div class="editor-stat-val" style="color:var(--warning)">${partialCount}</div><div class="editor-stat-lbl">Partial Fit</div></div>
        <div class="editor-stat"><div class="editor-stat-val" style="color:var(--danger)">${gapCount}</div><div class="editor-stat-lbl">Gap</div></div>
        <div class="editor-stat"><div class="editor-stat-val" style="color:var(--accent)">${doneCount}</div><div class="editor-stat-lbl">Decisions Made</div></div>
        <div class="editor-stat" style="border-top:2px solid #86efac">
          <div class="editor-stat-val" style="color:var(--success)">${approvedCount}</div>
          <div class="editor-stat-lbl">✅ Approved</div>
        </div>
        <div class="editor-stat" style="border-top:2px solid #fbbf24">
          <div class="editor-stat-val" style="color:var(--warning)">${deferredCount}</div>
          <div class="editor-stat-lbl">⏸ Deferred</div>
        </div>
        <div class="editor-stat" style="border-top:2px solid #f87171">
          <div class="editor-stat-val" style="color:var(--danger)">${rejectedCount}</div>
          <div class="editor-stat-lbl">❌ Rejected</div>
        </div>
        <div class="editor-stat" style="border-top:2px solid var(--accent)">
          <div class="editor-stat-val" style="color:var(--accent)">${bpdCount}</div>
          <div class="editor-stat-lbl">📋 BPD Refs</div>
        </div>
        <div class="editor-stat" style="border-top:2px solid #4f46e5">
          <div class="editor-stat-val" style="color:#4f46e5">${ricefRows.length}</div>
          <div class="editor-stat-lbl">🔧 WRICEF Items</div>
        </div>
      </div>

      ${ricefRows.length ? `
      <div class="card" style="margin-top:12px">
        <div class="card-title">WRICEF Breakdown</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <span style="font-size:12px;font-weight:700;color:#4f46e5">By Module</span>
          ${Object.entries(ricefByMod).sort((a,b)=>b[1]-a[1]).map(([m,n])=>
            `<span style="background:#ede9fe;color:#4f46e5;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:600">${m}: ${n}</span>`).join("")}
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-size:12px;font-weight:700;color:#0369a1">By Type</span>
          ${Object.entries(ricefByType).sort((a,b)=>b[1]-a[1]).map(([t,n])=>
            `<span style="background:#f0f9ff;color:#0369a1;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:600">${t}: ${n}</span>`).join("")}
        </div>
      </div>` : ""}

      <div class="card" style="margin-top:12px">
        <div class="card-title">Project Details</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;font-size:13px">
          <div><span style="color:var(--text-muted)">Client</span><br><strong>${meta.client || "—"}</strong></div>
          <div><span style="color:var(--text-muted)">Project</span><br><strong>${meta.project || "—"}</strong></div>
          <div><span style="color:var(--text-muted)">Scope Items</span><br><strong>${scopeIds.join(", ") || "—"}</strong></div>
          <div><span style="color:var(--text-muted)">Generated</span><br><strong>${meta.generatedAt ? new Date(meta.generatedAt).toLocaleDateString("en-GB") : "—"}</strong></div>
          <div><span style="color:var(--text-muted)">Last Saved</span><br><strong>${meta.lastEditedAt ? new Date(meta.lastEditedAt).toLocaleString("en-GB") : "—"}</strong></div>
        </div>
        <div style="margin-top:14px">
          <button class="btn btn-primary btn-sm" onclick="kddSwitchTab('editor')">Open Editor →</button>
        </div>
      </div>

    </div><!-- /kdd-panel-overview -->

    <!-- ══ EDITOR TAB (default active) ══════════════════════════════════════════ -->
    <div id="kdd-panel-editor">

    <!-- Filters -->
    <div class="card" style="padding:12px 16px;margin-bottom:12px">
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <select id="filter-scope" class="form-select" style="width:auto;min-width:150px" onchange="applyFilters()">
          <option value="">All Scope Items</option>
          ${scopeIds.map(id => `<option value="${id}">${id}</option>`).join("")}
        </select>
        <select id="filter-status" class="form-select" style="width:auto;min-width:130px" onchange="applyFilters()">
          <option value="">All Status</option>
          <option value="Open">Open</option>
          <option value="In Progress">In Progress</option>
          <option value="Deferred">Deferred</option>
          <option value="Approved">Approved</option>
          <option value="Decision Made">Decision Made</option>
          <option value="Closed">Closed</option>
        </select>
        <select id="filter-fitgap" class="form-select" style="width:auto;min-width:130px" onchange="applyFilters()">
          <option value="">All Fit/Gap</option>
          <option value="Fit">Fit</option>
          <option value="Partial Fit">Partial Fit</option>
          <option value="Gap">Gap</option>
        </select>
        <select id="filter-signoff" class="form-select" style="width:auto;min-width:140px" onchange="applyFilters()">
          <option value="">All Sign-off</option>
          <option value="Open">Open</option>
          <option value="Approved">✅ Approved</option>
          <option value="Deferred">⏸ Deferred</option>
          <option value="Rejected">❌ Rejected</option>
        </select>
        <input id="filter-search" class="form-input" placeholder="🔍 Search questions or BPD ref…"
          style="flex:1;min-width:180px" oninput="applyFilters()" />
      </div>
    </div>

    <div id="critique-panel"></div>
    <div id="export-result"></div>

    <!-- Editable table / card view -->
    <div id="kdd-table-wrap" class="card" style="padding:0;overflow:hidden">
      <div class="table-wrap">
        <table id="kdd-table">
          <thead>
            <tr>
              <th style="width:90px">KDD ID</th>
              <th style="width:70px">Item</th>
              <th style="width:130px">LoB</th>
              <th>Design Question</th>
              <th style="width:100px">Fit / Gap</th>
              <th style="width:115px">Resolution</th>
              <th style="width:100px">Status</th>
              <th style="width:120px">Owner</th>
              <th style="width:80px"></th>
            </tr>
          </thead>
          <tbody id="kdd-tbody"></tbody>
        </table>
      </div>
    </div>
    <div id="kdd-card-view" style="display:none">
      <div id="kdd-cards-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;padding:16px"></div>
    </div>

    </div><!-- /kdd-panel-editor -->

    <!-- ══ WRICEF INVENTORY TAB ═══════════════════════════════════════════════════ -->
    <div id="kdd-panel-wricef" style="display:none">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px">
        <div>
          <div style="font-size:15px;font-weight:700;color:var(--text)">🔧 WRICEF Inventory</div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:2px">All Gap rows with a WRICEF resolution — ready for solution board review</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <span id="wricef-count-badge" style="background:#ede9fe;color:#4f46e5;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700"></span>
          <button class="btn btn-primary btn-sm" id="wricef-export-btn" onclick="exportWRICEFExcel()">⬇ Export Excel</button>
        </div>
      </div>
      <div id="wricef-export-result"></div>

      <!-- Summary strip -->
      <div id="wricef-summary-strip" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px"></div>

      <!-- Table -->
      <div class="card" style="padding:0;overflow:hidden">
        <div class="table-wrap">
          <table id="wricef-table">
            <thead><tr>
              <th style="width:90px">WRICEF ID</th>
              <th style="width:70px">KDD ID</th>
              <th style="width:70px">Item</th>
              <th style="width:120px">Module</th>
              <th>Design Question</th>
              <th style="width:110px">WRICEF Type</th>
              <th style="width:90px">Complexity</th>
              <th style="width:90px">Extensibility</th>
              <th style="width:70px;text-align:center">BTP</th>
              <th style="width:55px;text-align:center">Effort</th>
              <th style="width:100px">Status</th>
              <th style="width:110px">Owner</th>
            </tr></thead>
            <tbody id="wricef-tbody"></tbody>
          </table>
        </div>
      </div>
    </div><!-- /kdd-panel-wricef -->

    <!-- ── Workshop Presenter Overlay ── -->
    <div id="presenter-overlay">
      <div class="presenter-header">
        <div style="display:flex;align-items:center;gap:14px">
          <span style="font-size:18px">🎭</span>
          <div>
            <div style="font-weight:700;color:#e2d9f3;font-size:14px">Workshop Presenter</div>
            <div style="font-size:11px;color:#9d7fe5">Open decisions only · click a card to pre-fill · Esc to exit</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:14px">
          <span id="p-progress" style="color:#9d7fe5;font-size:13px;font-weight:600"></span>
          <button onclick="closePresenter()"
            style="background:#4c2b9e;color:#e2d9f3;border:none;padding:6px 14px;border-radius:6px;
                   font-size:13px;font-weight:600;cursor:pointer">✕ Exit</button>
        </div>
      </div>

      <div class="presenter-body">
        <!-- Left: context panel -->
        <div class="presenter-left">
          <div>
            <div class="p-label">KDD ID</div>
            <div id="p-kddid" class="p-value" style="font-family:monospace;color:#a78bfa;font-size:14px"></div>
          </div>
          <div>
            <div class="p-label">Scope Item</div>
            <div id="p-scope" class="p-value"></div>
          </div>
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <div>
              <div class="p-label">Fit / Gap</div>
              <div id="p-fitgap" class="p-value" style="font-weight:700"></div>
            </div>
            <div>
              <div class="p-label">Complexity</div>
              <div id="p-complexity" class="p-value"></div>
            </div>
            <div>
              <div class="p-label">RAG</div>
              <div id="p-rag" class="p-value" style="font-weight:700"></div>
            </div>
          </div>
          <div>
            <div class="p-label">Rationale</div>
            <div id="p-rationale" class="p-value" style="font-size:12px"></div>
          </div>
          <div>
            <div class="p-label">Impact if Not Decided</div>
            <div id="p-impact" class="p-value" style="font-size:12px"></div>
          </div>
          <div>
            <div class="p-label">Fiori App / Config</div>
            <div id="p-notes" class="p-value" style="font-size:12px;color:#a78bfa"></div>
          </div>
        </div>

        <!-- Right: question + decision panel -->
        <div class="presenter-right">
          <div id="p-question" class="p-question"></div>

          <div>
            <div class="p-label" style="color:#9d7fe5;margin-bottom:6px">📝 Decision Made</div>
            <textarea id="p-decision" class="p-decision-ta" rows="4"
              placeholder="Type or click a suggestion below to fill…"></textarea>
          </div>

          <div>
            <button id="p-suggest-btn" onclick="presenterSuggest()"
              style="background:#1a0f36;border:1.5px solid #4c2b9e;color:#a78bfa;padding:7px 16px;
                     border-radius:7px;font-size:13px;font-weight:600;cursor:pointer">
              💡 Suggest Decisions
            </button>
            <div id="p-suggest-cards" style="margin-top:10px"></div>
          </div>

          <div style="display:flex;gap:10px;align-items:center">
            <button id="p-save-btn" onclick="savePresenterDecision()"
              style="background:#7c3aed;color:#fff;padding:8px 20px;border-radius:7px;
                     font-size:13px;font-weight:600;cursor:pointer;border:none">
              💾 Save Decision
            </button>
            <span style="font-size:12px;color:#6b5fa0">Decision auto-saves to the editor · continue to next KDD</span>
          </div>
        </div>
      </div>

      <div class="presenter-footer">
        <button id="p-prev" class="presenter-nav-btn" onclick="presenterNav(-1)">◀ Previous</button>
        <div style="display:flex;gap:8px">
          <button onclick="togglePresenterFilter()"
            style="background:#2d1b6b;color:#9d7fe5;border:1px solid #4c2b9e;padding:6px 14px;
                   border-radius:6px;font-size:12px;cursor:pointer" id="p-filter-btn">
            Show: Open Only
          </button>
        </div>
        <button id="p-next" class="presenter-nav-btn" onclick="presenterNav(1)">Next ▶</button>
      </div>
    </div>`;
}

function bindEditor() {
  // State — loaded once, mutated on every edit, saved on demand
  let _rows          = [];
  let _meta          = {};
  let _dirty         = false;
  let _expanded      = null;   // currently expanded kddId
  let _ratings       = {};     // kddId → "specific" | "generic"
  let _autoSaveTimer = null;
  let _wsSelected    = new Set();
  let _viewMode      = "table"; // "table" | "cards"
  let _lobs          = [];      // LOB names from catalog — loaded on init

  // ── KDD tab switching ─────────────────────────────────────────────────────────
  window.kddSwitchTab = (tab) => {
    ["overview", "editor", "wricef"].forEach(t => {
      const btn   = document.getElementById(`kdd-tab-${t}`);
      const panel = document.getElementById(`kdd-panel-${t}`);
      const active = t === tab;
      if (btn) {
        btn.style.borderBottomColor = active ? "var(--accent)" : "transparent";
        btn.style.color             = active ? "var(--accent)" : "var(--text-muted)";
      }
      if (panel) panel.style.display = active ? "block" : "none";
    });
    if (tab === "wricef") renderWRICEFInventory();
  };

  // ── WRICEF Inventory ─────────────────────────────────────────────────────────
  const WRICEF_TYPE_LABELS = {
    W:"W – Workflow", R:"R – Report", I:"I – Interface", C:"C – Conversion",
    E_RAP:"E – BTP RAP", E_CAPM:"E – BTP CAPM", E_RAP_CAPM:"E – RAP & CAPM & UI", F:"F – Form"
  };

  function renderWRICEFInventory() {
    const wRows = _rows.filter(r => r.gapResolution === "WRICEF" && r.ricefType && r.ricefType !== "None");
    const tbody  = document.getElementById("wricef-tbody");
    const badge  = document.getElementById("wricef-count-badge");
    const strip  = document.getElementById("wricef-summary-strip");
    if (!tbody) return;

    // Count badge
    if (badge) badge.textContent = `${wRows.length} item${wRows.length !== 1 ? "s" : ""}`;

    // Summary strip — by type
    if (strip) {
      const byType = {};
      wRows.forEach(r => { byType[r.ricefType] = (byType[r.ricefType] || 0) + 1; });
      strip.innerHTML = Object.entries(byType).sort((a,b) => b[1]-a[1]).map(([t, n]) =>
        `<span style="background:#ede9fe;color:#4f46e5;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:600">
           ${WRICEF_TYPE_LABELS[t]||t}: ${n}
         </span>`
      ).join("") + (wRows.length === 0 ? `<span style="color:var(--text-muted);font-size:13px">No WRICEF items yet — set Resolution = WRICEF on Gap rows in the Editor tab.</span>` : "");
    }

    if (!wRows.length) {
      tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;padding:32px;color:var(--text-muted)">
        No WRICEF items yet — go to Editor tab and set Resolution = WRICEF on Gap rows.
      </td></tr>`;
      return;
    }

    tbody.innerHTML = wRows.map((r, i) => {
      const effortColor = {XL:"#dc2626",L:"#d97706",M:"#0369a1",S:"#059669"}[r.effortEstimate] || "var(--text-muted)";
      const btpEl = r.btpRequired === "Yes"
        ? `<span style="color:#dc2626;font-weight:700">Yes</span>`
        : r.btpRequired === "Possible"
          ? `<span style="color:#d97706">Possible</span>` : "No";
      return `<tr>
        <td style="font-family:monospace;font-size:11px;color:var(--accent);font-weight:700">${r.ricefId || "—"}</td>
        <td style="font-family:monospace;font-size:11px;color:var(--text-muted)">${r.kddId}</td>
        <td><span class="badge badge-purple" style="font-size:10px">${r.scopeItemId}</span></td>
        <td style="font-size:12px">${r.module || r.l1 || "—"}</td>
        <td style="font-size:12px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${(r.designQuestion||"").replace(/"/g,"&quot;")}">${r.designQuestion ? (r.designQuestion.length > 80 ? r.designQuestion.slice(0,80)+"…" : r.designQuestion) : "—"}</td>
        <td><span style="background:#ede9fe;color:#4f46e5;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:700">${WRICEF_TYPE_LABELS[r.ricefType]||r.ricefType}</span></td>
        <td style="font-size:12px">${r.complexity || "—"}</td>
        <td style="font-size:11px;color:var(--text-muted)">${r.extensibilityType && r.extensibilityType !== "None" ? r.extensibilityType : "—"}</td>
        <td style="text-align:center">${btpEl}</td>
        <td style="text-align:center;font-weight:700;color:${effortColor}">${r.effortEstimate && r.effortEstimate !== "None" ? r.effortEstimate : "—"}</td>
        <td style="font-size:12px">${r.status || "Open"}</td>
        <td style="font-size:12px">${r.decisionOwner || "—"}</td>
      </tr>`;
    }).join("");
  }

  // ── Export WRICEF Inventory to Excel ─────────────────────────────────────────
  window.exportWRICEFExcel = async () => {
    const btn = document.getElementById("wricef-export-btn");
    const el  = document.getElementById("wricef-export-result");
    btn.disabled = true;
    btn.textContent = "Building…";
    if (el) el.innerHTML = "";

    // Save first if dirty
    if (_dirty) await window.saveEdits();

    try {
      const res  = await fetch("/api/kdd/export-wricef", { method: "POST" });
      const data = await res.json();
      if (data.ok && data.filename) {
        if (el) el.innerHTML = `
          <div class="alert alert-success" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:10px">
            <span>✅ <strong>${data.filename}</strong> — ${data.rowCount} WRICEF items</span>
            <a href="/api/output/download/${encodeURIComponent(data.filename)}"
               class="btn btn-primary btn-sm" download>⬇ Download</a>
          </div>`;
      } else {
        if (el) el.innerHTML = `<div class="alert alert-danger">⚠️ ${data.error || "Export failed"}</div>`;
      }
    } catch (e) {
      if (el) el.innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
    }
    btn.disabled = false;
    btn.textContent = "⬇ Export Excel";
    setTimeout(() => { if (el) el.innerHTML = ""; }, 8000);
  };

  // ── Project switcher ──
  window.toggleProjectPanel = async () => {
    let panel = document.getElementById("proj-panel");
    if (panel) { panel.remove(); return; }

    panel = document.createElement("div");
    panel.id = "proj-panel";
    panel.style.cssText = "background:#fff;border:1px solid var(--border);border-radius:10px;box-shadow:0 4px 24px rgba(0,0,0,.12);padding:16px 18px;margin-bottom:12px;min-width:360px;max-width:560px;position:relative";
    panel.innerHTML = `<div style="font-weight:700;font-size:13px;margin-bottom:12px;color:#6d28d9">📁 Saved Projects</div>
      <div id="proj-list" style="font-size:13px;color:var(--text-muted)">Loading…</div>`;

    // Insert after page-header
    const header = document.querySelector(".page-header");
    if (header && header.nextSibling) header.parentNode.insertBefore(panel, header.nextSibling);
    else document.getElementById("app").prepend(panel);

    const { projects } = await api.get("/api/projects").catch(() => ({ projects: [] }));
    const list = document.getElementById("proj-list");
    if (!list) return;

    if (!projects.length) {
      list.innerHTML = `<div style="color:var(--text-muted);font-size:12.5px">No saved projects yet — generate a KDD log to create one.</div>`;
      return;
    }
    list.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12.5px">
        <thead><tr style="border-bottom:1px solid var(--border)">
          <th style="text-align:left;padding:4px 8px;font-size:11px;color:var(--text-muted);font-weight:600">Client · Project</th>
          <th style="text-align:center;padding:4px 8px;font-size:11px;color:var(--text-muted);font-weight:600">KDDs</th>
          <th style="text-align:left;padding:4px 8px;font-size:11px;color:var(--text-muted);font-weight:600">Last saved</th>
          <th style="padding:4px 8px"></th>
        </tr></thead>
        <tbody>
          ${projects.map(p => `
            <tr style="border-bottom:1px solid #f3f4f6;${p.active?"background:#faf5ff;":""}" id="proj-row-${p.slug}">
              <td style="padding:7px 8px;font-weight:${p.active?700:400}">
                ${p.active?'<span style="color:#6d28d9;margin-right:5px">●</span>':''}${p.client} · ${p.project}
              </td>
              <td style="text-align:center;padding:7px 8px;color:var(--text-muted)">${p.rows}</td>
              <td style="padding:7px 8px;color:var(--text-muted);font-size:11.5px">
                ${p.savedAt ? new Date(p.savedAt).toLocaleDateString("en-GB",{day:"2-digit",month:"short",year:"2-digit"}) : "—"}
              </td>
              <td style="padding:7px 8px;text-align:right;white-space:nowrap">
                ${p.active
                  ? `<span style="font-size:11px;color:#6d28d9;font-weight:600">Active</span>`
                  : `<button onclick="loadProject('${p.slug}')"
                       style="background:#6d28d9;color:#fff;border:none;border-radius:5px;padding:3px 10px;font-size:11.5px;font-weight:600;cursor:pointer;margin-right:4px">Load</button>`}
                <button onclick="deleteProject('${p.slug}','${p.client} · ${p.project}')"
                  style="background:#fee2e2;color:#dc2626;border:1px solid #fecaca;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer">✕</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  };

  window.loadProject = async (slug) => {
    if (!confirm("Load this project? Any unsaved changes to the current project will be lost.")) return;
    const data = await api.post("/api/projects/load", { slug });
    if (data.ok) {
      const panel = document.getElementById("proj-panel");
      if (panel) panel.remove();
      navigate("editor");
    } else {
      alert("Error: " + (data.error || "Could not load project"));
    }
  };

  window.deleteProject = async (slug, label) => {
    if (!confirm(`Delete "${label}"?\n\nThis removes it from the project list. Current active data is not affected.`)) return;
    const row = document.getElementById(`proj-row-${slug}`);
    if (row) row.style.opacity = "0.4";
    const data = await fetch(`/api/projects/${encodeURIComponent(slug)}`, { method: "DELETE" }).then(r => r.json());
    if (data.ok) {
      if (row) row.remove();
    } else {
      if (row) row.style.opacity = "1";
      alert("Error: " + (data.error || "Could not delete"));
    }
  };

  // ── Keyboard shortcuts (Ctrl+S = save, Esc = close row) ──
  const keyHandler = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      window.saveEdits();
    }
    if (e.key === "Escape" && _expanded) {
      window.toggleExpand(_expanded);
    }
  };
  document.addEventListener("keydown", keyHandler);
  // Remove listener when navigating away
  const _removeKeys = () => {
    document.removeEventListener("keydown", keyHandler);
    window.removeEventListener("hashchange", _removeKeys);
  };
  window.addEventListener("hashchange", _removeKeys);

  // ── Load data + LOBs ──
  (async () => {
    try {
      const [data, lobs] = await Promise.all([
        api.get("/api/kdd/data"),
        api.get("/api/catalog/lobs").catch(() => [])
      ]);
      _rows = data.rows || [];
      _meta = data.meta || {};
      _lobs = lobs.map(l => l.name || l).filter(Boolean);
      renderTable(_rows);
    } catch (e) {
      console.error("Editor load error:", e);
    }
  })();

  function showToast(msg, type) {
    const t = document.createElement("div");
    t.className = "toast";
    if (type === "warn") t.style.background = "#92400e";
    if (type === "error") t.style.background = "#7f1d1d";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }

  function markDirty() {
    _dirty = true;
    const ind = document.getElementById("save-indicator");
    if (ind) { ind.className = "save-indicator dirty"; ind.textContent = "● Unsaved changes"; }
    // Auto-save 3 seconds after last edit
    clearTimeout(_autoSaveTimer);
    _autoSaveTimer = setTimeout(() => { if (_dirty) window.saveEdits(); }, 3000);
  }
  function markSaved() {
    _dirty = false;
    const ind = document.getElementById("save-indicator");
    if (ind) { ind.className = "save-indicator clean"; ind.textContent = "✔ Saved"; }
  }

  // ── Render table ──
  function renderTable(rows) {
    const tbody = document.getElementById("kdd-tbody");
    if (!tbody) return;
    tbody.innerHTML = rows.map(r => rowHTML(r)).join("");
  }

  function rowHTML(r) {
    const qShort  = r.designQuestion ? r.designQuestion.slice(0, 90) + (r.designQuestion.length > 90 ? "…" : "") : "—";
    const hasDec  = r.decisionMade && r.decisionMade.trim();
    const decPrev = hasDec ? r.decisionMade.slice(0, 50) + (r.decisionMade.length > 50 ? "…" : "") : `<span class="text-muted">— click to add</span>`;
    const ratingBadge = _ratings[r.kddId]
      ? (_ratings[r.kddId] === "generic"
          ? `<span class="badge badge-weak" title="${(_ratings[r.kddId + "_reason"] || "")}">⚠ Generic</span>`
          : `<span class="badge badge-ok">✔ Specific</span>`)
      : "";
    const signOffBadge = r.signOffStatus === "Approved" ? `<span class="badge badge-green">✅ Approved</span>`
      : r.signOffStatus === "Deferred"  ? `<span class="badge badge-yellow">⏸ Deferred</span>`
      : r.signOffStatus === "Rejected"  ? `<span class="badge badge-red">❌ Rejected</span>`
      : "";
    const fitClass = r.fitGap === "Gap" ? "badge-red" : r.fitGap === "Partial Fit" ? "badge-yellow" : "badge-green";

    const inScope = r.inScope !== false;  // default true
    return `
      <tr id="row-${r.kddId}" class="${hasDec ? "row-has-decision" : ""} ${_expanded === r.kddId ? "row-expanded" : ""}"
          style="cursor:pointer;${inScope ? "" : "opacity:0.42;background:#f9fafb;"}" onclick="toggleExpand('${r.kddId}')">
        <td style="font-family:monospace;font-size:11.5px;color:var(--accent)">
          ${r.kddId} ${ratingBadge}
          ${!inScope ? `<div style="font-size:9px;font-weight:700;color:#9ca3af;letter-spacing:.04em;margin-top:1px">OUT OF SCOPE</div>` : ""}
        </td>
        <td><span class="badge badge-purple">${r.scopeItemId}</span></td>
        <td onclick="event.stopPropagation()">
          <select class="cell-select" style="font-size:11.5px" onchange="updateField('${r.kddId}','module',this.value);updateField('${r.kddId}','l1',this.value)">
            ${(_lobs.length ? _lobs : ["Finance","Sales","Sourcing and Procurement","Manufacturing","Human Resources","Supply Chain","Service","Asset Management","IT Management","Other"])
              .map(v => `<option ${modName(r)===v?"selected":""}>${v}</option>`).join("")}
          </select>
        </td>
        <td style="font-size:12.5px;max-width:280px">${qShort}</td>
        <td onclick="event.stopPropagation()">
          <select class="cell-select"
            title="SAP Standard process accepted = Fit · Requires extension or custom build = Gap"
            onchange="updateField('${r.kddId}','fitGap',this.value)">
            ${["Fit","Gap"].map(v => `<option ${r.fitGap===v?"selected":""}>${v}</option>`).join("")}
          </select>
        </td>
        <td onclick="event.stopPropagation()" id="ricef-cell-${r.kddId}">
          ${r.fitGap === "Fit"
            ? `<select class="cell-select" disabled style="opacity:0.35;cursor:not-allowed"><option>—</option></select>`
            : `<div style="display:flex;flex-direction:column;gap:3px">
                <select class="cell-select" style="font-size:11px"
                  title="How will this gap be resolved?"
                  onchange="updateField('${r.kddId}','gapResolution',this.value)">
                  <option value="None" ${(r.gapResolution||"None")==="None"?"selected":""}>— Resolution —</option>
                  <option value="WRICEF" ${(r.gapResolution||"None")==="WRICEF"?"selected":""}>WRICEF</option>
                  <option value="Config" ${(r.gapResolution||"None")==="Config"?"selected":""}>Config</option>
                </select>
                ${(r.gapResolution||"None") === "WRICEF" ? `
                  <select class="cell-select" style="font-size:10.5px"
                    onchange="updateField('${r.kddId}','ricefType',this.value)">
                    <option value="None" ${(r.ricefType||"None")==="None"?"selected":""}>— Type —</option>
                    <option value="W" ${(r.ricefType||"None")==="W"?"selected":""}>W – Workflow</option>
                    <option value="R" ${(r.ricefType||"None")==="R"?"selected":""}>R – Report</option>
                    <option value="I" ${(r.ricefType||"None")==="I"?"selected":""}>I – Interface</option>
                    <option value="C" ${(r.ricefType||"None")==="C"?"selected":""}>C – Conversion</option>
                    <option value="E_RAP"      ${(r.ricefType||"None")==="E_RAP"?"selected":""}>E – BTP RAP</option>
                    <option value="E_CAPM"     ${(r.ricefType||"None")==="E_CAPM"?"selected":""}>E – BTP CAPM</option>
                    <option value="E_RAP_CAPM" ${(r.ricefType||"None")==="E_RAP_CAPM"?"selected":""}>E – RAP &amp; CAPM &amp; UI</option>
                    <option value="F" ${(r.ricefType||"None")==="F"?"selected":""}>F – Form</option>
                  </select>
                  ${r.ricefId ? `<div style="font-size:10px;color:var(--accent);font-family:monospace">${r.ricefId}</div>` : ""}
                ` : (r.gapResolution === "Config" ? `<div style="font-size:10px;color:#059669;font-weight:600;padding:1px 0">✓ Standard Config</div>` : "")}
              </div>`
          }
        </td>
        <td onclick="event.stopPropagation()">
          <select class="cell-select" onchange="updateField('${r.kddId}','status',this.value)">
            ${["Open","In Progress","Deferred","Approved","Decision Made","Closed"].map(v => `<option ${r.status===v?"selected":""}>${v}</option>`).join("")}
          </select>
        </td>
        <td onclick="event.stopPropagation()">
          <input class="cell-input" value="${(r.decisionOwner||"").replace(/"/g,"&quot;")}"
            placeholder="Owner…" onchange="updateField('${r.kddId}','decisionOwner',this.value)" />
        </td>
        <td onclick="event.stopPropagation()" style="text-align:center;padding:4px 6px">
          <div style="display:flex;flex-direction:column;gap:3px;align-items:center">
            <button onclick="updateField('${r.kddId}','inScope',${inScope ? "false" : "true"})"
              style="background:${inScope?"#dcfce7":"#f3f4f6"};border:1px solid ${inScope?"#86efac":"#d1d5db"};
                     color:${inScope?"#15803d":"#9ca3af"};cursor:pointer;
                     font-size:10px;font-weight:700;padding:2px 6px;line-height:1.4;border-radius:4px;width:100%"
              title="${inScope?"Exclude from scope":"Include in scope"}">${inScope ? "✓ In Scope" : "Excluded"}</button>
            <button onclick="deleteKDDRow('${r.kddId}')"
              style="background:none;border:1px solid var(--border);color:#9ca3af;cursor:pointer;
                     font-size:10px;font-weight:600;padding:2px 6px;line-height:1.4;border-radius:4px;width:100%;
                     transition:color .15s,background .15s,border-color .15s"
              onmouseover="this.style.color='#dc2626';this.style.background='#fee2e2';this.style.borderColor='#fca5a5'"
              onmouseout="this.style.color='#9ca3af';this.style.background='none';this.style.borderColor='var(--border)'"
              title="Permanently delete KDD ${r.kddId}">Delete</button>
          </div>
        </td>
      </tr>
      ${_expanded === r.kddId ? expandHTML(r) : ""}`;
  }

  function expandHTML(r) {
    return `
      <tr class="row-expand-content">
        <td colspan="10" style="padding:0">
          <div class="expand-panel" style="position:relative;display:block;padding:0">
            <!-- ── Close button ── -->
            <button onclick="event.stopPropagation();toggleExpand('${r.kddId}')"
              style="position:absolute;top:10px;right:12px;z-index:10;background:#f3f4f6;border:1px solid var(--border);
                     border-radius:6px;padding:3px 10px;font-size:12px;font-weight:600;cursor:pointer;color:var(--text-muted)"
              title="Close (Esc)">✕</button>

            <!-- ── Section 1: Design ── -->
            <div style="padding:16px 20px 14px;border-bottom:1px solid #e0e7ff">
              <!-- Row A: LoB · Phase · [gap for close btn] -->
              <div style="display:grid;grid-template-columns:2fr 1fr 40px;gap:14px;margin-bottom:14px">
                <div>
                  <div class="expand-label">Line of Business / Module</div>
                  <select class="cell-select" style="width:100%;border:1px solid var(--border);padding:6px 8px"
                    onchange="updateField('${r.kddId}','module',this.value);updateField('${r.kddId}','l1',this.value)">
                    ${(_lobs.length ? _lobs : ["Finance","Sales","Sourcing and Procurement","Manufacturing","Human Resources","Supply Chain","Service","Asset Management","IT Management","Other"])
                      .map(v => `<option ${modName(r)===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
                <div>
                  <div class="expand-label">SAP Activate Phase</div>
                  <select class="cell-select" style="width:100%;border:1px solid var(--border);padding:6px 8px"
                    onchange="updateField('${r.kddId}','sapActivatePhase',this.value)">
                    ${["Explore","Realize","Deploy"].map(v => `<option ${r.sapActivatePhase===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
                <div></div>
              </div>
              <!-- Row B: Design Question -->
              <div style="margin-bottom:14px">
                <div class="expand-label">Design Question <span style="color:var(--text-muted);font-weight:400;text-transform:none">(editable)</span></div>
                <textarea class="expand-textarea" rows="2"
                  onchange="updateField('${r.kddId}','designQuestion',this.value)"
                >${(r.designQuestion||"").replace(/</g,"&lt;")}</textarea>
              </div>
              <!-- Row C: Rationale | Impact -->
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">
                <div>
                  <div class="expand-label">Rationale <span style="color:var(--text-muted);font-weight:400;text-transform:none">(editable)</span></div>
                  <textarea class="expand-textarea" rows="2"
                    onchange="updateField('${r.kddId}','rationale',this.value)"
                  >${(r.rationale||"").replace(/</g,"&lt;")}</textarea>
                </div>
                <div>
                  <div class="expand-label">Impact if Not Decided <span style="color:var(--text-muted);font-weight:400;text-transform:none">(editable)</span></div>
                  <textarea class="expand-textarea" rows="2"
                    onchange="updateField('${r.kddId}','impactActions',this.value)"
                  >${(r.impactActions||"").replace(/</g,"&lt;")}</textarea>
                </div>
              </div>
              <!-- Row D: Notes -->
              <div>
                <div class="expand-label">Notes / Fiori App</div>
                <textarea class="expand-textarea" rows="2"
                  onchange="updateField('${r.kddId}','notes',this.value)"
                >${(r.notes||"").replace(/</g,"&lt;")}</textarea>
              </div>
            </div>

            <!-- ── Section 2: Decision ── -->
            <div style="padding:14px 20px;border-bottom:1px solid #e0e7ff;background:#fdf8ff">
              <div class="expand-label" style="color:var(--accent)">📝 Decision Made <span style="color:var(--warning);font-weight:400;text-transform:none">← fill this in during workshop</span></div>
              <textarea id="decision-ta-${r.kddId}" class="expand-textarea decision-field" rows="3"
                placeholder="Enter the agreed decision here…"
                onchange="updateField('${r.kddId}','decisionMade',this.value)"
              >${(r.decisionMade||"").replace(/</g,"&lt;")}</textarea>
              <div id="suggest-box-${r.kddId}" style="margin-top:8px">
                <button onclick="event.stopPropagation();suggestDecisions('${r.kddId}')"
                  style="background:#faf5ff;border:1px solid #ddd6fe;color:#7c3aed;padding:5px 12px;
                         border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">
                  💡 Suggest Decisions
                </button>
                <div id="suggest-cards-${r.kddId}" style="margin-top:8px"></div>
              </div>
            </div>

            <!-- ── Section 3: WRICEF ── -->
            <div style="padding:14px 20px;border-bottom:1px solid #e0e7ff">
              <div class="expand-label" style="color:#4f46e5;margin-bottom:10px">
                🔧 WRICEF Details
                ${r.fitGap === "Fit"
                  ? `<span style="color:var(--text-muted);font-weight:400;text-transform:none;font-size:11px">&nbsp;— disabled for Fit items</span>`
                  : `<span style="color:var(--text-muted);font-weight:400;text-transform:none;font-size:11px">&nbsp;— set Resolution &amp; Type in the table row above</span>`}
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr;gap:12px;${r.fitGap === "Fit" ? "opacity:0.4;pointer-events:none;" : ""}">
                <div>
                  <div class="expand-label">WRICEF ID</div>
                  <div class="expand-readonly" style="font-family:monospace;font-size:13px;color:var(--accent);font-weight:700">${r.ricefId || (r.gapResolution==="WRICEF"?"auto-assigned on type select":"—")}</div>
                </div>
                <div>
                  <div class="expand-label">Complexity</div>
                  <select class="expand-textarea" style="min-height:unset;padding:6px 8px;cursor:pointer"
                    onchange="updateField('${r.kddId}','complexity',this.value)">
                    ${["Low","Medium","High"].map(v => `<option ${(r.complexity||"Low")===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
                <div>
                  <div class="expand-label">Extensibility</div>
                  <select class="expand-textarea" style="min-height:unset;padding:6px 8px;cursor:pointer"
                    onchange="updateField('${r.kddId}','extensibilityType',this.value)">
                    ${["None","Key User In-App","Developer ABAP Cloud","Side-by-Side BTP"].map(v =>
                      `<option ${(r.extensibilityType||"None")===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
                <div>
                  <div class="expand-label">BTP Required</div>
                  <select class="expand-textarea" style="min-height:unset;padding:6px 8px;cursor:pointer"
                    onchange="updateField('${r.kddId}','btpRequired',this.value)">
                    ${["No","Yes","Possible"].map(v => `<option ${(r.btpRequired||"No")===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
                <div>
                  <div class="expand-label">Effort</div>
                  <select class="expand-textarea" style="min-height:unset;padding:6px 8px;cursor:pointer"
                    onchange="updateField('${r.kddId}','effortEstimate',this.value)">
                    ${["None","S","M","L","XL"].map(v => `<option ${(r.effortEstimate||"None")===v?"selected":""}>${v}</option>`).join("")}
                  </select>
                </div>
              </div>
            </div>

            <!-- ── Section 4: Workshop Outcome ── -->
            <div style="padding:14px 20px">
              <div class="expand-label" style="color:#6d28d9;margin-bottom:12px">📋 Workshop Outcome</div>
              <!-- Row: Date · Decided By · Sign-off -->
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px">
                <div>
                  <div class="expand-label">Workshop Date</div>
                  <input type="date" class="expand-textarea" style="min-height:unset;padding:6px 8px"
                    value="${r.workshopDate || ""}"
                    onchange="updateField('${r.kddId}','workshopDate',this.value)" />
                </div>
                <div>
                  <div class="expand-label">Decided By</div>
                  <input type="text" class="expand-textarea" style="min-height:unset;padding:6px 8px"
                    placeholder="Name / role who signed off…"
                    value="${(r.decidedBy || "").replace(/"/g,"&quot;")}"
                    onchange="updateField('${r.kddId}','decidedBy',this.value)" />
                </div>
                <div>
                  <div class="expand-label">Sign-off Status</div>
                  <select class="expand-textarea" style="min-height:unset;padding:6px 8px;cursor:pointer"
                    onchange="updateField('${r.kddId}','signOffStatus',this.value)">
                    ${["Open","Approved","Deferred","Rejected"].map(v =>
                      `<option value="${v}" ${(r.signOffStatus||"Open")===v?"selected":""}>${
                        v==="Approved"?"✅ Approved":v==="Deferred"?"⏸ Deferred":v==="Rejected"?"❌ Rejected":v
                      }</option>`
                    ).join("")}
                  </select>
                </div>
              </div>
              <!-- Row: Action Items · BPD Ref · SAP Consultant -->
              <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px">
                <div>
                  <div class="expand-label">Action Items <span style="font-weight:400;text-transform:none;color:var(--text-muted)">from workshop</span></div>
                  <textarea class="expand-textarea" rows="3"
                    placeholder="e.g. • IT to confirm Fiori app availability by DD/MM&#10;• Finance lead to review with CFO"
                    onchange="updateField('${r.kddId}','actionItems',this.value)"
                  >${(r.actionItems||"").replace(/</g,"&lt;")}</textarea>
                </div>
                <div>
                  <div class="expand-label">BPD Reference</div>
                  <input type="text" class="expand-textarea" style="min-height:unset;padding:6px 8px"
                    placeholder="e.g. FIN-BPD-001…"
                    value="${(r.bpdRef || "").replace(/"/g,"&quot;")}"
                    onchange="updateField('${r.kddId}','bpdRef',this.value)" />
                </div>
                <div>
                  <div class="expand-label">SAP Consultant</div>
                  <input type="text" class="expand-textarea" style="min-height:unset;padding:6px 8px"
                    placeholder="Accenture consultant name…"
                    value="${(r.sapConsultant || "").replace(/"/g,"&quot;")}"
                    onchange="updateField('${r.kddId}','sapConsultant',this.value)" />
                </div>
              </div>
            </div>
          </div>
        </td>
      </tr>`;
  }

  // ── View mode toggle ──
  window.toggleViewMode = () => {
    _viewMode = _viewMode === "table" ? "cards" : "table";
    const btn   = document.getElementById("view-toggle-btn");
    const tWrap = document.getElementById("kdd-table-wrap");
    const cWrap = document.getElementById("kdd-card-view");
    if (_viewMode === "cards") {
      if (btn)   btn.textContent = "☰ Table View";
      if (tWrap) tWrap.style.display = "none";
      if (cWrap) cWrap.style.display = "block";
      renderCards(currentFiltered());
    } else {
      if (btn)   btn.textContent = "⊞ Card View";
      if (tWrap) tWrap.style.display = "block";
      if (cWrap) cWrap.style.display = "none";
      renderTable(currentFiltered());
    }
  };

  function renderCards(rows) {
    const grid = document.getElementById("kdd-cards-grid");
    if (!grid) return;
    const fgColor = fg => fg === "Gap" ? "#dc2626" : fg === "Partial Fit" ? "#d97706" : "#059669";
    const fgBg    = fg => fg === "Gap" ? "#fee2e2" : fg === "Partial Fit" ? "#fef3c7" : "#dcfce7";
    const cxColor = c  => c  === "High" ? "#dc2626" : c === "Medium" ? "#d97706" : "#059669";
    grid.innerHTML = rows.map(r => {
      const hasDec  = r.decisionMade && r.decisionMade.trim();
      const rating  = _ratings[r.kddId];
      const ratingH = rating === "generic"
        ? `<span style="background:#fef3c7;color:#92400e;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600">⚠ Generic</span>`
        : rating === "specific"
          ? `<span style="background:#dcfce7;color:#166534;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600">✔ Specific</span>` : "";
      const wricefTypeLabel = {
        W:"W–Workflow", R:"R–Report", I:"I–Interface", C:"C–Conversion",
        E_RAP:"E–BTP RAP", E_CAPM:"E–BTP CAPM", E_RAP_CAPM:"E–RAP&CAPM", F:"F–Form"
      };
      const ricefH = r.gapResolution === "Config"
        ? `<span style="background:#dcfce7;color:#059669;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">Config</span>`
        : (r.ricefType && r.ricefType !== "None"
        ? `<span style="background:#ede9fe;color:#4f46e5;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">🔧 ${wricefTypeLabel[r.ricefType]||r.ricefType}${r.ricefId ? " · " + r.ricefId : ""}</span>` : "");
      const cardInScope = r.inScope !== false;
      return `<div class="card" style="padding:14px;cursor:pointer;border:1.5px solid var(--border);transition:box-shadow 0.15s,border-color 0.15s;${cardInScope?"":"opacity:0.42;background:#f9fafb;"}"
          onmouseenter="this.style.boxShadow='0 4px 16px rgba(0,0,0,0.1)';this.style.borderColor='var(--accent)'"
          onmouseleave="this.style.boxShadow='';this.style.borderColor='var(--border)'"
          onclick="switchToTableAndExpand('${r.kddId}')">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;gap:8px">
          <span style="font-family:monospace;font-size:11px;color:var(--accent);font-weight:700">${r.kddId}${!cardInScope ? ' <span style="font-size:9px;color:#9ca3af;font-weight:400">OUT OF SCOPE</span>' : ""}</span>
          <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">
            <span class="badge badge-purple" style="font-size:10px">${r.scopeItemId}</span>
            ${r.module ? `<span style="background:#f0f9ff;color:#0369a1;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600">${r.module}</span>` : ""}
          </div>
        </div>
        <div style="font-size:12.5px;color:var(--text);margin-bottom:10px;line-height:1.5">
          ${r.designQuestion ? (r.designQuestion.length > 120 ? r.designQuestion.slice(0,120)+"…" : r.designQuestion) : "—"}
        </div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
          <span style="background:${fgBg(r.fitGap)};color:${fgColor(r.fitGap)};padding:2px 10px;border-radius:10px;font-size:11px;font-weight:700">${r.fitGap}</span>
          <span style="background:#f3f4f6;color:${cxColor(r.complexity)};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">${r.complexity}</span>
          ${ricefH}${ratingH}
          ${hasDec ? `<span style="background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">✔ Decision</span>` : ""}
          ${r.signOffStatus === "Approved" ? `<span style="font-size:11px">✅</span>` : ""}
        </div>
      </div>`;
    }).join("") || `<div style="padding:40px;text-align:center;color:var(--text-muted)">No KDDs match the current filters.</div>`;
  }

  window.switchToTableAndExpand = (kddId) => {
    _viewMode = "table";
    const btn   = document.getElementById("view-toggle-btn");
    const tWrap = document.getElementById("kdd-table-wrap");
    const cWrap = document.getElementById("kdd-card-view");
    if (btn)   btn.textContent = "⊞ Card View";
    if (tWrap) tWrap.style.display = "block";
    if (cWrap) cWrap.style.display = "none";
    _expanded = kddId;
    renderTable(currentFiltered());
    setTimeout(() => {
      const row = document.getElementById(`row-${kddId}`);
      if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
  };

  // ── WRICEF counter — next available WRICEF-NNN ──
  function nextRicefId() {
    const nums = _rows
      .map(r => r.ricefId)
      .filter(Boolean)
      .map(id => parseInt((id.match(/(\d+)$/) || ["0","0"])[1], 10))
      .filter(n => !isNaN(n));
    return `WRICEF-${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(3, "0")}`;
  }

  // ── Expand / collapse ──
  window.toggleExpand = (kddId) => {
    _expanded = _expanded === kddId ? null : kddId;
    // Re-render only the affected rows for speed
    const filtered = currentFiltered();
    renderTable(filtered);
  };

  // ── Decision library — save an approved KDD to decisions.json ──
  // Called automatically when sign-off status is set to Approved with a decision text.
  // The server injects these into future Claude prompts for the same scope item.
  async function saveToLibrary(row) {
    try {
      const res  = await fetch("/api/decisions/save", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kddId:         row.kddId,
          scopeItemId:   row.scopeItemId,
          scopeItemName: row.scopeItemName,
          question:      row.designQuestion,
          decision:      row.decisionMade,
          rationale:     row.rationale,
          fitGap:        row.fitGap,
          complexity:    row.complexity,
          client:        (_meta || {}).client  || "",
          project:       (_meta || {}).project || ""
        })
      });
      const data = await res.json();
      if (data.ok) {
        showToast(`📚 Saved to decision library (${data.total} decisions)`);
      } else {
        showToast(`⚠ Library save failed: ${data.error || "unknown error"}`, "warn");
      }
    } catch (e) {
      showToast(`⚠ Library save error: ${e.message}`, "warn");
    }
  }

  // ── Delete a KDD row ─────────────────────────────────────────────────────────
  window.deleteKDDRow = (kddId) => {
    const idx = _rows.findIndex(r => r.kddId === kddId);
    if (idx === -1) return;
    const row = _rows[idx];
    const preview = (row.designQuestion || "").slice(0, 80);
    if (!confirm(`Delete KDD ${kddId}?\n"${preview}${preview.length === 80 ? "…" : ""}"\n\nYou can regenerate from the KDD Generator to restore it.`)) return;
    _rows.splice(idx, 1);
    if (_expanded === kddId) _expanded = null;
    renderTable(_rows);
    markDirty();
  };

  // ── Inline field update ──
  window.updateField = (kddId, field, value) => {
    const row = _rows.find(r => r.kddId === kddId);
    if (!row) return;
    row[field] = value;
    markDirty();
    // Auto-save to decision library when Approved with a decision text
    if (field === "signOffStatus" && value === "Approved" && row.decisionMade && row.decisionMade.trim()) {
      saveToLibrary(row);
    }
    // WRICEF ID auto-assign: set when type selected, clear when reset to None
    if (field === "ricefType") {
      if (value !== "None" && !row.ricefId) row.ricefId = nextRicefId();
      if (value === "None") row.ricefId = "";
    }
    // When Resolution changes: clear WRICEF subtype if not WRICEF
    if (field === "gapResolution") {
      if (value !== "WRICEF") { row.ricefType = "None"; row.ricefId = ""; }
    }
    // When fitGap flips to Fit, clear resolution + WRICEF type + ID
    if (field === "fitGap" && value === "Fit") {
      row.gapResolution = "None";
      row.ricefType     = "None";
      row.ricefId       = "";
    }
    // When inScope toggled, re-render row for gray/active style
    if (field === "inScope") {
      const tr = document.getElementById(`row-${kddId}`);
      if (tr) tr.outerHTML = rowHTML(row);
      return;
    }
    // When fitGap, gapResolution or ricefType changes, re-render whole row so
    // Resolution/WRICEF cell is guaranteed to reflect the new state.
    if (field === "fitGap" || field === "gapResolution" || field === "ricefType") {
      const tr = document.getElementById(`row-${kddId}`);
      if (tr) tr.outerHTML = rowHTML(row);
      return;
    }
    // For other fields, just update className on the existing tr
    const tr = document.getElementById(`row-${kddId}`);
    if (tr) {
      const hasDec = row.decisionMade && row.decisionMade.trim();
      tr.className = `${hasDec ? "row-has-decision" : ""} ${_expanded === kddId ? "row-expanded" : ""}`;
    }
  };

  // ── Filter ──
  function currentFiltered() {
    const scope   = (document.getElementById("filter-scope")   || {}).value || "";
    const status  = (document.getElementById("filter-status")  || {}).value || "";
    const fitgap  = (document.getElementById("filter-fitgap")  || {}).value || "";
    const signoff = (document.getElementById("filter-signoff") || {}).value || "";
    const q       = ((document.getElementById("filter-search") || {}).value || "").toLowerCase();
    return _rows.filter(r =>
      (!scope   || r.scopeItemId === scope) &&
      (!status  || r.status === status) &&
      (!fitgap  || r.fitGap === fitgap) &&
      (!signoff || (r.signOffStatus || "Open") === signoff) &&
      (!q || r.designQuestion.toLowerCase().includes(q)
          || r.kddId.toLowerCase().includes(q)
          || (r.bpdRef || "").toLowerCase().includes(q))
    );
  }

  window.applyFilters = () => {
    const filtered = currentFiltered();
    if (_viewMode === "cards") renderCards(filtered);
    else renderTable(filtered);
  };

  // ── Save ──
  window.saveEdits = async () => {
    const btn = document.getElementById("save-btn");
    const ind = document.getElementById("save-indicator");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
    if (ind) { ind.className = "save-indicator saving"; ind.textContent = "⟳ Saving…"; }
    try {
      const res = await fetch("/api/kdd/data", {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rows: _rows, meta: _meta })
      });
      const data = await res.json();
      if (data.ok) {
        _meta.lastEditedAt = data.savedAt;
        markSaved();
      } else {
        if (ind) { ind.className = "save-indicator dirty"; ind.textContent = "⚠ Save failed"; }
      }
    } catch (e) {
      if (ind) { ind.className = "save-indicator dirty"; ind.textContent = "⚠ Save failed"; }
    }
    if (btn) { btn.disabled = false; btn.textContent = "💾 Save Changes"; }
  };

  // ── Export Excel ──
  window.exportExcel = async () => {
    const btn = document.getElementById("export-btn");
    const el  = document.getElementById("export-result");
    btn.disabled = true;
    btn.textContent = "Building…";
    el.innerHTML = "";

    // Save first if dirty
    if (_dirty) await window.saveEdits();

    try {
      const res  = await fetch("/api/kdd/export", { method: "POST" });
      const data = await res.json();
      if (data.ok && data.filename) {
        el.innerHTML = `
          <div class="alert alert-success" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
            <span>✅ <strong>${data.filename}</strong> ready</span>
            <a href="/api/output/download/${encodeURIComponent(data.filename)}"
               class="btn btn-primary btn-sm" download>⬇ Download</a>
          </div>`;
      } else {
        el.innerHTML = `<div class="alert alert-danger">⚠️ ${data.error || "Export failed"}</div>`;
      }
    } catch (e) {
      el.innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
    }
    btn.disabled = false;
    btn.textContent = "⬇ Export Excel";
    setTimeout(() => { if (el) el.innerHTML = ""; }, 8000);
  };

  // ── Mark as Ready (save + export + confirmation banner) ──
  window.markReady = async () => {
    if (!confirm("Mark this KDD log as ready?\n\nThis will:\n• Save all changes\n• Export the final Excel\n• Flag the log as ready for Workshop Prep\n\nYou can still edit it afterwards.")) return;

    // Tag meta
    _meta.status      = "Ready for Workshop Prep";
    _meta.readyAt     = new Date().toISOString();

    await window.saveEdits();
    const res = await fetch("/api/kdd/export", { method: "POST" });
    const data = await res.json();

    const el = document.getElementById("export-result");
    if (data.ok) {
      el.innerHTML = `
        <div class="alert alert-success" style="margin-bottom:16px">
          <div class="bold" style="font-size:14px;margin-bottom:6px">🚀 KDD Log is Ready for Workshop Prep</div>
          <div style="font-size:13px;margin-bottom:10px">
            All changes saved · Excel exported ·
            <code style="background:rgba(0,0,0,.07);padding:1px 6px;border-radius:3px">kdd-data.json</code>
            flagged as ready — Workshop Prep skill will pick it up automatically when available.
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <a href="/api/output/download/${encodeURIComponent(data.filename)}"
               class="btn btn-primary btn-sm" download>⬇ Download Final Excel</a>
            <button class="btn btn-outline btn-sm" onclick="navigate('output')">↓ Output Files</button>
          </div>
        </div>`;
    } else {
      el.innerHTML = `<div class="alert alert-danger">⚠️ ${data.error || "Export failed"}</div>`;
    }
  };

  // ── Workshop Session modal — bulk sign-off ────────────────────────────────────
  window.openWorkshopModal = () => {
    _wsSelected = new Set();
    const openRows = _rows.filter(r => !r.signOffStatus || r.signOffStatus === "Open");

    const modal = document.createElement("div");
    modal.className = "modal-overlay";
    modal.id        = "workshop-modal";
    modal.innerHTML = `
      <div class="modal-box">
        <div class="modal-header">
          <div class="bold" style="font-size:15px">📋 Workshop Session — Bulk Sign-off</div>
          <button class="btn btn-sm btn-outline" onclick="closeWorkshopModal()">✕ Close</button>
        </div>
        <div class="modal-body">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
            <div class="form-group" style="margin-bottom:0">
              <label class="form-label">Session Date</label>
              <input type="date" id="ws-date" class="form-input"
                value="${new Date().toISOString().slice(0,10)}" />
            </div>
            <div class="form-group" style="margin-bottom:0">
              <label class="form-label">Facilitator</label>
              <input type="text" id="ws-facilitator" class="form-input" placeholder="Name or role…" />
            </div>
          </div>
          <div class="form-group" style="margin-bottom:16px">
            <label class="form-label">Attendees <span class="text-muted" style="font-weight:400;text-transform:none">(one per line)</span></label>
            <textarea id="ws-attendees" class="form-textarea" rows="2"
              placeholder="e.g.&#10;Jane Smith — Finance Lead&#10;Ali Hassan — IT Architect"></textarea>
          </div>

          <div class="section-divider" style="margin-top:0">
            Select KDDs to update
            <span class="text-muted" style="font-weight:400;text-transform:none">
              — ${openRows.length} open item${openRows.length !== 1 ? "s" : ""}
            </span>
          </div>
          <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
            <button class="btn btn-sm btn-outline" onclick="wsSelectAll()">Select All</button>
            <button class="btn btn-sm btn-outline" onclick="wsSelectNone()">Clear</button>
            <span id="ws-count" class="text-sm text-muted" style="margin-left:auto">0 selected</span>
          </div>
          <div style="max-height:260px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius)">
            ${openRows.length ? openRows.map(r => `
              <div class="item-row" onclick="wsToggle('${r.kddId}',this)">
                <input type="checkbox" id="ws-cb-${r.kddId}"
                  onclick="event.stopPropagation();wsToggle('${r.kddId}',this.closest('.item-row'))" />
                <span class="item-id">${r.kddId}</span>
                <span class="item-name" style="flex:1;font-size:12.5px">
                  ${(r.designQuestion||"").slice(0,72)}${r.designQuestion && r.designQuestion.length > 72 ? "…" : ""}
                </span>
                <span class="item-lob"><span class="badge badge-purple">${r.scopeItemId}</span></span>
              </div>`).join("")
            : `<div style="padding:24px;text-align:center;color:var(--text-muted)">
                ✅ All KDDs already have a sign-off status.
               </div>`}
          </div>
        </div>
        <div class="modal-footer">
          <span class="text-sm text-muted">Apply to selected:</span>
          <button class="btn btn-sm" style="background:var(--success);color:#fff"
            onclick="wsBulkApply('Approved')">✅ Approve</button>
          <button class="btn btn-sm" style="background:var(--warning);color:#fff"
            onclick="wsBulkApply('Deferred')">⏸ Defer</button>
          <button class="btn btn-sm" style="background:var(--danger);color:#fff"
            onclick="wsBulkApply('Rejected')">❌ Reject</button>
          <button class="btn btn-sm btn-outline" onclick="closeWorkshopModal()"
            style="margin-left:auto">Cancel</button>
        </div>
      </div>`;

    document.body.appendChild(modal);
    modal.addEventListener("click", e => { if (e.target === modal) closeWorkshopModal(); });
  };

  window.closeWorkshopModal = () => {
    const m = document.getElementById("workshop-modal"); if (m) m.remove();
  };

  window.wsToggle = (kddId, rowEl) => {
    const cb = document.getElementById(`ws-cb-${kddId}`);
    if (_wsSelected.has(kddId)) {
      _wsSelected.delete(kddId);
      if (cb) cb.checked = false;
      if (rowEl) rowEl.classList.remove("checked");
    } else {
      _wsSelected.add(kddId);
      if (cb) cb.checked = true;
      if (rowEl) rowEl.classList.add("checked");
    }
    const cnt = document.getElementById("ws-count");
    if (cnt) cnt.textContent = `${_wsSelected.size} selected`;
  };

  window.wsSelectAll = () => {
    _rows.filter(r => !r.signOffStatus || r.signOffStatus === "Open").forEach(r => {
      _wsSelected.add(r.kddId);
      const cb = document.getElementById(`ws-cb-${r.kddId}`);
      if (cb) { cb.checked = true; cb.closest(".item-row").classList.add("checked"); }
    });
    const cnt = document.getElementById("ws-count");
    if (cnt) cnt.textContent = `${_wsSelected.size} selected`;
  };

  window.wsSelectNone = () => {
    _wsSelected = new Set();
    document.querySelectorAll("[id^='ws-cb-']").forEach(cb => {
      cb.checked = false; cb.closest(".item-row").classList.remove("checked");
    });
    const cnt = document.getElementById("ws-count");
    if (cnt) cnt.textContent = "0 selected";
  };

  window.wsBulkApply = (signOffStatus) => {
    if (!_wsSelected.size) { alert("Select at least one KDD first."); return; }
    const date      = (document.getElementById("ws-date")        || {}).value || "";
    const by        = (document.getElementById("ws-facilitator") || {}).value || "";
    const attendees = (document.getElementById("ws-attendees")   || {}).value || "";

    _wsSelected.forEach(kddId => {
      const row = _rows.find(r => r.kddId === kddId); if (!row) return;
      row.signOffStatus = signOffStatus;
      if (date) row.workshopDate = date;
      if (by)   row.decidedBy   = by;
      if (attendees && !row.actionItems) row.actionItems = `Attendees: ${attendees}`;
      // Save approved decisions with text to the learning library
      if (signOffStatus === "Approved" && row.decisionMade && row.decisionMade.trim()) {
        saveToLibrary(row);
      }
    });

    const count = _wsSelected.size;
    markDirty();
    closeWorkshopModal();
    renderTable(currentFiltered());
    window.saveEdits();
    showToast(`${count} KDD${count !== 1 ? "s" : ""} marked as ${signOffStatus}`);
    _wsSelected = new Set();
  };

  // ── Quality Critique (Claude scores each question) ──
  window.runCritique = async () => {
    const btn = document.getElementById("critique-btn");
    const el  = document.getElementById("critique-panel");
    btn.disabled = true;
    btn.textContent = "⚡ Checking…";

    el.innerHTML = `
      <div class="card mb-3" style="padding:14px 18px">
        <div class="bold mb-2" style="font-size:13.5px">⚡ Quality Check — Claude is reviewing each question for specificity</div>
        <div class="critique-log" id="critique-log"></div>
      </div>`;

    const log = (text, cls = "") => {
      const logEl = document.getElementById("critique-log");
      if (logEl) logEl.innerHTML += `<div class="line ${cls}">${text}</div>`;
    };

    try {
      const response = await fetch("/api/kdd/critique", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rows: _rows })
      });

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === "progress") {
            log(`⟳ ${evt.message}`);
          } else if (evt.type === "item_done") {
            const cls = evt.generic > 0 ? "warn" : "ok";
            log(`${evt.item} — ${evt.total - evt.generic} specific · ${evt.generic} generic`, cls);
          } else if (evt.type === "item_error") {
            log(`⚠ ${evt.item}: ${evt.message}`, "warn");
          } else if (evt.type === "complete" && evt.results) {
            // Apply ratings to rows and re-render
            evt.results.forEach(s => {
              _ratings[s.kddId] = s.rating;
              _ratings[s.kddId + "_reason"] = s.reason || "";
            });
            const genericCount = evt.results.filter(s => s.rating === "generic").length;
            log(`✔ Done — ${evt.results.length - genericCount} specific · ${genericCount} need improvement`, genericCount > 0 ? "warn" : "ok");
            renderTable(currentFiltered());
            if (genericCount > 0) {
              el.innerHTML += `
                <div class="alert alert-warning mb-3" style="font-size:13px">
                  ⚠ <strong>${genericCount} question${genericCount !== 1 ? "s" : ""}</strong> flagged as generic —
                  marked with ⚠ in the table. Click the row to expand and improve the question.
                </div>`;
            } else {
              el.innerHTML += `<div class="alert alert-success mb-3" style="font-size:13px">✅ All questions are process-specific — no generic fillers found.</div>`;
            }
          }
        }
      }
    } catch (e) {
      el.innerHTML = `<div class="alert alert-danger">⚠️ ${e.message}</div>`;
    }

    btn.disabled = false;
    btn.textContent = "⚡ Quality Check";
  };

  // ── 💡 Decision Suggestions (editor row) ─────────────────────────────────────
  window.suggestDecisions = async (kddId) => {
    const r = _rows.find(x => x.kddId === kddId);
    if (!r) return;

    const btn     = document.querySelector(`#suggest-box-${kddId} button`);
    const cardsEl = document.getElementById(`suggest-cards-${kddId}`);
    if (!btn || !cardsEl) return;

    btn.disabled = true;
    cardsEl.innerHTML = "";

    // Live elapsed-time counter so users know it's working, not frozen
    const start   = Date.now();
    let timerInterval = setInterval(() => {
      const secs = Math.round((Date.now() - start) / 1000);
      btn.textContent = `⏳ Asking Claude… ${secs}s`;
    }, 1000);
    btn.textContent = "⏳ Asking Claude…";

    try {
      const res = await api.post("/api/kdd/suggest", {
        designQuestion: r.designQuestion, scopeItemId: r.scopeItemId,
        scopeItemName:  r.scopeItemName,  fitGap:       r.fitGap,
        rationale:      r.rationale,      notes:        r.notes,
        l1:             r.l1,             l2:           r.l2
      });
      clearInterval(timerInterval);
      if (!res.ok) throw new Error(res.error || "API error");

      window[`_sugg_${kddId}`] = res.suggestions;
      const tagColor  = { "SAP Standard": "#059669", "Common Variation": "#d97706", "Custom Path": "#7c3aed" };
      const fromLib   = res.source === "library";
      const sourceTag = fromLib
        ? `<span style="font-size:10px;color:#059669;background:#d1fae5;padding:1px 6px;
                        border-radius:8px;margin-left:4px">⚡ From library</span>`
        : `<span style="font-size:10px;color:#6b7280;margin-left:4px">(Claude)</span>`;

      cardsEl.innerHTML = `<div style="font-size:11px;color:#6b7280;margin-bottom:6px">
        ${fromLib ? "📚 From decision library — instant" : "🤖 Generated by Claude"}${sourceTag}
      </div>` + res.suggestions.map((s, i) => `
        <div onclick="event.stopPropagation();pickSuggestion('${kddId}',${i})"
          style="background:#faf5ff;border:1.5px solid #ddd6fe;border-radius:8px;padding:10px 14px;
                 margin-bottom:6px;cursor:pointer" class="suggest-hover">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
            <span style="font-size:10.5px;font-weight:700;color:${tagColor[s.tag]||"#7c3aed"};
                         background:#ede9fe;padding:2px 8px;border-radius:12px">${s.tag}</span>
          </div>
          <div style="font-size:12.5px;color:#111827;line-height:1.5;margin-bottom:3px">${s.text}</div>
          <div style="font-size:11px;color:#6b7280">${s.rationale}</div>
        </div>`).join("");

      btn.disabled = false;
      btn.textContent = fromLib ? "💡 Suggest Again (instant)" : "💡 Suggest Again";
    } catch (e) {
      clearInterval(timerInterval);
      cardsEl.innerHTML = `<div style="color:#dc2626;font-size:12px;padding:4px">⚠ ${e.message}</div>`;
      btn.disabled = false;
      btn.textContent = "💡 Suggest Decisions";
    }
  };

  window.pickSuggestion = (kddId, idx) => {
    const s = window[`_sugg_${kddId}`]?.[idx];
    if (!s) return;
    const ta = document.getElementById(`decision-ta-${kddId}`);
    if (ta) { ta.value = s.text; updateField(kddId, "decisionMade", s.text); }
  };

  // ── 📊 PPT Export ────────────────────────────────────────────────────────────
  window.exportPPT = async () => {
    const btn = document.getElementById("ppt-btn");
    btn.disabled = true;
    btn.textContent = "⏳ Building PPT…";
    try {
      const res = await api.post("/api/kdd/ppt", {});
      if (res.ok && res.filename) {
        showToast("📊 PPT ready: " + res.filename);
        btn.textContent = "⬇ Download PPT";
        btn.disabled = false;
        btn.onclick = () => { window.location.href = `/api/output/download/${encodeURIComponent(res.filename)}`; };
      } else {
        throw new Error(res.error || "PPT build failed");
      }
    } catch (e) {
      showToast("⚠ PPT failed: " + e.message);
      btn.disabled = false;
      btn.textContent = "📊 PPT";
      btn.onclick = () => exportPPT();
    }
  };

  // ── 🎭 Workshop Presenter ─────────────────────────────────────────────────────
  let _pRows = [], _pIdx = 0, _pShowAll = false;

  function _presenterRAG(r) {
    if (r.fitGap === "Gap" && (r.complexity === "High" || r.complexity === "Medium")) return "Red";
    if (r.fitGap === "Gap" || (r.fitGap === "Partial Fit" && (r.complexity === "High" || r.complexity === "Medium"))) return "Amber";
    return "Green";
  }

  function _filterPresenterRows() {
    return _pShowAll
      ? [..._rows]
      : _rows.filter(r => !r.decisionMade || !r.decisionMade.trim());
  }

  function _renderPresenter() {
    if (!_pRows.length) {
      document.getElementById("p-question").textContent = "All decisions captured! 🎉";
      ["p-kddid","p-scope","p-fitgap","p-complexity","p-rag","p-rationale","p-impact","p-notes"]
        .forEach(id => { const el = document.getElementById(id); if (el) el.textContent = ""; });
      return;
    }
    const r   = _pRows[_pIdx];
    const rag = _presenterRAG(r);
    const ragColor  = rag === "Red" ? "#dc2626" : rag === "Amber" ? "#f59e0b" : "#10b981";
    const fitColor  = r.fitGap === "Gap" ? "#dc2626" : r.fitGap === "Partial Fit" ? "#f59e0b" : "#10b981";

    document.getElementById("p-progress").textContent   = `${_pIdx + 1} / ${_pRows.length}`;
    document.getElementById("p-kddid").textContent      = r.kddId;
    document.getElementById("p-scope").textContent      = `${r.scopeItemId} — ${r.scopeItemName}`;
    document.getElementById("p-fitgap").textContent     = r.fitGap;
    document.getElementById("p-fitgap").style.color     = fitColor;
    document.getElementById("p-complexity").textContent = r.complexity;
    document.getElementById("p-rag").textContent        = rag;
    document.getElementById("p-rag").style.color        = ragColor;
    document.getElementById("p-rationale").textContent  = r.rationale || "—";
    document.getElementById("p-impact").textContent     = r.impactActions || "—";
    document.getElementById("p-notes").textContent      = r.notes || "—";
    document.getElementById("p-question").textContent   = r.designQuestion || "";
    document.getElementById("p-decision").value         = r.decisionMade || "";
    document.getElementById("p-suggest-cards").innerHTML = "";
    document.getElementById("p-suggest-btn").textContent = "💡 Suggest Decisions";
    document.getElementById("p-suggest-btn").disabled   = false;
    document.getElementById("p-prev").disabled          = _pIdx === 0;
    document.getElementById("p-next").disabled          = _pIdx === _pRows.length - 1;
  }

  function _presenterKeyHandler(e) {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") presenterNav(1);
    if (e.key === "ArrowLeft"  || e.key === "ArrowUp")   presenterNav(-1);
    if (e.key === "Escape") closePresenter();
  }

  window.openPresenter = () => {
    _pRows = _filterPresenterRows();
    _pIdx  = 0;
    _renderPresenter();
    document.getElementById("presenter-overlay").style.display = "flex";
    document.addEventListener("keydown", _presenterKeyHandler);
  };

  window.closePresenter = () => {
    document.getElementById("presenter-overlay").style.display = "none";
    document.removeEventListener("keydown", _presenterKeyHandler);
  };

  window.presenterNav = (dir) => {
    _pIdx = Math.max(0, Math.min(_pRows.length - 1, _pIdx + dir));
    _renderPresenter();
  };

  window.togglePresenterFilter = () => {
    _pShowAll = !_pShowAll;
    _pRows = _filterPresenterRows();
    _pIdx  = 0;
    _renderPresenter();
    const btn = document.getElementById("p-filter-btn");
    if (btn) btn.textContent = _pShowAll ? "Show: All KDDs" : "Show: Open Only";
  };

  window.savePresenterDecision = () => {
    const r   = _pRows[_pIdx];
    if (!r) return;
    const val = document.getElementById("p-decision").value;
    updateField(r.kddId, "decisionMade", val);
    _pRows[_pIdx] = { ..._pRows[_pIdx], decisionMade: val };
    const saveBtn = document.getElementById("p-save-btn");
    saveBtn.textContent = "✅ Saved!";
    saveBtn.style.background = "#059669";
    setTimeout(() => { saveBtn.textContent = "💾 Save Decision"; saveBtn.style.background = "#7c3aed"; }, 1800);
  };

  window.presenterSuggest = async () => {
    const r   = _pRows[_pIdx];
    if (!r) return;
    const btn     = document.getElementById("p-suggest-btn");
    const cardsEl = document.getElementById("p-suggest-cards");

    btn.disabled = true;
    cardsEl.innerHTML = "";

    // Live timer — shows elapsed seconds so users know it's working
    const start = Date.now();
    let timerInterval = setInterval(() => {
      const secs = Math.round((Date.now() - start) / 1000);
      btn.textContent = `⏳ Asking Claude… ${secs}s`;
    }, 1000);
    btn.textContent = "⏳ Asking Claude…";

    try {
      const res = await api.post("/api/kdd/suggest", {
        designQuestion: r.designQuestion, scopeItemId: r.scopeItemId,
        scopeItemName:  r.scopeItemName,  fitGap:       r.fitGap,
        rationale:      r.rationale,      notes:        r.notes,
        l1:             r.l1,             l2:           r.l2
      });
      clearInterval(timerInterval);
      if (!res.ok) throw new Error(res.error || "API error");

      window._pSugg = res.suggestions;
      const fromLib  = res.source === "library";
      const tagColor = { "SAP Standard": "#10b981", "Common Variation": "#f59e0b", "Custom Path": "#a78bfa" };

      cardsEl.innerHTML =
        `<div style="font-size:10.5px;color:#9d7fe5;margin-bottom:8px">
          ${fromLib ? "⚡ From decision library — instant" : "🤖 Generated by Claude"}
        </div>` +
        res.suggestions.map((s, i) => `
          <div class="p-suggest-card" onclick="presenterPickSuggestion(${i})">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
              <span style="font-size:10.5px;font-weight:700;color:${tagColor[s.tag]||"#a78bfa"};
                           background:rgba(167,139,250,.15);padding:2px 8px;border-radius:12px">${s.tag}</span>
            </div>
            <div style="font-size:13px;color:#e2d9f3;line-height:1.5;margin-bottom:4px">${s.text}</div>
            <div style="font-size:11px;color:#9d7fe5">${s.rationale}</div>
          </div>`).join("");

      btn.textContent = fromLib ? "💡 Suggest Again (instant)" : "💡 Suggest Again";
      btn.disabled = false;
    } catch (e) {
      clearInterval(timerInterval);
      cardsEl.innerHTML = `<div style="color:#f87171;font-size:12px">⚠ ${e.message}</div>`;
      btn.textContent = "💡 Suggest Decisions";
      btn.disabled = false;
    }
  };

  window.presenterPickSuggestion = (idx) => {
    const s = window._pSugg?.[idx];
    if (!s) return;
    document.getElementById("p-decision").value = s.text;
  };
}

// ── DECISION LIBRARY page ─────────────────────────────────────────────────────
// Searchable view of decisions.json — all approved KDD decisions across projects.
// Stats strip → filter bar → decision table with expand-to-view detail.

async function pagDecisionLibrary(activeSlug) {
  // Pick up tab state set by dlSwitchClient (survives navigate() re-render)
  if (activeSlug === undefined) activeSlug = window._dlActiveSlug || "";
  const [clients, lib, data] = await Promise.all([
    api.get("/api/decisions/clients").catch(() => ({ clients: [] })),
    api.get(`/api/decisions/summary${activeSlug ? `?clientSlug=${encodeURIComponent(activeSlug)}` : ""}`).catch(() => ({ total: 0, scopeItems: 0, projects: 0, byFitGap: { Fit: 0, "Partial Fit": 0, Gap: 0 }, scopeBreakdown: [] })),
    api.get(`/api/decisions${activeSlug ? `?clientSlug=${encodeURIComponent(activeSlug)}` : ""}`).catch(() => ({ decisions: [], total: 0 }))
  ]);
  const clientList  = (clients && clients.clients) || [];
  // Normalise — old server may return array directly instead of {decisions:[]}
  const decisions   = (data && Array.isArray(data.decisions)) ? data.decisions
                    : Array.isArray(data) ? data : [];
  // Normalise lib — guard against missing fields
  if (!lib.byFitGap)       lib.byFitGap = { Fit: 0, "Partial Fit": 0, Gap: 0 };
  if (!lib.scopeBreakdown) lib.scopeBreakdown = [];

  const fitPct   = lib.total ? Math.round(((lib.byFitGap["Fit"] || 0) / lib.total) * 100) : 0;
  const gapPct   = lib.total ? Math.round(((lib.byFitGap["Gap"] || 0) / lib.total) * 100) : 0;
  const partPct  = lib.total ? Math.round(((lib.byFitGap["Partial Fit"] || 0) / lib.total) * 100) : 0;

  const empty = lib.total === 0;

  const emptyState = `
    <div class="card" style="text-align:center;padding:48px 24px;margin-top:16px">
      <div style="font-size:48px;margin-bottom:16px">📚</div>
      <div style="font-size:18px;font-weight:600;margin-bottom:8px">Decision Library is empty</div>
      <div style="color:var(--text-muted);font-size:14px;max-width:480px;margin:0 auto 20px;line-height:1.7">
        When you mark a KDD decision as <strong>Approved</strong> in the KDD Editor,
        it's automatically saved here for future projects.<br><br>
        <strong>Already approved decisions?</strong> Click Sync to import them now.
      </div>
      <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button class="btn btn-primary" id="dl-sync-empty-btn" onclick="dlSync()">🔄 Sync Approved Decisions</button>
        <button class="btn btn-outline" onclick="navigate('editor')">✎ Open KDD Editor</button>
      </div>
      <div id="dl-sync-msg" style="margin-top:14px;font-size:13px;color:var(--text-muted)"></div>
    </div>`;

  // ── Client tabs ───────────────────────────────────────────────────────────────
  const tabsHTML = clientList.length > 1 ? `
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;align-items:center">
      <span style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-right:4px">Client:</span>
      <button onclick="dlSwitchClient('')"
        style="padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;border:1.5px solid ${!activeSlug?"var(--accent)":"var(--border)"};background:${!activeSlug?"var(--accent)":"transparent"};color:${!activeSlug?"#fff":"var(--text)"}">
        All (${clientList.reduce((s,c)=>s+c.count,0)})
      </button>
      ${clientList.map(c => `
        <button onclick="dlSwitchClient('${c.slug}')"
          style="padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;border:1.5px solid ${activeSlug===c.slug?"var(--accent)":"var(--border)"};background:${activeSlug===c.slug?"var(--accent)":"transparent"};color:${activeSlug===c.slug?"#fff":"var(--text)"}">
          ${c.label} (${c.count})
        </button>`).join("")}
    </div>` : "";

  return `
    <div class="page-header" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
      <div>
        <div class="page-title">📚 Decision Library</div>
        <div class="page-sub">Approved KDD decisions · ${activeSlug ? clientList.find(c=>c.slug===activeSlug)?.label || activeSlug : "All clients"} · client names not stored</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-outline btn-sm" id="dl-sync-btn" onclick="dlSync()">🔄 Sync</button>
        ${lib.total > 0 ? `<button class="btn btn-outline btn-sm" onclick="dlExport()">⬇ Export</button>` : ""}
        ${lib.total > 0 ? `<button class="btn btn-sm" style="background:#fff1f0;color:#c0392b;border:1px solid #f5c6cb" onclick="dlClear()">🗑 Clear</button>` : ""}
      </div>
    </div>

    ${tabsHTML}

    ${empty ? emptyState : `
    <!-- Stats strip -->
    <div class="stats-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:20px">
      <div class="card">
        <div class="card-title">Total Decisions</div>
        <div class="card-value">${lib.total.toLocaleString()}</div>
        <div class="card-note">${activeSlug ? "this client" : "across all clients"}</div>
      </div>
      <div class="card">
        <div class="card-title">Scope Items</div>
        <div class="card-value">${lib.scopeItems}</div>
        <div class="card-note">unique SAP processes</div>
      </div>
      <div class="card">
        <div class="card-title">Projects</div>
        <div class="card-value">${lib.projects}</div>
        <div class="card-note">engagements</div>
      </div>
      <div class="card">
        <div class="card-title">Fit / Part / Gap</div>
        <div class="card-value" style="font-size:20px">
          <span style="color:#10b981">${fitPct}%</span>
          <span style="color:#f59e0b;margin:0 6px">${partPct}%</span>
          <span style="color:#ef4444">${gapPct}%</span>
        </div>
        <div class="card-note">Fit · Partial · Gap</div>
      </div>
    </div>

    <!-- Filter bar -->
    <div style="display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap;align-items:center">
      <input id="dl-search" class="form-input" style="flex:1;min-width:200px"
        placeholder="Search questions or decisions…" oninput="dlFilter()" />
      <select id="dl-fitgap" class="form-input" style="width:150px" onchange="dlFilter()">
        <option value="">All Fit/Gap</option>
        <option value="Fit">✅ Fit</option>
        <option value="Partial Fit">⚠️ Partial Fit</option>
        <option value="Gap">🔴 Gap</option>
      </select>
      <select id="dl-scope" class="form-input" style="width:160px" onchange="dlFilter()">
        <option value="">All Scope Items</option>
        ${(lib.scopeBreakdown || []).map(s => `<option value="${s.id}">${s.id} (${s.count})</option>`).join("")}
      </select>
      <span id="dl-count" style="font-size:12px;color:var(--text-muted);white-space:nowrap">
        ${decisions.length} decision${decisions.length !== 1 ? "s" : ""}
      </span>
    </div>

    <!-- Decisions table -->
    <div class="card" style="padding:0;overflow:hidden">
      <div class="table-wrap">
        <table id="dl-table">
          <thead>
            <tr>
              <th style="width:70px">Scope</th>
              <th>Design Question</th>
              <th>Decision Made</th>
              <th style="width:100px">Fit/Gap</th>
              <th style="width:80px">Complexity</th>
              <th style="width:110px">Project</th>
              <th style="width:32px"></th>
            </tr>
          </thead>
          <tbody id="dl-tbody">
            ${renderDLRows(decisions)}
          </tbody>
        </table>
      </div>
    </div>

    <div id="dl-no-results" class="alert alert-info" style="display:none;margin-top:12px">
      No decisions match the current filter.
    </div>
    <div id="dl-sync-msg" style="margin-top:14px;font-size:13px;color:var(--text-muted);text-align:center"></div>
    `}`;
}

function renderDLRows(decisions) {
  if (!decisions || !decisions.length) return `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px">No decisions yet — sync approved decisions from the KDD Editor.</td></tr>`;

  const fitColors = { "Fit": "#10b981", "Partial Fit": "#f59e0b", "Gap": "#ef4444" };
  const compColors = { "Low": "#10b981", "Medium": "#f59e0b", "High": "#ef4444" };

  return decisions.map((d, i) => {
    const rowId = `dl-row-${i}`;
    const detailId = `dl-detail-${i}`;
    const qShort = (d.question || "").length > 80 ? d.question.slice(0, 80) + "…" : (d.question || "—");
    const decShort = (d.decision || "").length > 80 ? d.decision.slice(0, 80) + "…" : (d.decision || "—");
    const fitColor = fitColors[d.fitGap] || "#9ca3af";
    const compColor = compColors[d.complexity] || "#9ca3af";
    const savedDate = d.savedAt ? new Date(d.savedAt).toLocaleDateString("en-GB", { day:"2-digit", month:"short", year:"2-digit" }) : "";

    return `
      <tr id="${rowId}" style="cursor:pointer" onclick="dlToggle(${i})">
        <td><span class="bold" style="color:var(--accent)">${d.scopeItemId || "—"}</span></td>
        <td style="font-size:12.5px">${qShort}</td>
        <td style="font-size:12.5px">${decShort}</td>
        <td><span style="font-size:11px;font-weight:700;color:${fitColor};background:${fitColor}22;
                         padding:2px 8px;border-radius:10px">${d.fitGap || "—"}</span></td>
        <td><span style="font-size:11px;color:${compColor}">${d.complexity || "—"}</span></td>
        <td style="font-size:11px;color:var(--text-muted)">${d.project || "—"}</td>
        <td style="text-align:center;font-size:11px;color:var(--text-muted)">▼</td>
      </tr>
      <tr id="${detailId}" style="display:none;background:var(--surface-raised)">
        <td colspan="7" style="padding:16px 20px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12.5px">
            <div>
              <div style="font-weight:600;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Design Question</div>
              <div style="line-height:1.6">${d.question || "—"}</div>
            </div>
            <div>
              <div style="font-weight:600;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Decision Made</div>
              <div style="line-height:1.6">${d.decision || "—"}</div>
            </div>
            ${d.rationale ? `
            <div style="grid-column:1/-1">
              <div style="font-weight:600;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Rationale</div>
              <div style="line-height:1.6;color:var(--text-muted)">${d.rationale}</div>
            </div>` : ""}
          </div>
          <div style="display:flex;gap:16px;margin-top:12px;font-size:11px;color:var(--text-muted)">
            ${d.kddId         ? `<span>🔑 ${d.kddId}</span>` : ""}
            ${d.scopeItemName ? `<span>📋 ${d.scopeItemName}</span>` : ""}
            ${d.project       ? `<span>📁 ${d.project}</span>` : ""}
            ${savedDate       ? `<span>🗓 ${savedDate}</span>` : ""}
          </div>
        </td>
      </tr>`;
  }).join("");
}

function bindDecisionLibrary() {
  // Track which client tab is active — persisted across filter calls
  let _activeSlug = window._dlActiveSlug || "";

  window.dlToggle = (i) => {
    const detail = document.getElementById(`dl-detail-${i}`);
    const row    = document.getElementById(`dl-row-${i}`);
    if (!detail) return;
    const open = detail.style.display !== "none";
    detail.style.display = open ? "none" : "table-row";
    const arrow = row ? row.querySelector("td:last-child") : null;
    if (arrow) arrow.textContent = open ? "▼" : "▲";
  };

  // Switch client tab — re-renders the whole page with that client's data
  window.dlSwitchClient = (slug) => {
    window._dlActiveSlug = slug;
    navigate("decision-library");
  };

  window.dlFilter = async () => {
    const q     = (document.getElementById("dl-search")?.value || "").toLowerCase();
    const fg    = document.getElementById("dl-fitgap")?.value  || "";
    const scope = document.getElementById("dl-scope")?.value   || "";

    const params = new URLSearchParams();
    if (q)               params.set("q",           q);
    if (scope)           params.set("scopeItemId",  scope);
    if (_activeSlug)     params.set("clientSlug",   _activeSlug);
    const url = `/api/decisions?${params.toString()}`;

    try {
      const data = await api.get(url);
      let results = data.decisions || [];
      if (fg) results = results.filter(d => d.fitGap === fg);

      const tbody     = document.getElementById("dl-tbody");
      const noResults = document.getElementById("dl-no-results");
      const countEl   = document.getElementById("dl-count");

      if (tbody)     tbody.innerHTML = renderDLRows(results);
      if (noResults) noResults.style.display = results.length ? "none" : "block";
      if (countEl)   countEl.textContent = `${results.length} decision${results.length !== 1 ? "s" : ""}`;
    } catch { /* non-fatal */ }
  };

  window.dlSync = async () => {
    const btn      = document.getElementById("dl-sync-btn");
    const emptyBtn = document.getElementById("dl-sync-empty-btn");
    const msg = document.getElementById("dl-sync-msg");
    if (btn)      { btn.disabled = true;      btn.textContent = "⏳ Syncing…"; }
    if (emptyBtn) { emptyBtn.disabled = true; emptyBtn.textContent = "⏳ Syncing…"; }
    try {
      const data = await api.post("/api/decisions/sync", {});
      if (data.ok) {
        const text = data.message || `Synced ${data.synced} decisions.`;
        if (msg) { msg.style.color = "var(--success)"; msg.textContent = `✅ ${text}`; }
        // Switch to the client tab that was just synced and reload
        if (data.synced > 0) {
          window._dlActiveSlug = data.clientSlug || "";
          setTimeout(() => navigate("decision-library"), 1400);
        }
      } else {
        if (msg) { msg.style.color = "var(--warning)"; msg.textContent = `⚠ ${data.error || data.message}`; }
      }
    } catch (e) {
      if (msg) { msg.style.color = "var(--danger)"; msg.textContent = `⚠ ${e.message}`; }
    } finally {
      if (btn)      { btn.disabled = false;      btn.textContent = "🔄 Sync"; }
      if (emptyBtn) { emptyBtn.disabled = false; emptyBtn.textContent = "🔄 Sync Approved Decisions"; }
    }
  };

  window.dlExport = () => {
    const params = _activeSlug ? `?clientSlug=${encodeURIComponent(_activeSlug)}` : "";
    const label  = _activeSlug || "all-clients";
    api.get(`/api/decisions${params}`).then(data => {
      const blob = new Blob([JSON.stringify(data.decisions, null, 2)], { type: "application/json" });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = `fulcrum-decisions-${label}-${new Date().toISOString().slice(0,10)}.json`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    }).catch(() => {});
  };

  window.dlClear = async () => {
    const scope = _activeSlug
      ? `all decisions for this client`
      : `ALL decisions across every client`;
    if (!confirm(`⚠ This will permanently delete ${scope}.\n\nThis cannot be undone. Continue?`)) return;
    const params = _activeSlug ? `?clientSlug=${encodeURIComponent(_activeSlug)}` : "";
    try {
      const data = await fetch(`/api/decisions${params}`, { method: "DELETE" }).then(r => r.json());
      if (data.ok) {
        window._dlActiveSlug = "";
        navigate("decision-library");
      } else {
        alert(`Error: ${data.error || "Could not clear decisions"}`);
      }
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  };
}

// ── MCP SERVER page ───────────────────────────────────────────────────────────
// Shows config status, registered tools, live data snapshot, and a "Try" panel.
// The actual mcp-server.js process is started by Claude CLI via .claude/settings.json.
// This page is purely for visibility — no writes happen here.

async function pagMCPServer() {
  const cfg = await api.get("/api/mcp/config").catch(() => null);
  if (!cfg) return `<div class="alert alert-danger">Could not load MCP config</div>`;

  const ok = cfg.settingsFound && cfg.mcpServerFound;
  const statusColor = ok ? "#10b981" : "#f59e0b";
  const statusLabel = ok ? "✅ Registered" : "⚠️ Check setup";

  const toolCards = cfg.tools.map(t => `
    <div class="card" id="mcp-tool-${t.name}"
         style="border:1px solid var(--border);cursor:pointer"
         onclick="mcpTryTool('${t.name}')">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span style="font-size:20px">${t.icon}</span>
        <div>
          <div style="font-weight:600;font-size:13px">${t.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">${t.hint}</div>
        </div>
        <span style="margin-left:auto;font-size:11px;color:var(--accent)">▶ Try</span>
      </div>
      <div style="font-size:12.5px;color:var(--text-muted);line-height:1.5">${t.desc}</div>
    </div>`).join("");

  const servers = cfg.registeredServers.length
    ? cfg.registeredServers.map(s => `
        <div style="font-size:12.5px;padding:10px 14px;background:var(--surface-raised);
                    border-radius:6px;margin-bottom:6px">
          <span style="color:var(--accent);font-weight:600">${s.name}</span>
          <span style="color:var(--text-muted);margin-left:8px">${s.command}</span>
        </div>`).join("")
    : `<div class="alert alert-warning" style="margin:0">No servers in .claude/settings.json</div>`;

  return `
    <div class="page-header" style="display:flex;align-items:center;
         justify-content:space-between;flex-wrap:wrap;gap:10px">
      <div>
        <div class="page-title">🔌 MCP Server</div>
        <div class="page-sub">Model Context Protocol — how Claude CLI reads application data</div>
      </div>
      <span style="font-size:12px;font-weight:600;color:${statusColor};
                   background:${statusColor}22;padding:4px 12px;border-radius:12px">
        ${statusLabel}
      </span>
    </div>

    <!-- Explainer -->
    <div class="card" style="border-left:3px solid var(--accent);margin-bottom:20px">
      <div style="font-size:13px;line-height:1.8;color:var(--text-muted)">
        <strong style="color:var(--text)">How it works:</strong>
        When you open Claude Code CLI in this folder, it reads
        <code style="background:var(--surface-raised);padding:1px 6px;border-radius:4px">.claude/settings.json</code>
        and starts
        <code style="background:var(--surface-raised);padding:1px 6px;border-radius:4px">mcp-server.js</code>
        as a background subprocess. Claude can then call any of the 5 tools below — reading your SAP catalog,
        querying past decisions, or pulling cached KDDs —
        without any API key. The <strong style="color:var(--text)">Try</strong> panel below
        shows exactly the JSON Claude would receive.
      </div>
    </div>

    <!-- Data snapshot -->
    <div class="stats-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:20px">
      <div class="card">
        <div class="card-title">Catalog Items</div>
        <div class="card-value">${(cfg.dataSnapshot.catalogItems || 0).toLocaleString()}</div>
        <div class="card-note">SAP processes Claude can look up</div>
      </div>

      <div class="card">
        <div class="card-title">Saved Decisions</div>
        <div class="card-value">${(cfg.dataSnapshot.savedDecisions || 0).toLocaleString()}</div>
        <div class="card-note">Approved decisions in learning store</div>
      </div>
    </div>

    <!-- Registered servers -->
    <div class="section-divider">.claude/settings.json — Registered MCP Servers</div>
    <div style="margin-bottom:20px">${servers}</div>

    <!-- Tool cards -->
    <div class="section-divider">5 Tools Claude Can Call · Click any to try it live</div>
    <div class="skills-grid" style="margin-bottom:20px">${toolCards}</div>

    <!-- Try panel — shown when a tool card is clicked -->
    <div id="mcp-try-panel" style="display:none">
      <div class="section-divider" id="mcp-try-title">▶ Try Tool</div>
      <div class="card" style="padding:16px">
        <div style="font-size:11.5px;color:var(--text-muted);margin-bottom:8px">
          Edit the JSON arguments below then click Run — this calls the same logic
          Claude CLI calls when it uses this tool.
        </div>
        <div style="display:flex;gap:10px;margin-bottom:12px;align-items:flex-start">
          <textarea id="mcp-args-input" class="form-input" rows="4"
            style="flex:1;font-family:monospace;font-size:12px;resize:vertical"></textarea>
          <button class="btn btn-primary" onclick="mcpRunTool()" style="white-space:nowrap">
            ▶ Run
          </button>
        </div>
        <div id="mcp-running" style="display:none;color:var(--text-muted);font-size:12px;
             margin-bottom:8px">⏳ Running…</div>
        <pre id="mcp-result" style="background:var(--surface-raised);border-radius:6px;
             padding:14px;font-size:11.5px;overflow:auto;max-height:400px;margin:0;
             white-space:pre-wrap;display:none"></pre>
      </div>
    </div>`;
}

function bindMCPServer() {
  let _currentTool = null;

  const defaults = {
    "get_process_context":    '{\n  "scopeItemId": "BD6"\n}',
    "search_past_decisions":  '{\n  "scopeItemId": "",\n  "query": "payment",\n  "fitGap": ""\n}',
    "save_approved_decision": '{\n  "scopeItemId": "BD6",\n  "question": "Will standard process meet requirements?",\n  "decision": "Standard SAP process accepted",\n  "fitGap": "Fit"\n}',
    "get_cached_kdds":        '{\n  "scopeItemId": "J59"\n}',
    "list_decisions_summary": '{}'
  };

  window.mcpTryTool = (toolName) => {
    _currentTool = toolName;

    document.querySelectorAll("[id^='mcp-tool-']").forEach(el => {
      el.style.borderColor = el.id === "mcp-tool-" + toolName
        ? "var(--accent)" : "var(--border)";
    });

    const panel  = document.getElementById("mcp-try-panel");
    const title  = document.getElementById("mcp-try-title");
    const input  = document.getElementById("mcp-args-input");
    const result = document.getElementById("mcp-result");
    const run    = document.getElementById("mcp-running");

    if (panel)  panel.style.display  = "block";
    if (title)  title.textContent    = "▶ Try: " + toolName;
    if (input)  input.value          = defaults[toolName] || "{}";
    if (result) { result.textContent = ""; result.style.display = "none"; }
    if (run)    run.style.display    = "none";

    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  window.mcpRunTool = async () => {
    if (!_currentTool) return;
    const runEl    = document.getElementById("mcp-running");
    const resultEl = document.getElementById("mcp-result");
    const input    = document.getElementById("mcp-args-input");

    let args = {};
    try { args = JSON.parse(input?.value || "{}"); }
    catch { /* malformed JSON — send empty */ }

    if (runEl)    runEl.style.display    = "block";
    if (resultEl) resultEl.style.display = "none";

    try {
      const data = await api.post("/api/mcp/invoke", { tool: _currentTool, args });
      if (resultEl) {
        resultEl.textContent     = JSON.stringify(data.result, null, 2);
        resultEl.style.display   = "block";
        resultEl.style.color     = data.result?.error ? "#f87171" : "var(--text)";
      }
    } catch (e) {
      if (resultEl) {
        resultEl.textContent   = "Error: " + e.message;
        resultEl.style.display = "block";
        resultEl.style.color   = "#f87171";
      }
    } finally {
      if (runEl) runEl.style.display = "none";
    }
  };
}

// ── BDCQ Agent page ───────────────────────────────────────────────────────────
async function pagBDCQ() {
  const s = await api.get("/api/bdcq/status").catch(() => ({ found: false }));

  // ── Source card content ─────────────────────────────────────────────────────
  const sourceCard = s.found
    ? `<div class="alert alert-success" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <span>
          <strong>bdcq-questions.json loaded</strong> &nbsp;·&nbsp;
          ${s.totalDomains} domain${s.totalDomains !== 1 ? "s" : ""} &nbsp;·&nbsp;
          ${(s.totalQuestions || 0).toLocaleString()} questions
          ${s.generatedAt ? `&nbsp;·&nbsp; generated ${s.generatedAt.slice(0,10)}` : ""}
        </span>
        <label style="font-size:12px;color:var(--accent);cursor:pointer;white-space:nowrap">
          Replace file
          <input id="bdcq-replace-input" type="file" accept=".json" style="display:none" onchange="bdcqUploadFile(this.files[0])">
        </label>
      </div>`
    : `<div id="bdcq-upload-zone" class="upload-zone" style="border:2px dashed var(--border);border-radius:8px;padding:40px;text-align:center;cursor:pointer;transition:border-color .2s"
          ondragover="event.preventDefault();this.style.borderColor='var(--accent)'"
          ondragleave="this.style.borderColor='var(--border)'"
          ondrop="event.preventDefault();this.style.borderColor='var(--border)';bdcqDropFile(event.dataTransfer.files[0])"
          onclick="document.getElementById('bdcq-file-input').click()">
        <div style="font-size:32px;margin-bottom:8px">⊟</div>
        <div style="font-weight:600;margin-bottom:4px">Drop bdcq-questions.json here</div>
        <div style="font-size:13px;color:var(--text-muted)">or click to browse — generated by the SAPDeckAgent Chrome extension from SAP Roadmap Viewer</div>
        <input id="bdcq-file-input" type="file" accept=".json" style="display:none" onchange="bdcqUploadFile(this.files[0])">
      </div>`;

  // ── Domain meta (per-file columns) ─────────────────────────────────────────
  // Stored on window so bindBDCQ() can access it without re-fetching
  window._bdcqDomainMeta = s.domainMeta || [];

  // ── Country checkboxes ──────────────────────────────────────────────────────
  const countries = s.countries || [];
  const countrySection = countries.length > 0
    ? `<div class="section-divider">Countries / Localisation</div>
       <div class="form-group">
         <div class="form-label">Include locale sheets</div>
         <div style="margin-bottom:6px">
           ${countries.map(c => `
             <label style="display:inline-flex;align-items:center;gap:6px;margin:4px 8px 4px 0;font-size:13px">
               <input type="checkbox" class="bdcq-country-check" value="${c}"> ${c}
             </label>`).join("")}
         </div>
         <div class="form-hint">Leave all unchecked to include global sheets only</div>
       </div>`
    : "";

  // ── Domain multi-select — inline collapsible list ───────────────────────────
  const domainSection = s.found && window._bdcqDomainMeta.length > 0
    ? `<div class="section-divider">BDCQ Files</div>
       <div class="form-group">
         <div class="form-label">Select domains to include</div>

         <!-- Trigger row -->
         <button id="bdcq-domain-trigger" onclick="bdcqToggleDropdown()"
           style="width:100%;display:flex;align-items:center;justify-content:space-between;
                  padding:8px 12px;background:var(--surface-2);border:1px solid var(--border);
                  border-radius:6px;color:var(--text);font-size:13px;cursor:pointer;text-align:left;
                  margin-bottom:0">
           <span id="bdcq-domain-label" style="color:var(--text-muted)">Select domains…</span>
           <span id="bdcq-domain-arrow" style="font-size:10px;color:var(--text-muted)">▼</span>
         </button>

         <!-- Inline list — hidden by default, expands below trigger -->
         <div id="bdcq-domain-panel" style="display:none;border:1px solid var(--border);
              border-top:none;border-radius:0 0 6px 6px;background:var(--surface-2);
              max-height:260px;overflow-y:auto">
           <div style="display:flex;align-items:center;justify-content:flex-end;gap:16px;
                       padding:8px 14px;border-bottom:1px solid var(--border);
                       background:var(--surface-1,#f5f5f5);min-height:36px">
             <button onclick="bdcqDomainAll(true);event.stopPropagation()"
               style="background:none;border:none;color:var(--accent);font-size:12px;font-weight:600;cursor:pointer;padding:0">Select all</button>
             <span style="color:var(--border);font-size:12px">|</span>
             <button onclick="bdcqDomainAll(false);event.stopPropagation()"
               style="background:none;border:none;color:var(--text-muted);font-size:12px;cursor:pointer;padding:0">Clear</button>
           </div>
           ${window._bdcqDomainMeta.map(d => `
             <label style="display:flex;align-items:center;justify-content:space-between;
                            padding:9px 12px;cursor:pointer;font-size:13px;
                            border-bottom:1px solid rgba(255,255,255,.04)"
                    onmouseover="this.style.background='rgba(255,255,255,.05)'"
                    onmouseout="this.style.background=''">
               <span style="display:flex;align-items:center;gap:8px">
                 <input type="checkbox" class="bdcq-domain-check" value="${escHtml(d.name)}"
                   style="accent-color:var(--accent);width:14px;height:14px;flex-shrink:0"
                   onchange="bdcqOnDomainChange()">
                 ${escHtml(d.name)}
               </span>
               <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;margin-left:8px">${d.questions} q</span>
             </label>`).join("")}
         </div>
         <div class="form-hint">Select one or more — columns load automatically. Leave empty to include all files.</div>
       </div>`
    : "";

  return `
    <div class="page-header">
      <div class="page-title">BDCQ Agent</div>
      <div class="page-sub">Business Driven Configuration Questionnaire · SAP S/4HANA Cloud PE</div>
    </div>

    <!-- ── Tab bar ─────────────────────────────────────────────────────────────── -->
    <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:18px">
      <button id="bdcq-tab-configure" onclick="bdcqSwitchTab('configure')"
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:pointer;
               background:none;border-bottom:2px solid var(--accent);color:var(--accent);
               margin-bottom:-2px;transition:color .15s">
        ⚙️ Configure
      </button>
      <button id="bdcq-tab-grounding" onclick="bdcqSwitchTab('grounding')"
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:pointer;
               background:none;border-bottom:2px solid transparent;color:var(--text-muted);
               margin-left:0;position:relative">
        ◈ Grounding <span id="bdcq-grounding-badge" style="display:none;position:absolute;top:6px;right:4px;
          width:8px;height:8px;border-radius:50%;background:var(--accent)"></span>
      </button>
      <button id="bdcq-tab-editor" onclick="bdcqSwitchTab('editor')" disabled
        style="padding:10px 20px;font-size:13px;font-weight:600;border:none;cursor:not-allowed;
               background:none;border-bottom:2px solid transparent;color:var(--text-muted);
               margin-bottom:-2px;transition:color .15s;opacity:.5">
        📋 Editor
      </button>
    </div>

    <!-- ══ CONFIGURE TAB ════════════════════════════════════════════════════════ -->
    <div id="bdcq-panel-configure">

    <div class="card">
      <div class="card-title">Source — bdcq-questions.json</div>
      ${sourceCard}
    </div>

    <div class="card">
      <div class="card-title">Configuration</div>

      <div class="section-divider">Project Details</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="form-group">
          <label class="form-label">Client Name</label>
          <input id="bdcq-client" class="form-input" type="text" placeholder="e.g. Acme Corp">
        </div>
        <div class="form-group">
          <label class="form-label">Output File Name <span style="color:#f87171">*</span></label>
          <input id="bdcq-filename" class="form-input" type="text" placeholder="e.g. Acme_BDCQ_Finance">
          <div class="form-hint">.xlsx added automatically</div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
        <div class="form-group">
          <label class="form-label">Client Logo <span style="color:var(--text-muted);font-weight:400">(optional)</span></label>
          <label style="display:flex;align-items:center;gap:8px;padding:7px 10px;
                         border:1px dashed var(--border);border-radius:6px;cursor:pointer;font-size:12px;
                         color:var(--text-muted);transition:border-color .2s"
                 onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
            <span id="bdcq-client-logo-label">⊕ Upload logo (PNG/JPG)</span>
            <input id="bdcq-client-logo" type="file" accept="image/png,image/jpeg" style="display:none"
              onchange="bdcqLogoChange('client',this)">
          </label>
        </div>
        <div class="form-group">
          <label class="form-label">Partner Logo <span style="color:var(--text-muted);font-weight:400">(optional)</span></label>
          <label style="display:flex;align-items:center;gap:8px;padding:7px 10px;
                         border:1px dashed var(--border);border-radius:6px;cursor:pointer;font-size:12px;
                         color:var(--text-muted);transition:border-color .2s"
                 onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
            <span id="bdcq-partner-logo-label">⊕ Upload logo (PNG/JPG)</span>
            <input id="bdcq-partner-logo" type="file" accept="image/png,image/jpeg" style="display:none"
              onchange="bdcqLogoChange('partner',this)">
          </label>
        </div>
      </div>

      <div class="form-group" style="margin-top:10px">
        <label class="form-label">Cover Sheet Overview Text <span style="color:var(--text-muted);font-weight:400">(optional)</span></label>
        <textarea id="bdcq-overview" class="form-input" rows="2"
          placeholder="e.g. This questionnaire covers configuration decisions for the SAP S/4HANA Cloud PE implementation at Acme Corp."
          style="resize:vertical;font-size:13px"></textarea>
        <div class="form-hint">Appears on the cover sheet. Leave blank for default text.</div>
      </div>

      <div class="section-divider">Scope Filter <span style="font-weight:400;color:var(--text-muted)">(optional)</span></div>
      <div class="form-group">
        <label class="form-label">Scope Item IDs — from DDA</label>
        <div style="display:flex;gap:8px;align-items:flex-start">
          <textarea id="bdcq-scope" class="form-input" rows="2"
            placeholder="e.g. BD6, J59, BEI — comma or space separated"
            style="resize:vertical;flex:1"></textarea>
          <button class="btn btn-outline btn-sm" style="white-space:nowrap;padding:7px 12px;margin-top:1px"
            onclick="bdcqLookupScope()" title="Auto-select matching BDCQ domains">
            Find Domains →
          </button>
        </div>
        <div class="form-hint">From DDA confirmed scope — auto-selects matching domains below. Leave blank to select domains manually.</div>
        <div id="bdcq-scope-lookup-result" style="margin-top:6px"></div>
      </div>

      ${domainSection}

      <div id="bdcq-col-section" style="display:none">
        <div class="section-divider">Columns</div>
        <div class="form-group">
          <div style="margin-bottom:8px;display:flex;gap:8px;align-items:center">
            <button class="btn btn-outline btn-sm" style="font-size:12px;padding:4px 10px" onclick="bdcqColAll(true)">Select All</button>
            <button class="btn btn-outline btn-sm" style="font-size:12px;padding:4px 10px" onclick="bdcqColAll(false)">Clear All</button>
          </div>
          <div id="bdcq-col-list"></div>
          <div style="margin-top:10px;display:flex;align-items:center;gap:8px">
            <input id="bdcq-custom-col" class="form-input" type="text"
              placeholder="Add custom column…" style="max-width:240px;font-size:13px">
            <button class="btn btn-outline btn-sm" style="font-size:12px;padding:4px 10px;white-space:nowrap"
              onclick="bdcqAddCustomCol()">+ Add</button>
          </div>
          <div class="form-hint">Custom columns appear in the Excel but will be empty — ready for manual fill-in.</div>
        </div>
      </div>

      ${countrySection}

      <div class="section-divider">AI Enrichment Context
        <span style="font-size:11px;font-weight:400;color:var(--text-muted);margin-left:8px">
          — controls <em>how</em> Claude enriches, not <em>which</em> questions are included
        </span>
      </div>
      <div style="background:rgba(99,102,241,.07);border:1px solid rgba(99,102,241,.25);border-radius:6px;
                  padding:8px 12px;font-size:12px;color:var(--text-muted);margin-bottom:12px;line-height:1.5">
        ⚠️ <strong>To control which questions are enriched</strong>, use the <strong>BDCQ Files</strong> domain selector above — select only the domains in your project scope.
        Industry and Countries here only add localisation context to Claude's answers; they do not filter questions.
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="form-group">
          <label class="form-label">Industry <span style="color:var(--text-muted);font-weight:400">(for ✨ Enrich)</span></label>
          <select id="bdcq-industry" class="form-input" style="font-size:13px">
            <option value="">— Select industry —</option>
            <option value="manufacturing">Manufacturing</option>
            <option value="retail">Retail</option>
            <option value="professional services">Professional Services</option>
            <option value="pharma">Pharma / Life Sciences</option>
            <option value="utilities">Utilities</option>
            <option value="financial services">Financial Services</option>
            <option value="oil & gas">Oil &amp; Gas</option>
            <option value="high tech">High Tech</option>
          </select>
          <div class="form-hint">Frames enriched answers for your client's vertical</div>
          <div id="bdcq-domain-suggest" style="display:none"></div>
        </div>
        <div class="form-group">
          <label class="form-label">Countries in scope <span style="color:var(--text-muted);font-weight:400">(for ✨ Enrich)</span></label>
          <div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;max-height:110px;overflow-y:auto;background:var(--surface-2)">
            ${["India","Germany","USA","UK","Brazil","UAE","Saudi Arabia","France","Australia","Singapore"].map(c => `
              <label style="display:flex;align-items:center;gap:6px;margin:2px 0;font-size:12px;cursor:pointer">
                <input type="checkbox" class="bdcq-enrich-country" value="${c}" style="accent-color:var(--accent)"> ${c}
              </label>`).join("")}
          </div>
          <div class="form-hint">Adds statutory requirements (GST, GoBD, MTD, ZATCA…) to enriched answers</div>
        </div>
      </div>

      <div style="margin-top:6px;font-size:11px;color:var(--text-muted)">
        * Select at least one domain before generating
      </div>
      <div style="margin-top:8px;display:flex;gap:10px">
        <button class="btn btn-primary" style="flex:1;padding:12px" onclick="bdcqLoadEditor()">
          Generate &amp; Edit
        </button>
        <button class="btn btn-outline" style="padding:12px 18px;white-space:nowrap" onclick="bdcqGenerate()"
          title="Skip editor — generate and download Excel directly">
          Quick Export ↓
        </button>
      </div>
      <div id="bdcq-result" style="margin-top:14px"></div>
    </div>

    </div><!-- /bdcq-panel-configure -->

    <!-- ══ GROUNDING TAB ════════════════════════════════════════════════════════ -->
    <div id="bdcq-panel-grounding" style="display:none">

    <div class="card">
      <div class="card-title">Reference Grounding Library</div>
      <p style="font-size:13px;color:var(--text-muted);margin:0 0 16px">
        Upload Excel files containing your organisation's reference data — prior project answers, client-specific standards, functional team verified values, or SAP best practice documents. Claude uses the most relevant rows as primary sources when enriching BDCQ questions.
      </p>

      <!-- Upload zone -->
      <div id="bdcq-grounding-dropzone"
           style="border:2px dashed var(--border);border-radius:8px;padding:28px;text-align:center;
                  cursor:pointer;transition:border-color .2s;margin-bottom:16px"
           onclick="document.getElementById('bdcq-grounding-file').click()"
           ondragover="event.preventDefault();this.style.borderColor='var(--accent)'"
           ondragleave="this.style.borderColor='var(--border)'"
           ondrop="bdcqGroundingDrop(event)">
        <div style="font-size:28px;margin-bottom:8px">📂</div>
        <div style="font-size:14px;font-weight:600;margin-bottom:4px">Drop Excel files here or click to browse</div>
        <div style="font-size:12px;color:var(--text-muted)">.xlsx or .xls · Multiple files supported · Each sheet becomes a searchable reference block</div>
        <input id="bdcq-grounding-file" type="file" accept=".xlsx,.xls" multiple style="display:none"
               onchange="bdcqGroundingUpload(this.files)">
      </div>

      <!-- Upload status -->
      <div id="bdcq-grounding-status" style="margin-bottom:16px"></div>

      <!-- Current grounding data preview -->
      <div id="bdcq-grounding-preview"></div>
    </div>

    </div><!-- /bdcq-panel-grounding -->

    <!-- ══ EDITOR TAB ═══════════════════════════════════════════════════════════ -->
    <div id="bdcq-panel-editor" style="display:none">
      <div class="card" style="padding:0;overflow:hidden">

        <!-- Toolbar -->
        <div style="display:flex;align-items:center;justify-content:space-between;
                    padding:14px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:10px">
          <div>
            <div id="bdcq-editor-meta" style="font-size:13px;font-weight:600;color:var(--text)"></div>
            <div id="bdcq-editor-sub" style="font-size:12px;color:var(--text-muted);margin-top:2px">
              Click ✨ Enrich to add AI values, then fill Answer &amp; Notes
            </div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <button class="btn btn-outline btn-sm" style="font-size:12px" onclick="bdcqSwitchTab('configure')">
              ← Back to Configure
            </button>
            <button id="bdcq-enrich-btn" class="btn btn-outline btn-sm"
              style="display:flex;align-items:center;gap:6px;border-color:var(--accent);color:var(--accent)"
              onclick="bdcqEnrich()">
              ✨ Enrich with Claude AI
            </button>
            <span id="bdcq-grounding-enrich-status" style="display:none;align-items:center;gap:4px;
              font-size:13px;font-weight:600;color:var(--accent);padding:7px 14px;
              border:1px solid var(--accent);border-radius:6px;cursor:pointer;
              background:rgba(99,102,241,.06)" onclick="bdcqSwitchTab('grounding')"
              title="Grounding data active — click to manage">
              ◈ Grounding active
            </span>
            <button class="btn btn-primary btn-sm" style="padding:7px 16px" onclick="bdcqExportExcel()">
              Export Excel ↓
            </button>
          </div>
        </div>

        <!-- Enrich progress bar -->
        <div id="bdcq-enrich-progress" style="display:none;padding:10px 18px;
             background:rgba(99,102,241,.08);border-bottom:1px solid var(--border);
             font-size:13px;color:var(--accent)"></div>

        <!-- Local questions hint (shown when countries selected but no grounding match) -->
        <div id="bdcq-local-hint" style="display:none;padding:10px 18px;
             border-bottom:1px solid var(--border)"></div>

        <!-- Question cards -->
        <div id="bdcq-editor-list" style="max-height:calc(100vh - 220px);overflow-y:auto;padding:0 20px 16px"></div>

        <div style="padding:10px 18px;border-top:1px solid var(--border);
                    font-size:11px;color:var(--text-muted);display:flex;gap:16px">
          <span>✨ AI fields — click to edit</span>
          <span>✏️ Answer &amp; Notes — fill in workshop</span>
          <span id="bdcq-editor-count"></span>
        </div>
      </div>
    </div>`;
}

function bindBDCQ() {
  // ── File upload helpers ─────────────────────────────────────────────────────
  async function uploadJSON(file) {
    if (!file) return;
    const resultEl = document.getElementById("bdcq-result");
    if (resultEl) resultEl.innerHTML = `<div class="alert alert-info">Uploading…</div>`;

    const reader = new FileReader();
    reader.onload = async e => {
      const base64 = btoa(String.fromCharCode(...new Uint8Array(e.target.result)));
      try {
        const r = await api.post("/api/bdcq/upload", { content: base64 });
        if (r.ok) {
          // Reload to refresh status card and column list
          navigate("bdcq-agent");
        } else {
          if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Upload failed: ${r.error || "unknown error"}</div>`;
        }
      } catch (err) {
        if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Upload error: ${err.message}</div>`;
      }
    };
    reader.readAsArrayBuffer(file);
  }

  window.bdcqUploadFile = file => uploadJSON(file);
  window.bdcqDropFile   = file => uploadJSON(file);

  // ── Logo helpers ────────────────────────────────────────────────────────────
  window._bdcqLogos = { client: null, partner: null };

  const LOGO_MAX_W  = 300;   // max width in px
  const LOGO_MAX_H  = 150;   // max height in px
  const LOGO_MAX_MB = 2;     // reject originals over 2 MB before even trying
  const LOGO_QUALITY = 0.82; // JPEG compression quality

  window.bdcqLogoChange = (which, input) => {
    const file    = input.files[0];
    if (!file) return;
    const labelEl = document.getElementById(`bdcq-${which}-logo-label`);

    // Reject files over 2 MB immediately
    if (file.size > LOGO_MAX_MB * 1024 * 1024) {
      if (labelEl) labelEl.textContent = `⚠ File too large (max ${LOGO_MAX_MB}MB)`;
      input.value = "";
      return;
    }

    const reader = new FileReader();
    reader.onload = e => {
      const img = new Image();
      img.onload = () => {
        // Calculate scaled dimensions maintaining aspect ratio
        let w = img.width, h = img.height;
        if (w > LOGO_MAX_W || h > LOGO_MAX_H) {
          const ratio = Math.min(LOGO_MAX_W / w, LOGO_MAX_H / h);
          w = Math.round(w * ratio);
          h = Math.round(h * ratio);
        }
        // Draw onto canvas and compress
        const canvas = document.createElement("canvas");
        canvas.width  = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        const compressed = canvas.toDataURL("image/jpeg", LOGO_QUALITY);
        window._bdcqLogos[which] = compressed;

        const kb = Math.round(compressed.length * 0.75 / 1024);
        if (labelEl) labelEl.textContent = `✓ ${file.name} (${kb} KB)`;
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  };

  // ── Domain dropdown toggle ───────────────────────────────────────────────────
  window.bdcqToggleDropdown = () => {
    const panel   = document.getElementById("bdcq-domain-panel");
    const trigger = document.getElementById("bdcq-domain-trigger");
    const arrow   = document.getElementById("bdcq-domain-arrow");
    if (!panel) return;
    const open = panel.style.display === "none";
    panel.style.display = open ? "block" : "none";
    if (arrow) arrow.textContent = open ? "▲" : "▼";
    if (trigger) trigger.style.borderRadius = open ? "6px 6px 0 0" : "6px";
  };

  // ── Domain selection → reactive column list ─────────────────────────────────
  function renderColsForDomains() {
    const meta      = window._bdcqDomainMeta || [];
    const colList   = document.getElementById("bdcq-col-list");
    const colSection= document.getElementById("bdcq-col-section");
    const label     = document.getElementById("bdcq-domain-label");
    if (!colList) return;

    // Preserve custom columns (they have a <span> "custom" tag) before rebuilding
    const savedCustomCols = [...colList.querySelectorAll(".bdcq-col-check")]
      .filter(cb => cb.parentElement?.querySelector("span"))
      .map(cb => ({ val: cb.value, checked: cb.checked }));

    const selected = [...document.querySelectorAll(".bdcq-domain-check")]
      .filter(cb => cb.checked).map(cb => cb.value);

    // Update trigger label
    if (label) {
      label.textContent = selected.length === 0
        ? "Select domains…"
        : selected.length === 1
          ? selected[0]
          : `${selected.length} domains selected`;
      label.style.color = selected.length > 0 ? "var(--text)" : "var(--text-muted)";
    }

    // Only show columns section after at least one domain is picked
    if (!colSection) return;
    if (selected.length === 0) {
      colSection.style.display = "none";
      colList.innerHTML = "";
      return;
    }
    colSection.style.display = "block";

    // Build column union from selected domains
    const source = meta.filter(d => selected.includes(d.name));
    const colSet = new Set();
    source.forEach(d => (d.columns || []).forEach(c => { if (c) colSet.add(c); }));
    const cols = [...colSet];

    colList.innerHTML = cols.length === 0
      ? `<span class="text-muted" style="font-size:13px">No columns found for selected domains</span>`
      : cols.map(c =>
          `<label style="display:inline-flex;align-items:center;gap:6px;margin:4px 8px 4px 0;font-size:13px">
            <input type="checkbox" class="bdcq-col-check" value="${escHtml(c)}" checked> ${escHtml(c)}
          </label>`
        ).join("");

    // Re-append custom columns that were there before the rebuild
    const existingVals = new Set([...colList.querySelectorAll(".bdcq-col-check")].map(cb => cb.value.toLowerCase()));
    savedCustomCols.forEach(({ val, checked }) => {
      if (existingVals.has(val.toLowerCase())) return;
      const lbl = document.createElement("label");
      lbl.style.cssText = "display:inline-flex;align-items:center;gap:6px;margin:4px 8px 4px 0;font-size:13px";
      lbl.innerHTML = `<input type="checkbox" class="bdcq-col-check" value="${escHtml(val)}"${checked ? " checked" : ""}> ${escHtml(val)}<span style="font-size:10px;color:var(--accent);margin-left:2px">custom</span>`;
      colList.appendChild(lbl);
    });
  }

  window.bdcqOnDomainChange = () => { renderColsForDomains(); bdcqSaveState(); };

  window.bdcqDomainAll = (checked) => {
    document.querySelectorAll(".bdcq-domain-check").forEach(cb => { cb.checked = checked; });
    renderColsForDomains();
    bdcqSaveState();
  };

  // ── Persist setup state to localStorage ─────────────────────────────────────
  function bdcqSaveState() {
    try {
      const domains         = [...document.querySelectorAll(".bdcq-domain-check:checked")].map(cb => cb.value);
      const countries       = [...document.querySelectorAll(".bdcq-country-check:checked")].map(cb => cb.value);
      const enrichCountries = [...document.querySelectorAll(".bdcq-enrich-country:checked")].map(cb => cb.value);
      const state = {
        client:          document.getElementById("bdcq-client")?.value   || "",
        scope:           document.getElementById("bdcq-scope")?.value    || "",
        industry:        document.getElementById("bdcq-industry")?.value || "",
        domains,
        countries,
        enrichCountries,
      };
      sessionStorage.setItem("fulcrum-bdcq-state", JSON.stringify(state));
    } catch { /**/ }
  }

  function bdcqRestoreState() {
    try {
      localStorage.removeItem("fulcrum-bdcq-state"); // clear old persisted value
      const raw = sessionStorage.getItem("fulcrum-bdcq-state");
      if (!raw) return;
      const state = JSON.parse(raw);
      if (state.client) {
        const el = document.getElementById("bdcq-client");
        if (el) el.value = state.client;
      }
      if (state.scope) {
        const el = document.getElementById("bdcq-scope");
        if (el) el.value = state.scope;
      }
      if (state.industry) {
        const el = document.getElementById("bdcq-industry");
        if (el) el.value = state.industry;
      }
      if (state.domains?.length) {
        document.querySelectorAll(".bdcq-domain-check").forEach(cb => {
          cb.checked = state.domains.includes(cb.value);
        });
        renderColsForDomains();
      }
      if (state.countries?.length) {
        document.querySelectorAll(".bdcq-country-check").forEach(cb => {
          cb.checked = state.countries.includes(cb.value);
        });
      }
      if (state.enrichCountries?.length) {
        document.querySelectorAll(".bdcq-enrich-country").forEach(cb => {
          cb.checked = state.enrichCountries.includes(cb.value);
        });
      }
    } catch { /**/ }
  }

  // ── Domain suggestions based on industry ────────────────────────────────────
  // Keywords matched case-insensitively against the domain name from the BDCQ file.
  /* COMMENTED OUT — domain auto-selection by industry (keep for future use)
  const INDUSTRY_DOMAIN_KEYWORDS = {
    "professional services": ["project","professional","service","customer","finance","account","resource","billing","time","sales","contract"],
    "manufacturing":         ["production","manufactur","material","quality","inventory","procurement","supply","plant","warehouse","mrp","bom"],
    "retail":                ["sales","retail","store","inventory","customer","pricing","warehouse","order","point"],
    "pharma":                ["quality","batch","production","procurement","compliance","warehouse","sales","serial"],
    "utilities":             ["billing","customer","asset","maintenance","project","finance","meter","service","credit"],
    "financial services":    ["finance","account","treasury","payment","risk","compliance","customer","bank"],
    "oil & gas":             ["asset","maintenance","project","procurement","finance","inventory","warehouse","joint"],
    "high tech":             ["sales","production","material","quality","customer","service","finance","contract"]
  };

  window.bdcqSuggestDomains = (industry) => { ... };
  window.bdcqApplySuggestedDomains = (industry) => { ... };
  */

  // Auto-save on client/scope/industry field changes
  setTimeout(() => {
    document.getElementById("bdcq-client")?.addEventListener("input", bdcqSaveState);
    document.getElementById("bdcq-scope")?.addEventListener("input", bdcqSaveState);
    document.getElementById("bdcq-industry")?.addEventListener("change", bdcqSaveState);
    document.querySelectorAll(".bdcq-country-check").forEach(cb => cb.addEventListener("change", bdcqSaveState));
    document.querySelectorAll(".bdcq-enrich-country").forEach(cb => cb.addEventListener("change", bdcqSaveState));
    // Restore saved values after DOM is ready
    bdcqRestoreState();
  }, 100);

  // ── Column helpers ──────────────────────────────────────────────────────────
  window.bdcqColAll = (checked) => {
    document.querySelectorAll(".bdcq-col-check").forEach(cb => { cb.checked = checked; });
  };

  window.bdcqAddCustomCol = () => {
    const input = document.getElementById("bdcq-custom-col");
    const val   = (input?.value || "").trim();
    if (!val) return;
    const list  = document.getElementById("bdcq-col-list");
    if (!list) return;
    const existing = [...list.querySelectorAll(".bdcq-col-check")].map(cb => cb.value.toLowerCase());
    if (existing.includes(val.toLowerCase())) { input.value = ""; return; }
    const label = document.createElement("label");
    label.style.cssText = "display:inline-flex;align-items:center;gap:6px;margin:4px 8px 4px 0;font-size:13px";
    label.innerHTML = `<input type="checkbox" class="bdcq-col-check" value="${escHtml(val)}" checked> ${escHtml(val)}
      <span style="font-size:10px;color:var(--accent);margin-left:2px">custom</span>`;
    list.appendChild(label);
    input.value = "";
    // Track immediately in editor state so export picks it up even before re-generating
    if (window._bdcqEditorState && !window._bdcqEditorState.columns.includes(val)) {
      window._bdcqEditorState.columns.push(val);
    }
  };

  // ── Tab switching ────────────────────────────────────────────────────────────
  window.bdcqSwitchTab = (tab) => {
    const tabs   = ["configure", "grounding", "editor"];
    tabs.forEach(t => {
      const btn   = document.getElementById(`bdcq-tab-${t}`);
      const panel = document.getElementById(`bdcq-panel-${t}`);
      const active = t === tab;
      if (btn) {
        btn.style.borderBottomColor = active ? "var(--accent)" : "transparent";
        btn.style.color             = active ? "var(--accent)" : "var(--text-muted)";
        btn.style.opacity           = "1";
        btn.style.cursor            = "pointer";
        btn.disabled                = false;
      }
      if (panel) panel.style.display = active ? "block" : "none";
    });
  };

  // ── Grounding library functions ───────────────────────────────────────────────

  async function bdcqGroundingRefresh() {
    const preview  = document.getElementById("bdcq-grounding-preview");
    const badge    = document.getElementById("bdcq-grounding-badge");
    const enrichEl = document.getElementById("bdcq-grounding-enrich-status");
    try {
      const g = await api.get("/api/bdcq/grounding");
      if (!g.exists || !g.files.length) {
        if (preview)  preview.innerHTML  = `<div style="font-size:13px;color:var(--text-muted);padding:12px 0">No grounding files uploaded yet. Drop an Excel above to get started.</div>`;
        if (badge)    badge.style.display = "none";
        if (enrichEl) enrichEl.style.display = "none";
        return;
      }

      if (badge) badge.style.display = "block";

      // Build one card per file
      const fileCards = g.files.map(f => {
        const uploadedAt = f.uploadedAt ? new Date(f.uploadedAt).toLocaleString() : "—";
        const sheetRows  = (f.sheets || []).map(s => `
          <tr>
            <td style="padding:5px 10px;font-size:13px;font-weight:500">${escHtml(s.sheet)}</td>
            <td style="padding:5px 10px;font-size:11px;color:var(--text-muted)">${escHtml(s.columns.join(", "))}</td>
            <td style="padding:5px 10px;font-size:12px;text-align:right;white-space:nowrap">${s.rowCount.toLocaleString()} rows</td>
          </tr>`).join("");
        return `
          <div style="border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:12px">
            <div style="display:flex;align-items:center;justify-content:space-between;
                        padding:10px 14px;background:var(--surface-2);border-bottom:1px solid var(--border)">
              <div>
                <div style="font-size:13px;font-weight:600">◈ ${escHtml(f.origName)}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:2px">
                  ${f.totalRows.toLocaleString()} rows · ${(f.sheets||[]).length} sheet(s) · uploaded ${uploadedAt}
                </div>
              </div>
              <button class="btn btn-outline btn-sm"
                style="font-size:11px;color:#f87171;border-color:#f87171;padding:4px 10px;white-space:nowrap"
                onclick="bdcqGroundingDelete('${escHtml(f.slug)}', this)">🗑 Remove</button>
            </div>
            <table style="width:100%;border-collapse:collapse">
              <thead>
                <tr style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;
                           background:var(--surface-1,rgba(255,255,255,.03))">
                  <th style="padding:5px 10px;text-align:left;font-weight:600">Sheet</th>
                  <th style="padding:5px 10px;text-align:left;font-weight:600">Columns</th>
                  <th style="padding:5px 10px;text-align:right;font-weight:600">Rows</th>
                </tr>
              </thead>
              <tbody>${sheetRows}</tbody>
            </table>
          </div>`;
      }).join("");

      if (preview) preview.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
          <div style="font-size:13px;font-weight:600;color:var(--accent)">
            ✅ ${g.files.length} file(s) · ${g.totalRows.toLocaleString()} total rows · active during enrichment
          </div>
          <button class="btn btn-outline btn-sm" style="font-size:11px;color:#f87171;border-color:#f87171"
            onclick="bdcqGroundingClearAll()">🗑 Remove all</button>
        </div>
        ${fileCards}
        <div style="margin-top:4px;font-size:12px;color:var(--text-muted)">
          💡 Claude searches all rows above for context when you click <strong>✨ Enrich</strong>.
          Add more files any time — each is stored separately in <code>bdcq/grounding/</code>.
        </div>`;

      if (enrichEl) { enrichEl.style.display = "inline-flex"; enrichEl.title = `${g.totalRows} grounding rows active from ${g.files.length} file(s)`; }
    } catch { /* silent */ }
  }

  window.bdcqGroundingUpload = async (fileList) => {
    const statusEl = document.getElementById("bdcq-grounding-status");
    const zone     = document.getElementById("bdcq-grounding-dropzone");
    if (!fileList || !fileList.length) return;

    if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:var(--accent);padding:8px 0">⏳ Parsing ${fileList.length} file(s)…</div>`;
    if (zone) zone.style.borderColor = "var(--accent)";

    const files = [];
    for (const f of fileList) {
      const buf   = await f.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = "";
      bytes.forEach(b => { bin += String.fromCharCode(b); });
      files.push({ filename: f.name, content: btoa(bin) });
    }

    try {
      const r = await api.post("/api/bdcq/upload-grounding", { files });
      if (r.ok) {
        const totalRows = (r.saved || []).reduce((s, f) => s + f.totalRows, 0);
        if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:#34d399;padding:8px 0">
          ✅ Saved ${r.saved.length} file(s) → <code>bdcq/grounding/</code> · ${totalRows.toLocaleString()} rows</div>`;
        await bdcqGroundingRefresh();
      } else {
        if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:#f87171;padding:8px 0">❌ ${escHtml(r.error || "Upload failed")}</div>`;
      }
    } catch (e) {
      if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:#f87171;padding:8px 0">❌ ${escHtml(e.message)}</div>`;
    } finally {
      if (zone) zone.style.borderColor = "var(--border)";
      const inp = document.getElementById("bdcq-grounding-file");
      if (inp) inp.value = "";
    }
  };

  window.bdcqGroundingDrop = async (event) => {
    event.preventDefault();
    document.getElementById("bdcq-grounding-dropzone").style.borderColor = "var(--border)";
    const files = event.dataTransfer?.files;
    if (files && files.length) await bdcqGroundingUpload(files);
  };

  window.bdcqGroundingDelete = async (slug, btn) => {
    if (!confirm(`Remove this grounding file?`)) return;
    if (btn) { btn.disabled = true; btn.textContent = "Removing…"; }
    try {
      await api.delete(`/api/bdcq/grounding/${encodeURIComponent(slug)}`);
      await bdcqGroundingRefresh();
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = "🗑 Remove"; }
      alert("Error: " + e.message);
    }
  };

  window.bdcqGroundingClearAll = async () => {
    if (!confirm("Remove all grounding files? Claude will no longer use them for enrichment.")) return;
    const statusEl = document.getElementById("bdcq-grounding-status");
    try {
      await api.delete("/api/bdcq/grounding");
      if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:var(--text-muted);padding:8px 0">All grounding files removed.</div>`;
      await bdcqGroundingRefresh();
    } catch (e) {
      if (statusEl) statusEl.innerHTML = `<div style="font-size:13px;color:#f87171;padding:8px 0">❌ ${escHtml(e.message)}</div>`;
    }
  };

  // Load grounding status on page init
  bdcqGroundingRefresh();

  // ── Scope ID → Domain auto-select (DDA optional) ────────────────────────────
  window.bdcqLookupScope = async () => {
    const scopeRaw  = (document.getElementById("bdcq-scope")?.value || "").trim();
    const resultEl  = document.getElementById("bdcq-scope-lookup-result");
    if (!scopeRaw) {
      if (resultEl) resultEl.innerHTML = `<span style="color:var(--text-muted);font-size:12px">Enter scope IDs first.</span>`;
      return;
    }
    if (resultEl) resultEl.innerHTML = `<span style="font-size:12px;color:var(--text-muted)">Looking up…</span>`;
    const ids = scopeRaw.split(/[,;\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    try {
      const r = await api.get(`/api/bdcq/scope-lookup?ids=${ids.join(",")}`);
      if (r.error) {
        if (resultEl) resultEl.innerHTML = `<span style="color:#f87171;font-size:12px">${r.error}</span>`;
        return;
      }
      if (!r.domains || r.domains.length === 0) {
        if (resultEl) resultEl.innerHTML = `<span style="color:var(--text-muted);font-size:12px">No matching BDCQ domains found for those IDs.</span>`;
        return;
      }
      // Auto-check matching domain checkboxes
      let matched = 0;
      document.querySelectorAll(".bdcq-domain-check").forEach(cb => {
        if (r.domains.includes(cb.value)) { cb.checked = true; matched++; }
      });
      renderColsForDomains();
      // Open dropdown so user sees selections
      const panel = document.getElementById("bdcq-domain-panel");
      if (panel && panel.style.display === "none") bdcqToggleDropdown();
      if (resultEl) resultEl.innerHTML =
        `<span style="color:var(--accent);font-size:12px">✓ Auto-selected ${matched} domain${matched !== 1 ? "s" : ""}` +
        ` from ${r.matchedIds?.length || 0} scope item${(r.matchedIds?.length || 0) !== 1 ? "s" : ""}` +
        ` (LOBs: ${(r.matchedLobs || []).join(", ")})</span>`;
    } catch (err) {
      if (resultEl) resultEl.innerHTML = `<span style="color:#f87171;font-size:12px">Lookup error: ${err.message}</span>`;
    }
  };

  // ── Editor state ─────────────────────────────────────────────────────────────
  // Holds the current rows (with AI enrichment merged in) and column list.
  window._bdcqEditorState = { rows: [], columns: [], questionCol: "Question" };

  // ── Generate & Edit — load editor ───────────────────────────────────────────
  window.bdcqLoadEditor = async () => {
    const resultEl = document.getElementById("bdcq-result");

    const scopeRaw      = (document.getElementById("bdcq-scope")?.value || "").trim();
    const selectedDomains = [...document.querySelectorAll(".bdcq-domain-check")].filter(cb => cb.checked).map(cb => cb.value);
    const selectedColumns = [...document.querySelectorAll(".bdcq-col-check")].filter(cb => cb.checked).map(cb => cb.value);
    // Merge both country selectors: per-domain sheet filter (.bdcq-country-check)
    // + enrichment countries (.bdcq-enrich-country) — so AllCountries local questions
    // are injected for whichever countries the user has ticked in either place.
    const countries = [...new Set([
      ...[...document.querySelectorAll(".bdcq-country-check:checked")].map(cb => cb.value),
      ...[...document.querySelectorAll(".bdcq-enrich-country:checked")].map(cb => cb.value),
    ])];
    const scopeIds        = scopeRaw ? scopeRaw.split(/[,;\s]+/).map(s => s.trim()).filter(Boolean) : [];

    // Domain selection is mandatory — prevents accidentally loading all 1400+ questions
    if (!selectedDomains.length) {
      if (resultEl) resultEl.innerHTML = `
        <div class="alert" style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);color:#fca5a5;border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.6">
          <strong style="font-size:14px">Select at least one domain to continue.</strong><br>
          Open <strong>BDCQ Files → Select domains</strong> above and tick the domains in your project scope.<br>
          <span style="color:var(--text-muted);font-size:11px;margin-top:6px;display:block">
            Tip: Pick an industry in <em>AI Enrichment Context</em> below — a domain suggestion button will appear to help you choose.
          </span>
        </div>`;
      // Scroll the domain picker into view so the user can act immediately
      document.getElementById("bdcq-domain-label")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    if (resultEl) resultEl.innerHTML = `<div class="alert alert-info">Loading questions…</div>`;

    try {
      const r = await api.post("/api/bdcq/preview", { scopeIds, selectedDomains, selectedColumns, countries });
      if (!r.ok) {
        if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Error: ${r.error || "unknown"}</div>`;
        return;
      }
      if (r.rowCount === 0) {
        if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">No questions matched your filters. Try selecting more domains or clearing scope IDs.</div>`;
        return;
      }

      // Store state
      window._bdcqEditorState.columns    = r.columns;
      window._bdcqEditorState.questionCol = r.questionCol;
      const _qCol = r.questionCol || "Question";
      const INDUSTRY_LABELS = {
        "manufacturing": "Manufacturing", "retail": "Retail",
        "professional services": "Professional Services", "pharma": "Pharma / Life Sciences",
        "utilities": "Utilities", "financial services": "Financial Services",
        "oil & gas": "Oil & Gas", "high tech": "High Tech",
      };
      const _industryRaw   = document.getElementById("bdcq-industry")?.value || "";
      const _industryLabel = INDUSTRY_LABELS[_industryRaw] || _industryRaw;
      // Add AI + Answer + Notes fields to each row; normalize Level/Industry/questionCol for local rows;
      // override Industry for ALL rows with the UI-selected industry (so all rows show the client's vertical)
      window._bdcqEditorState.rows = r.rows.map(row => {
        const isLocal = row._fromAllCountries;
        return {
          ...row,
          // Ensure question text lands in whichever column the regular rows use
          ...(_qCol !== "Question" && !row[_qCol] && row.Question ? { [_qCol]: row.Question } : {}),
          // Override Industry for every row with the normalized UI selection
          ...(_industryLabel ? { Industry: _industryLabel } : {}),
          // Fill Level for local rows (country-specific items are always config-level L3)
          ...(isLocal && !row.Level ? { Level: "L3" } : {}),
          "SAP Standard Value":    "",
          "Watch Out / Constraint": "",
          "Country / Region":      row["Country / Region"] || "",
          "Answer": "",
          "Notes":  ""
        };
      });

      if (resultEl) resultEl.innerHTML = "";
      renderEditorCards();

      // Switch to editor tab first, then render (so DOM elements exist)
      bdcqSwitchTab("editor");
      window.scrollTo({ top: 0, behavior: "smooth" });

      const visibleCount  = renderEditorCards();
      const localCount    = (r.rows || []).filter(row => row._fromAllCountries).length;
      const metaEl  = document.getElementById("bdcq-editor-meta");
      const countEl = document.getElementById("bdcq-editor-count");
      if (metaEl) metaEl.textContent =
        `${visibleCount} question${visibleCount !== 1 ? "s" : ""} · ${selectedDomains.length ? selectedDomains.join(", ") : "all domains"}`;
      if (countEl) countEl.textContent = `${visibleCount} questions`;

      // Show local-question hint whenever countries are selected — regardless of grounding row count
      const enrichCountriesSel = [...document.querySelectorAll(".bdcq-enrich-country:checked")].map(cb => cb.value);
      const hintEl = document.getElementById("bdcq-local-hint");
      if (hintEl) {
        if (enrichCountriesSel.length > 0) {
          const domainsStr   = escHtml(selectedDomains.join(", "));
          const countriesStr = escHtml(enrichCountriesSel.join(", "));
          const hintHeading  = localCount > 0
            ? `${localCount} grounding row${localCount > 1 ? "s" : ""} found — ask Claude to add more local questions`
            : "No SAP reference data found for these countries";
          hintEl.style.display = "block";
          hintEl.innerHTML = `
            <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:4px 0">
              <div style="font-size:20px;flex-shrink:0">🌍</div>
              <div style="flex:1;min-width:180px">
                <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:3px">
                  ${hintHeading}
                </div>
                <div style="font-size:12px;color:var(--text-muted);line-height:1.5">
                  <strong>${domainsStr}</strong> + <strong>${countriesStr}</strong> —
                  AllCountries BDCQ covers Finance, HR/Payroll, Procurement, Manufacturing, Supply Chain.
                </div>
              </div>
              <button id="bdcq-ai-local-btn"
                style="background:linear-gradient(135deg,#6d28d9 0%,#7c3aed 100%);
                       color:#fff;border:none;border-radius:8px;padding:10px 20px;
                       font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap;
                       box-shadow:0 2px 10px rgba(109,40,217,.4);letter-spacing:.01em;
                       flex-shrink:0;transition:opacity .15s">
                ✨ Ask Claude for Local Questions
              </button>
            </div>`;
          // Attach click handler directly — avoids JSON-in-onclick quote-escaping bug
          const aiLocalIndustry = document.getElementById("bdcq-industry")?.value || "";
          document.getElementById("bdcq-ai-local-btn")?.addEventListener("click", () => {
            window.bdcqGenerateAiLocalQuestions(selectedDomains, enrichCountriesSel, aiLocalIndustry);
          });
        } else {
          hintEl.style.display = "none";
          hintEl.innerHTML = "";
        }
      }
    } catch (err) {
      if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Error: ${err.message}</div>`;
    }
  };

  // ── Card-based editor renderer ───────────────────────────────────────────────
  const AI_COLS   = ["SAP Standard Value", "Watch Out / Constraint", "Country / Region", "Answer", "Notes"];
  const META_COLS = new Set(["Module", "Section", ...AI_COLS]);

  // Find the best question text from a row's own keys.
  // Priority: (1) col named "question", (2) longest text value > 20 chars,
  //           (3) any non-empty non-metadata value, (4) null → skip row.
  function getQuestionText(row) {
    const SKIP = META_COLS;
    const dataEntries = Object.entries(row).filter(([k, v]) => !SKIP.has(k) && String(v || "").trim().length > 0);
    if (!dataEntries.length) return null;

    // Priority 1 — col explicitly named "question"
    const qEntry = dataEntries.find(([k]) => k.toLowerCase() === "question");
    if (qEntry && String(qEntry[1]).trim().length > 2) return String(qEntry[1]).trim();

    // Priority 2 — longest value that reads like a question (>20 chars)
    const byLength = [...dataEntries]
      .map(([, v]) => String(v).trim())
      .filter(v => v.length > 20)
      .sort((a, b) => b.length - a.length);
    if (byLength.length) return byLength[0];

    // Priority 3 — any non-empty value (catches short answers like "Y/N")
    const anyVal = String(dataEntries[0][1]).trim();
    if (anyVal.length > 0) return anyVal;

    return null;
  }

  // Detail fields: short metadata shown as secondary labels under the question text.
  // Excludes the value already used as the question text.
  function getDetailFields(row, columns) {
    const DETAIL_KEYS = ["solution processes", "process area", "topic", "industry", "level",
                         "systems", "scope ref", "sap id", "project relevant", "business process"];
    const qText   = getQuestionText(row) || "";
    const results = [];

    for (const col of columns) {
      if (META_COLS.has(col)) continue;
      const v = String(row[col] || "").trim();
      if (!v || v === qText) continue;
      const colLow = col.toLowerCase();
      // Include if it's a known detail column OR a short categorical value
      if (DETAIL_KEYS.some(k => colLow.includes(k)) || (v.length < 80 && v !== qText)) {
        results.push({ col, val: v });
        if (results.length >= 4) break;
      }
    }
    return results;
  }

  function editableDiv(rowIdx, col, value, placeholder) {
    return `<div contenteditable="true"
      style="min-height:36px;outline:none;line-height:1.6;padding:6px 8px;
             border-radius:4px;border:1px solid var(--border);background:var(--surface-1,#fff1);
             font-size:12px;color:var(--text);transition:border-color .15s"
      data-row="${rowIdx}" data-col="${escHtml(col)}"
      oninput="bdcqEditorCell(this)"
      onfocus="this.style.borderColor='var(--accent)'"
      onblur="this.style.borderColor='var(--border)'"
      >${escHtml(value)}</div>
    ${!value ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${placeholder}</div>` : ""}`;
  }

  function renderEditorCards() {
    const { rows, columns } = window._bdcqEditorState;
    const listEl = document.getElementById("bdcq-editor-list");
    if (!listEl) return;

    if (!rows.length) { listEl.innerHTML = `<p style="color:var(--text-muted);padding:20px 0">No questions to display.</p>`; return; }

    // Group rows by Section (within Module)
    const groups = [];
    let lastKey = null;
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const key = `${row.Module || ""}||${row.Section || ""}`;
      if (key !== lastKey) {
        groups.push({ module: row.Module || "", section: row.Section || "", startIdx: i, endIdx: i });
        lastKey = key;
      } else {
        groups[groups.length - 1].endIdx = i;
      }
    }

    // Sections that are NOT configuration questions — skip entirely
    const NON_QUESTION_SECTIONS = /^(template overview|change history|cover|instructions|overview|version history|revision history|changelog)$/i;

    let visibleQCounter = 0; // sequential number across all sections

    listEl.innerHTML = groups.map(g => {
      // Skip entire non-question sections upfront
      if (NON_QUESTION_SECTIONS.test((g.section || "").trim())) return "";

      const groupRows = rows.slice(g.startIdx, g.endIdx + 1);

      // Build question cards first so we know the real visible count
      const questionCards = groupRows.map((row) => {
        const qText = getQuestionText(row);
        if (!qText) return ""; // skip rows with no question content
        const rowIdx = rows.indexOf(row); // real index for editor state updates
        visibleQCounter++;
        const displayNum  = visibleQCounter;
        const details     = getDetailFields(row, columns);
        const sv          = row["SAP Standard Value"]    || "";
        const wo          = row["Watch Out / Constraint"] || "";
        const cr          = row["Country / Region"]       || "";
        const ans         = row["Answer"] || "";
        const notes       = row["Notes"]  || "";
        const isLocaleRow  = /^[A-Za-z]{2}$/.test(row.Section || "") || row.Section === "LC";
        const isAllCountry = row.Section === "LC";
        const isAiGen      = !!row._aiGenerated;
        const badgeLabel   = isAllCountry ? "Local – All Countries" : (row.Section || "");
        const badgeColor   = isAllCountry ? "#065f46" : "#1d4ed8";
        const badgeBg      = isAllCountry ? "#d1fae5"  : "#dbeafe";
        const localeBadge  = isLocaleRow
          ? `<span style="font-size:9px;font-weight:700;padding:2px 6px;background:${badgeBg};color:${badgeColor};border-radius:3px;flex-shrink:0;margin-top:2px">🌍 ${escHtml(badgeLabel)}</span>`
          : "";
        const aiBadge      = isAiGen
          ? `<span style="font-size:9px;font-weight:700;padding:2px 6px;background:#ede9fe;color:#6d28d9;border-radius:3px;flex-shrink:0;margin-top:2px">✨ AI Local Question</span>`
          : "";
        const borderColor  = isAiGen ? "#a78bfa" : isAllCountry ? "#6ee7b7" : "#93c5fd";

        return `
        <div style="padding:14px 0;border-bottom:1px solid var(--border)${(isLocaleRow || isAiGen) ? `;border-left:3px solid ${borderColor};padding-left:10px` : ""}">

          <!-- Question number + text + delete -->
          <div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:10px">
            <div style="min-width:28px;height:22px;background:var(--surface-2);border-radius:4px;
                        display:flex;align-items:center;justify-content:center;
                        font-size:10px;font-weight:700;color:var(--text-muted);flex-shrink:0;margin-top:1px">
              ${displayNum}
            </div>
            <div style="flex:1">
              <div style="display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap">
                <div style="font-size:13px;font-weight:500;color:var(--text);line-height:1.5;flex:1">${escHtml(qText)}</div>
                ${localeBadge}${aiBadge}
              </div>
              ${details.map(d =>
                `<div style="font-size:11px;color:var(--text-muted);margin-top:3px">
                  <strong>${escHtml(d.col)}:</strong> ${escHtml(d.val)}
                </div>`).join("")}
            </div>
            <button onclick="bdcqDeleteRow(${rowIdx})"
              style="flex-shrink:0;align-self:flex-start;background:none;
                     border:1px solid var(--border);color:#9ca3af;cursor:pointer;
                     font-size:11px;font-weight:600;padding:3px 8px;border-radius:4px;
                     white-space:nowrap;transition:color .15s,background .15s,border-color .15s"
              onmouseover="this.style.color='#dc2626';this.style.background='#fee2e2';this.style.borderColor='#fca5a5'"
              onmouseout="this.style.color='#9ca3af';this.style.background='none';this.style.borderColor='var(--border)'"
              title="Delete this question">✕ Delete</button>
          </div>

          <!-- AI columns (2-col grid) -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
            <div>
              <div style="font-size:11px;font-weight:600;color:var(--accent);margin-bottom:4px">✨ SAP Standard Value</div>
              ${editableDiv(rowIdx, "SAP Standard Value", sv, "Click Enrich to auto-fill or type manually")}
            </div>
            <div>
              <div style="font-size:11px;font-weight:600;color:#f59e0b;margin-bottom:4px">⚠️ Watch Out / Constraint</div>
              ${editableDiv(rowIdx, "Watch Out / Constraint", wo, "Known constraints or irreversible settings")}
            </div>
          </div>

          <!-- Country / Region (from AllCountries grounding — shown only when populated) -->
          ${cr ? `
          <div style="margin-bottom:10px;padding:6px 10px;background:rgba(16,185,129,.06);
                      border:1px solid rgba(16,185,129,.25);border-radius:6px;
                      display:flex;align-items:flex-start;gap:8px">
            <span style="font-size:10px;font-weight:700;color:#10b981;white-space:nowrap;margin-top:1px">
              Country / Region
            </span>
            <span style="font-size:11px;color:var(--text);line-height:1.5">${escHtml(cr)}</span>
          </div>` : ""}

          <!-- Answer + Notes (2-col grid) -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <div>
              <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:4px">✏️ Answer</div>
              ${editableDiv(rowIdx, "Answer", ans, "Fill in during Fit-to-Standard workshop")}
            </div>
            <div>
              <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:4px">📝 Notes</div>
              ${editableDiv(rowIdx, "Notes", notes, "Open points, decisions, follow-ups")}
            </div>
          </div>

        </div>`;
      });

      const questionsHtml  = questionCards.join("");
      const visibleCount   = questionCards.filter(Boolean).length;

      // Skip section entirely if it has no visible questions
      if (visibleCount === 0) return "";

      const isLocaleSection = /^[A-Za-z]{2}$/.test(g.section || "");
      const sectionHtml = `
        <div style="background:${isLocaleSection ? "#eff6ff" : "var(--surface-2)"};
                    border:1px solid ${isLocaleSection ? "#bfdbfe" : "var(--border)"};
                    border-radius:6px;padding:8px 14px;margin:16px 0 4px">
          <span style="font-size:11px;font-weight:700;color:var(--accent);letter-spacing:.5px;text-transform:uppercase">
            ${escHtml(g.module)}
          </span>
          <span style="font-size:11px;color:var(--text-muted);margin:0 6px">›</span>
          ${isLocaleSection
            ? `<span style="font-size:11px;font-weight:700;color:#1d4ed8">🌍 Localization: ${escHtml(g.section)}</span>`
            : `<span style="font-size:11px;font-weight:600;color:var(--text)">${escHtml(g.section)}</span>`
          }
          <span style="font-size:11px;color:var(--text-muted);margin-left:8px">${visibleCount} question${visibleCount !== 1 ? "s" : ""}</span>
        </div>`;

      return `<div style="margin-bottom:8px">${sectionHtml}${questionsHtml}</div>`;
    }).join("");

    return visibleQCounter; // total visible questions rendered
  }

  // ── Delete a BDCQ question row ──────────────────────────────────────────────
  window.bdcqDeleteRow = (rowIdx) => {
    const rows = window._bdcqEditorState.rows;
    if (rowIdx < 0 || rowIdx >= rows.length) return;
    const qText = getQuestionText(rows[rowIdx]) || "this question";
    if (!confirm(`Delete question?\n"${qText.slice(0, 100)}${qText.length > 100 ? "…" : ""}"\n\nClick Back to Configure → Generate & Edit to restore.`)) return;
    rows.splice(rowIdx, 1);
    const count  = renderEditorCards();
    const metaEl = document.getElementById("bdcq-editor-meta");
    if (metaEl) {
      const parts = metaEl.textContent.split(" · ");
      parts[0] = `${count} question${count !== 1 ? "s" : ""}`;
      metaEl.textContent = parts.join(" · ");
    }
    const countEl = document.getElementById("bdcq-editor-count");
    if (countEl) countEl.textContent = `${count} questions`;
  };

  // Called when user edits a cell
  window.bdcqEditorCell = (el) => {
    const rowIdx = parseInt(el.dataset.row, 10);
    const col    = el.dataset.col;
    if (isNaN(rowIdx) || !col) return;
    window._bdcqEditorState.rows[rowIdx][col] = el.innerText.trim();
  };

  // ── Enrich with Claude AI ────────────────────────────────────────────────────
  // ── AI-generated local questions (when AllCountries has no match) ─────────────
  window.bdcqGenerateAiLocalQuestions = async (domains, countries, industry) => {
    const btn     = document.getElementById("bdcq-ai-local-btn");
    const hintEl  = document.getElementById("bdcq-local-hint");
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = "0.7";
      btn.innerHTML = `<span style="display:inline-block;animation:spin 1s linear infinite;margin-right:6px">⟳</span> Asking Claude for Local Questions…`;
    }

    try {
      for (const domain of domains) {
        const r = await api.post("/api/bdcq/ai-local-questions", { domain, countries, industry });
        if (!r.ok) throw new Error(r.error || "Unknown error");

        // Inject rows into editor state — align with regular row schema
        const _qCol2 = window._bdcqEditorState.questionCol || "Question";
        const _INDUSTRY_LABELS = {
          "manufacturing": "Manufacturing", "retail": "Retail",
          "professional services": "Professional Services", "pharma": "Pharma / Life Sciences",
          "utilities": "Utilities", "financial services": "Financial Services",
          "oil & gas": "Oil & Gas", "high tech": "High Tech",
        };
        const _industryNorm = _INDUSTRY_LABELS[(industry || "").toLowerCase()] || industry || "";
        const newRows = (r.rows || []).map(row => ({
          ...row,
          // Copy question text into whichever column regular rows use
          ...(_qCol2 !== "Question" ? { [_qCol2]: row.Question || "" } : {}),
          Level:    row.Level    || "L3",
          Industry: _industryNorm || row.Industry || "",
          "SAP Standard Value":    row["Sample Value"] || "",
          "Watch Out / Constraint": "",
          "Country / Region":      row["Country / Region"] || "",
          "Answer": "",
          "Notes":  ""
        }));

        window._bdcqEditorState.rows.push(...newRows);
        // Ensure Level and Industry columns are registered (so they appear in Excel export)
        for (const col of ["Level", "Industry"]) {
          if (!window._bdcqEditorState.columns.includes(col)) {
            const aiColsSet = new Set(["SAP Standard Value","Watch Out / Constraint","Country / Region","Answer","Notes"]);
            const insertBefore = window._bdcqEditorState.columns.findIndex(c => aiColsSet.has(c));
            if (insertBefore >= 0) window._bdcqEditorState.columns.splice(insertBefore, 0, col);
            else window._bdcqEditorState.columns.push(col);
          }
        }
      }

      const hintEl2 = document.getElementById("bdcq-local-hint");
      if (hintEl2) { hintEl2.style.display = "none"; hintEl2.innerHTML = ""; }
      renderEditorCards();

      // Update meta count
      const total   = window._bdcqEditorState.rows.length;
      const metaEl  = document.getElementById("bdcq-editor-meta");
      const countEl = document.getElementById("bdcq-editor-count");
      const aiCount = window._bdcqEditorState.rows.filter(r => r._aiGenerated).length;
      if (metaEl) metaEl.textContent = metaEl.textContent.split("·")[0].trim() +
        ` · +${aiCount} AI-Generated Local Questions`;
      if (countEl) countEl.textContent = `${total} questions`;

    } catch (err) {
      if (hintEl) hintEl.innerHTML +=
        `<div style="margin-top:8px;padding:8px 12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:6px;font-size:12px;color:#dc2626">
          ⚠️ Claude error: ${escHtml(err.message)}
        </div>`;
      if (btn) {
        btn.disabled = false;
        btn.style.opacity = "1";
        btn.innerHTML = "✨ Ask Claude for Local Questions";
      }
    }
  };

  window.bdcqEnrich = async () => {
    const { rows, questionCol } = window._bdcqEditorState;
    if (!rows.length) return;

    // Read enrichment context — industry + countries selected on configure tab
    const enrichIndustry  = document.getElementById("bdcq-industry")?.value || "";
    const enrichCountries = [...document.querySelectorAll(".bdcq-enrich-country:checked")].map(cb => cb.value);

    const btnEl      = document.getElementById("bdcq-enrich-btn");
    const progressEl = document.getElementById("bdcq-enrich-progress");
    const BATCH      = 20;

    // Warn if enriching a large set — suggest scoping to relevant domains first
    if (rows.length > 300) {
      const domainCount = new Set(rows.map(r => r.Module || "General")).size;
      const proceed = confirm(
        `You are about to enrich ${rows.length} questions across ${domainCount} domains.\n\n` +
        `This will take approximately ${Math.ceil(rows.length / BATCH)} Claude calls and may run for 10–20 minutes.\n\n` +
        `Tip: Go back to Configure and select only the domains relevant to your project scope — typically 3–5 domains (200–300 questions) is enough.\n\n` +
        `Continue anyway?`
      );
      if (!proceed) {
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = "✨ Enrich with Claude AI"; }
        return;
      }
    }

    if (btnEl) { btnEl.disabled = true; btnEl.textContent = "Enriching…"; }
    if (progressEl) progressEl.style.display = "block";

    const _enrichStart = Date.now();
    function _elapsed() {
      const s = Math.floor((Date.now() - _enrichStart) / 1000);
      return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
    }
    let _enrichStatusText = "";
    function _setStatus(html) {
      _enrichStatusText = html;
      if (progressEl) progressEl.innerHTML = `${html} &nbsp;·&nbsp; ⏱ <span id="bdcq-timer">${_elapsed()}</span>`;
    }
    const _timerTick = setInterval(() => {
      const t = document.getElementById("bdcq-timer");
      if (t) t.textContent = _elapsed();
    }, 1000);

    // Group rows by domain, then collect every batch as a flat list
    const domainGroups = {};
    rows.forEach((row, idx) => {
      const dom = row.Module || "General";
      if (!domainGroups[dom]) domainGroups[dom] = [];
      domainGroups[dom].push({ idx, row });
    });

    const allBatches = [];
    for (const [domain, entries] of Object.entries(domainGroups)) {
      for (let b = 0; b < entries.length; b += BATCH) {
        allBatches.push({ domain, batch: entries.slice(b, b + BATCH) });
      }
    }

    let done = 0;
    const total = rows.length;
    const errors = [];

    _setStatus(`✨ Enriching ${allBatches.length} batch${allBatches.length > 1 ? "es" : ""} in parallel — 0/${total} rows done`);

    // Fire all batches simultaneously — time = max(slowest batch) not sum of all
    await Promise.all(allBatches.map(async ({ domain, batch }) => {
      try {
        const r = await api.post("/api/bdcq/enrich", {
          domain, questionCol,
          rows:     batch.map(e => e.row),
          country:  enrichCountries,
          industry: enrichIndustry
        });
        if (r.ok && Array.isArray(r.enrichments)) {
          r.enrichments.forEach((e, i) => {
            if (!batch[i]) return;
            const globalIdx = batch[i].idx;
            window._bdcqEditorState.rows[globalIdx]["SAP Standard Value"]     = e.standardValue || "";
            window._bdcqEditorState.rows[globalIdx]["Watch Out / Constraint"] = e.watchOut      || "";
            if (e.countryRegion) window._bdcqEditorState.rows[globalIdx]["Country / Region"] = e.countryRegion;
          });
        } else {
          errors.push(`${domain}: ${r.error || "unknown error"}`);
        }
      } catch (err) {
        errors.push(`${domain}: ${err.message}`);
      }
      done += batch.length;
      _setStatus(`✨ Enriching — ${done}/${total} rows done`);
    }));

    clearInterval(_timerTick);

    if (errors.length) {
      if (progressEl) progressEl.innerHTML =
        `❌ Enrichment errors:<br>${errors.map(escHtml).join("<br>")}
         <br><span style="font-size:11px;color:var(--text-muted)">
         Check that Claude CLI is installed and START.bat added it to PATH.</span>`;
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = "✨ Re-Enrich"; }
      return;
    }

    // Re-render cards with enriched data
    renderEditorCards();

    if (progressEl) {
      progressEl.innerHTML = `✅ Enrichment complete — ${total} rows enriched in ${_elapsed()}. Review AI values before exporting.`;
      setTimeout(() => { if (progressEl) progressEl.style.display = "none"; }, 5000);
    }
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = "✨ Re-Enrich"; }

    /* EMAIL NOTIFY — commented out
    if (total > 0) {
      try {
        const nr = await fetch("http://127.0.0.1:8323/notify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            type: "bdcq",
            detail: `${total} question${total !== 1 ? "s" : ""} enriched with SAP Standard Values and Watch-Out points.`
          })
        });
        const nd = await nr.json();
        if (nd.ok && window._fulcrumShowToast) window._fulcrumShowToast(`📧 Email sent to ${nd.sentTo.split("@")[0]}`);
      } catch { }
    }
    */
  };

  // ── Export Excel from editor state ──────────────────────────────────────────
  window.bdcqExportExcel = async () => {
    const { rows } = window._bdcqEditorState;
    if (!rows.length) return;

    // Build columns: Module + Section always first, then whatever the user has
    // ticked in the column list (standard + custom). Read directly from DOM so
    // custom columns added before OR after "Generate & Edit" are always included.
    const domChecked = [...document.querySelectorAll(".bdcq-col-check")]
      .filter(cb => cb.checked).map(cb => cb.value);
    const seen = new Set(["module", "section"]);
    const columns = [
      "Module", "Section",
      ...domChecked.filter(v => {
        const lc = v.toLowerCase();
        if (seen.has(lc)) return false;
        seen.add(lc);
        return true;
      })
    ];

    // Auto-generate filename if not filled in the configure tab
    let filename = (document.getElementById("bdcq-filename")?.value || "").trim();
    if (!filename) {
      const client = (document.getElementById("bdcq-client")?.value || "").trim().replace(/\s+/g, "_") || "Client";
      filename = `${client}_BDCQ_${new Date().toISOString().slice(0,10)}`;
    }

    // Show spinner on the export button (visible in editor tab)
    const exportBtn = document.querySelector("[onclick='bdcqExportExcel()']");
    if (exportBtn) { exportBtn.disabled = true; exportBtn.textContent = "Exporting…"; }

    try {
      const clientName   = (document.getElementById("bdcq-client")?.value   || "").trim();
      const overviewText      = (document.getElementById("bdcq-overview")?.value || "").trim();
      const clientLogo        = window._bdcqLogos?.client  || null;
      const partnerLogo       = window._bdcqLogos?.partner || null;
      const selectedCountries = [...document.querySelectorAll(".bdcq-enrich-country:checked")].map(cb => cb.value);
      const r = await api.post("/api/bdcq/export-editor", { filename, rows, columns, clientName, overviewText, clientLogo, partnerLogo, selectedCountries });
      if (r.ok) {
        // Show download toast — visible regardless of which tab is active
        const toast = document.createElement("div");
        toast.style.cssText = `
          position:fixed;bottom:24px;right:24px;z-index:99999;
          background:#0f172a;color:#f8fafc;border-radius:10px;
          padding:14px 18px;font-family:Segoe UI,Arial,sans-serif;
          font-size:13px;box-shadow:0 8px 32px rgba(0,0,0,.35);
          display:flex;align-items:center;gap:12px;max-width:360px;
        `;
        toast.innerHTML = `
          <span style="font-size:18px">✅</span>
          <div>
            <div style="font-weight:600;margin-bottom:4px">Excel ready — ${r.rowCount} rows</div>
            <a href="/api/output/download/${encodeURIComponent(r.filename)}"
               style="color:#818cf8;font-size:12px;text-decoration:underline" download>
              ⬇ Download ${r.filename}
            </a>
          </div>
          <button onclick="this.parentElement.remove()" style="
            background:transparent;border:none;color:#64748b;
            font-size:16px;cursor:pointer;margin-left:auto;padding:2px">✕</button>
        `;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 20000);
      } else {
        alert(`Export error: ${r.error || "unknown"}`);
      }
    } catch (err) {
      alert(`Export error: ${err.message}`);
    } finally {
      if (exportBtn) { exportBtn.disabled = false; exportBtn.textContent = "Export Excel ↓"; }
    }
  };

  // ── Quick Export (bypass editor) ────────────────────────────────────────────
  window.bdcqGenerate = async () => {
    const resultEl  = document.getElementById("bdcq-result");
    const filename    = (document.getElementById("bdcq-filename")?.value || "").trim();
    const clientName  = (document.getElementById("bdcq-client")?.value || "").trim();
    const scopeRaw    = (document.getElementById("bdcq-scope")?.value || "").trim();
    const overviewText= (document.getElementById("bdcq-overview")?.value || "").trim();
    const clientLogo  = window._bdcqLogos?.client  || null;
    const partnerLogo = window._bdcqLogos?.partner || null;

    if (!filename) {
      if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Output File Name is required.</div>`;
      return;
    }

    // Parse scope IDs — split on comma, semicolon, or whitespace
    const scopeIds = scopeRaw
      ? scopeRaw.split(/[,;\s]+/).map(s => s.trim()).filter(Boolean)
      : [];

    // Collect selected domains
    const selectedDomains = [...document.querySelectorAll(".bdcq-domain-check")]
      .filter(cb => cb.checked)
      .map(cb => cb.value);

    // Collect selected columns
    const selectedColumns = [...document.querySelectorAll(".bdcq-col-check")]
      .filter(cb => cb.checked)
      .map(cb => cb.value);

    // Collect selected countries
    const countries = [...document.querySelectorAll(".bdcq-country-check")]
      .filter(cb => cb.checked)
      .map(cb => cb.value);

    if (!selectedDomains.length) {
      if (resultEl) resultEl.innerHTML = `
        <div class="alert" style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);color:#fca5a5;border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.6">
          <strong style="font-size:14px">Select at least one domain to continue.</strong><br>
          Open <strong>BDCQ Files → Select domains</strong> above and tick the domains in your project scope.
        </div>`;
      document.getElementById("bdcq-domain-label")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    if (resultEl) resultEl.innerHTML = `<div class="alert alert-info">Generating Excel…</div>`;

    try {
      const r = await api.post("/api/bdcq/generate", {
        filename,
        scopeIds,
        selectedDomains,
        selectedColumns,
        countries,
        clientName,
        projectName: "",
        overviewText,
        clientLogo,
        partnerLogo
      });

      if (r.ok) {
        if (resultEl) resultEl.innerHTML = `
          <div class="alert alert-success">
            Generated &mdash; ${r.rowCount.toLocaleString()} rows &nbsp;
            <a href="/api/output/download/${encodeURIComponent(r.filename)}"
               style="color:var(--accent);font-weight:600;text-decoration:underline"
               download>Download ${r.filename}</a>
          </div>`;
      } else {
        if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Error: ${r.error || "unknown error"}</div>`;
      }
    } catch (err) {
      if (resultEl) resultEl.innerHTML = `<div class="alert alert-warning">Error: ${err.message}</div>`;
    }
  };
}
