// SAP Deck Agent v28 — content_iframe.js
// Runs inside pr.alm.me.sap.com — same origin as SAP API

// ── Get scenario GUID from page network calls ─────────────────────────────────
function getScenarioGuid() {
  const entries = performance.getEntriesByType("resource");
  for (const e of entries) {
    const m = e.name.match(/SolutionScenarioTranslation\(ID=([a-f0-9-]{36})/);
    if (m) return m[1];
  }
  return null;
}

function getVersion() {
  const m = window.location.href.match(/\/(\d{4})\//);
  return m ? m[1] : "latest";
}

// ── Fetch a single process detail (description, steps, benefits) ─────────────
async function fetchProcessDetail(guid, externalId) {
  const url = `/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenarioTranslation(ID=${guid},lanCode='en-US')/solutionProcessTranslation?$filter=country_ID%20eq%20'DE'%20and%20externalId%20eq%20'${externalId}'&$top=1`;
  const res  = await fetch(url);
  const data = await res.json();
  return data.value?.[0] || null;
}

// ── Parse SAP process description HTML → structured L4 sections ──────────────
function parseDescriptionStructured(html) {
  const empty = { overview: "", keyProcessSteps: [], businessBenefits: [], scope: "", keyFeatures: [], fullText: "" };
  if (!html) return empty;
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  const fullText = (tmp.textContent || tmp.innerText || "").replace(/\s+/g, " ").trim();
  const r = { ...empty, fullText };
  let section = "overview";

  function classify(t) {
    t = t.toLowerCase();
    if (/process step|key step|process flow|activities/.test(t)) return "steps";
    if (/benefit/.test(t))                                        return "benefits";
    if (/scope|key feature|function|capabilit/.test(t))          return "features";
    if (/overview|description|summary/.test(t))                   return "overview";
    return section;
  }

  tmp.querySelectorAll("h1,h2,h3,h4,p,li").forEach(el => {
    const tag  = el.tagName.toLowerCase();
    const text = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    if (/^h[1-4]$/.test(tag)) { section = classify(text); return; }
    if (tag === "li") {
      if (section === "steps")    r.keyProcessSteps.push(text);
      else if (section === "benefits") r.businessBenefits.push(text);
      else if (section === "features") r.keyFeatures.push(text);
    } else if (tag === "p") {
      if (el.closest("li")) return; // skip paragraphs inside list items
      if (section === "overview") r.overview = (r.overview + " " + text).trim();
      else if (section === "features") r.scope = (r.scope + " " + text).trim();
    }
  });
  return r;
}

// ── Derive L1 Business Domain from L2 Business Function name ─────────────────
function deriveL1(l2) {
  const t = (l2 || "").toLowerCase();
  if (/finance|accounting|treasury|tax|asset account|controlling|revenue|ledger|payment|invoice|receivable|payable|cash/.test(t))
    return "Finance & Controlling";
  if (/procurement|purchasing|sourcing|supplier|vendor|ariba|spend/.test(t))
    return "Sourcing & Procurement";
  if (/manufactur|production|quality|plant|shop floor|mrp|capacity/.test(t))
    return "Manufacturing";
  if (/supply chain|warehouse|inventory|logistics|transport|shipping|import|export/.test(t))
    return "Supply Chain";
  if (/sales|customer order|pricing|billing|crm|lead|opportunity|trade/.test(t))
    return "Sales & Distribution";
  if (/service|field service|customer service|after.?sales/.test(t))
    return "Customer Service";
  if (/human resource|hr |payroll|talent|workforce|employee|benefit|time management|personnel/.test(t))
    return "Human Resources";
  if (/project|professional service|ps |time.?expense/.test(t))
    return "Professional Services";
  if (/real estate|facility|lease/.test(t))
    return "Real Estate";
  if (/r&d|engineering|product develop|innovat/.test(t))
    return "R&D & Engineering";
  if (/retail/.test(t))
    return "Retail";
  if (/public sector|government/.test(t))
    return "Public Sector";
  if (/asset management|maintenance|pm |equipment/.test(t))
    return "Asset Management";
  return "Cross-Functional";
}

// ── Full L1–L4 Catalog Fetch ──────────────────────────────────────────────────
// Step 1: bulk list (all processes, basic fields)
// Step 2: if description not in bulk response → parallel batch-fetch details
// Step 3: parse descriptions into L4 sections
// Step 4: build hierarchy + save
async function fetchScopeCatalog() {
  const guid    = getScenarioGuid();
  const version = getVersion();

  if (!guid) {
    // Try resolving GUID from the API directly
    const r = await fetch(`/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenario?$filter=stableId%20eq%20'EARL_SolS-013'%20and%20targetRelease%20eq%20'${version}'&$top=1`);
    const d = await r.json();
    const resolvedGuid = d.value?.[0]?.ID;
    if (!resolvedGuid) throw new Error("Cannot find SAP scenario ID. Navigate to me.sap.com/processnavigator/SolS/EARL_SolS-013/ and try again.");
    return fetchScopeCatalogWithGuid(resolvedGuid, version);
  }
  return fetchScopeCatalogWithGuid(guid, version);
}

async function fetchScopeCatalogWithGuid(guid, version) {
  await chrome.storage.local.set({ _catalogProgress: JSON.stringify({ step: "Fetching process list…", done: 0, total: 0 }) });

  // ── Step 1: Bulk list ───────────────────────────────────────────────────
  const listRes  = await fetch(
    `/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenarioTranslation(ID=${guid},lanCode='en-US')/solutionProcessTranslation?$count=true&$orderby=name&$filter=country_ID%20eq%20'DE'&$skip=0&$top=700`
  );
  const listData = await listRes.json();
  const rawList  = listData.value || [];
  const total    = rawList.length;

  // ── Step 2: Fetch descriptions if bulk response doesn't include them ─────
  const bulkHasDesc = rawList.length > 0 && (rawList[0].description || "").length > 20;

  if (!bulkHasDesc) {
    const BATCH = 25; // parallel requests per round
    for (let i = 0; i < total; i += BATCH) {
      const batch   = rawList.slice(i, i + BATCH);
      const results = await Promise.allSettled(
        batch.map(p => fetchProcessDetail(guid, p.externalId || ""))
      );
      results.forEach((r, idx) => {
        if (r.status === "fulfilled" && r.value) {
          Object.assign(rawList[i + idx], r.value);
        }
      });
      const done = Math.min(i + BATCH, total);
      await chrome.storage.local.set({ _catalogProgress: JSON.stringify({ step: "Fetching details…", done, total }) });
    }
  } else {
    await chrome.storage.local.set({ _catalogProgress: JSON.stringify({ step: "Parsing descriptions…", done: total, total }) });
  }

  // ── Step 3a: Build KDD catalog (OLD format — scope-catalog.json) ────────
  // Preserves { id, lob, name, description } exactly matching KDD agent schema.
  // Raw description HTML is kept intact so the KDD agent can parse it itself.
  const kddProcesses = rawList.map(p => {
    const rawName = p.name || "";
    const idMatch = rawName.match(/\(([A-Z0-9]{2,5})\)\s*$/);
    const id      = p.externalId || (idMatch ? idMatch[1] : "");
    const name    = rawName.replace(/\s*\([A-Z0-9]{2,5}\)\s*$/, "").trim();
    return {
      description: p.description || "",                 // raw HTML — KDD agent reads this
      id,
      lob:  p.businessProcessGroupName || "Other",
      name,
    };
  }).filter(p => p.id);

  const kddCatalog = {
    type:        "scope_catalog",                       // KDD agent checks this type
    version,
    country:     "DE",
    extractedAt: new Date().toISOString(),
    total:       kddProcesses.length,
    processes:   kddProcesses,
  };

  // ── Step 3b: Build F2S catalog (NEW format — scope-catalog-f2s.json) ────
  // Enriched L1–L4 hierarchy with parsed Overview, Benefits, Features and
  // L4 process steps — feeds directly into the Fit-to-Standard agent.
  const f2sProcesses = rawList.map(p => {
    const rawName = p.name || "";
    const idMatch = rawName.match(/\(([A-Z0-9]{2,5})\)\s*$/);
    const id      = p.externalId || (idMatch ? idMatch[1] : "");
    const name    = rawName.replace(/\s*\([A-Z0-9]{2,5}\)\s*$/, "").trim();
    const l2      = p.businessProcessGroupName || "Other";
    const l1      = deriveL1(l2);
    const desc    = parseDescriptionStructured(p.description || "");
    return {
      id,
      name,
      l1,                           // Business Domain (derived)
      l2,                           // Business Function (= businessProcessGroupName)
      l3:               name,       // Scope Item (= process name)
      l4:               desc.keyProcessSteps,   // Process Steps extracted from description
      overview:         desc.overview,
      businessBenefits: desc.businessBenefits,
      keyFeatures:      desc.keyFeatures,
      scope:            desc.scope,
      fullDescription:  desc.fullText,
      version:          p.solutionScenarioTargetRelease || version,
    };
  }).filter(p => p.id);

  // ── Step 4: Build L1→L2→[scopeIds] hierarchy for F2S catalog ───────────
  const hierarchy = {};
  f2sProcesses.forEach(p => {
    if (!hierarchy[p.l1])        hierarchy[p.l1] = {};
    if (!hierarchy[p.l1][p.l2]) hierarchy[p.l1][p.l2] = [];
    hierarchy[p.l1][p.l2].push(p.id);
  });

  const f2sCatalog = {
    type:            "f2s_process_catalog",
    schemaVersion:   "2.0",
    sapVersion:      version,
    version,                                          // alias so popup can read cat.version
    extractedAt:     new Date().toISOString(),
    country:         "DE",
    cloudEdition:    "S/4HANA Cloud Public Edition",
    total:           f2sProcesses.length,
    hasDescriptions: rawList.some(p => (p.description || "").length > 20),
    hierarchy,
    processes:       f2sProcesses,
  };

  // ── Step 5: Persist both catalogs ───────────────────────────────────────
  await chrome.storage.local.set({
    sapScopeCatalog:    JSON.stringify(kddCatalog),   // KDD agent → scope-catalog.json
    sapScopeCatalogF2S: JSON.stringify(f2sCatalog),   // F2S agent → scope-catalog-f2s.json
    _catalogProgress:   JSON.stringify({ step: "Done", done: total, total }),
  });

  return f2sCatalog;  // popup shows L1/L2/L4 stats from the F2S catalog
}

// ── Message handler ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, respond) => {

  // ── fetchFileBytes — used by popup folder-save path ─────────────────────────
  // Runs in the pr.alm.me.sap.com context so same-origin SAP file URLs work.
  if (msg.action === "fetchFileBytes") {
    (async () => {
      try {
        // Security: only fetch from SAP domains
        const host = new URL(msg.url).hostname;
        if (host !== 'sap.com' && !host.endsWith('.sap.com')) {
          respond({ ok: false, error: 'Blocked: not a SAP domain' });
          return;
        }
        const resp = await fetch(msg.url, { credentials: 'include' });
        if (!resp.ok) throw new Error("HTTP " + resp.status + " " + resp.statusText);
        const buf   = await resp.arrayBuffer();
        const bytes = new Uint8Array(buf);
        const CHUNK = 8192;
        let b64 = "";
        for (let i = 0; i < bytes.length; i += CHUNK) {
          b64 += btoa(String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + CHUNK, bytes.length))));
        }
        respond({ ok: true, base64: b64, size: buf.byteLength });
      } catch (e) {
        respond({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  if (msg.action === "ping_iframe") {
    respond({ ok: true, chars: (document.body?.innerText||"").length, url: window.location.href });
    return true;
  }

  if (msg.action === "fetch_process_data") {
    (async () => {
      try {
        // Get scenario GUID — try from network calls first
        let guid = getScenarioGuid();

        // If not found, fetch it via API using scenario stable ID
        if (!guid) {
          const version = getVersion();
          const scenRes = await fetch(
            `/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenario?$filter=stableId%20eq%20'EARL_SolS-013'%20and%20targetRelease%20eq%20'${version}'&$top=1`
          );
          const scenData = await scenRes.json();
          guid = scenData.value?.[0]?.ID;
        }

        if (!guid) throw new Error("Could not find SAP scenario. Open me.sap.com/processnavigator first.");

        const scopeIds = msg.scopeIds || [];
        const results = await Promise.all(scopeIds.map(id => fetchProcessContent(guid, id)));
        const extractedList = results.map(p => apiToExtractedData(p));

        // Save to chrome.storage — artifact will read this directly
        await chrome.storage.local.set({
          sapProcessDataList: JSON.stringify({
            type:        "process_data_list",
            fetchedAt:   new Date().toISOString(),
            count:       extractedList.length,
            processes:   extractedList,
          })
        });

        respond({ ok: true, count: extractedList.length });

      } catch(e) {
        respond({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  if (msg.action === "fetch_scope_catalog") {
    (async () => {
      try {
        const catalog = await fetchScopeCatalog();
        respond({ ok: true, catalog });
      } catch(e) {
        respond({ ok: false, error: e.message });
      }
    })();
    return true;
  }

});

