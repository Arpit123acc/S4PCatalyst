// SAP Deck Agent v27 — popup.js
// v27 changes:
//   - Roadmap Viewer mode: scan Phase Accelerator pages for BDCQ xlsx files
//   - Download found BDCQ files directly to cartridge folder (bdcq/ subfolder)
//   - show() updated to include roadmapMode

let activeTabId = null;

// File System Access API handle — shared between Process Navigator and Roadmap modes
let cartridgeDirHandle = null;

// BDCQ files discovered on the current roadmap viewer page
let bdcqFiles = [];

// FileSystemDirectoryHandle chosen by the user for BDCQ saves
let bdcqDirHandle = null;

// ── Shared helpers ────────────────────────────────────────────────────────────

// Verify (and re-request if needed) readwrite permission on a directory handle.
// Returns true if permission is granted, false if the user denies or the handle
// is irrecoverably stale. Callers should null the handle and re-pick on false.
async function verifyPermission(handle) {
  if (!handle) return false;
  try {
    const opts = { mode: "readwrite" };
    if (await handle.queryPermission(opts) === "granted") return true;
    return (await handle.requestPermission(opts)) === "granted";
  } catch {
    return false;
  }
}

function expectedRelease() {
  const now = new Date();
  const yy  = String(now.getFullYear()).slice(-2);
  return (now.getMonth() + 1) >= 8 ? `${yy}08` : `${yy}02`;
}

function show(id) {
  ["loading", "notSap", "mainMode", "roadmapMode"].forEach(i => {
    document.getElementById(i).style.display = i === id ? "block" : "none";
  });
}

function base64ToArrayBuffer(b64) {
  const binary = atob(b64);
  const bytes  = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

// Wait for SAP's SSO tab (accounts.sap.com/saml2…) to finish and close.
// When the SAP session needs re-auth, SAP opens this tab to refresh the SAML
// assertion; until it settles, file fetches return an auth-redirect HTML page.
// Returns true once the SSO tab has appeared and then gone (session now fresh),
// or false if no SSO was in progress. This is what a manual browser refresh does.
async function waitForSsoTabToClose(maxMs = 30000) {
  const start = Date.now();
  let sawSso = false;
  const hasSsoTab = async () => {
    const tabs = await chrome.tabs.query({});
    return tabs.some(t => t.url && /accounts\.sap\.com\/saml2/i.test(t.url));
  };
  while (Date.now() - start < maxMs) {
    if (await hasSsoTab()) {
      sawSso = true;
    } else if (sawSso) {
      // Was mid-SSO, now gone → give cookies a beat to settle, then done.
      await new Promise(r => setTimeout(r, 800));
      return true;
    } else if (Date.now() - start > 3000) {
      return false; // never appeared within 3s → no SSO in progress
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return sawSso; // timed out — report whether SSO was seen
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    activeTabId = tab.id;

    if (!tab.url || !tab.url.includes("me.sap.com")) {
      show("notSap"); return;
    }

    // ── Route by page type ─────────────────────────────────────────────────
    if (tab.url.includes("/roadmapviewer/")) {
      show("roadmapMode");
      initRoadmapMode();
      return;
    }

    // Default: Process Navigator mode
    show("mainMode");

    const url     = tab.url;
    const pidM    = url.match(/\/SolP\/([A-Z0-9]+)/i);
    const isScen  = url.includes("/SolS/") && !url.includes("/SolP/");
    const pageEl  = document.getElementById("pageType");
    if (pidM)       pageEl.textContent = "Process: " + pidM[1].toUpperCase();
    else if (isScen) pageEl.textContent = "Solution Scenario";
    else             pageEl.textContent = "SAP for Me";

    // Load stored catalog — show status + version warning
    const stored = await chrome.storage.local.get(["sapScopeCatalog"]);
    if (stored.sapScopeCatalog) {
      try {
        const cat     = JSON.parse(stored.sapScopeCatalog);
        const ver     = cat.version || "?";
        const exp     = expectedRelease();
        const outdated = parseInt(ver, 10) < parseInt(exp, 10);
        document.getElementById("catalogStatus").textContent =
          "Loaded: v" + ver + " · " + (cat.processes && cat.processes.length || 0) + " processes";
        if (outdated) {
          const warn = document.getElementById("catalogWarn");
          warn.textContent = "⚠️  Catalog is v" + ver + " — SAP " + exp + " is available. Reload recommended.";
          warn.style.display = "block";
        }
      } catch(e) {}
    }

  } catch(e) { show("notSap"); }
});

// ── Load Full Process Catalog (L1–L4) ────────────────────────────────────────
document.getElementById("btnLoadCatalog").addEventListener("click", async () => {
  const btn  = document.getElementById("btnLoadCatalog");
  const prog = document.getElementById("catalogProgArea");
  const res  = document.getElementById("catalogResult");
  const err  = document.getElementById("catalogErr");
  const pb   = document.getElementById("catalogPb");
  const pct  = document.getElementById("catalogPct");
  const warn = document.getElementById("catalogWarn");

  btn.disabled = true;
  prog.style.display = "block"; res.style.display = "none";
  err.style.display = "none";   warn.style.display = "none";
  pb.style.width = "5%"; pct.textContent = "Connecting to SAP API…";

  // Poll chrome.storage for progress updates written by the content script
  const pollTimer = setInterval(async () => {
    try {
      const s = await chrome.storage.local.get(['_catalogProgress']);
      if (!s._catalogProgress) return;
      const p = JSON.parse(s._catalogProgress);
      if (p.total > 0) {
        const pct2 = Math.round(10 + (p.done / p.total) * 85);
        pb.style.width = pct2 + "%";
        pct.textContent = p.step + (p.total > 0 ? ` ${p.done}/${p.total}` : "");
      } else {
        pct.textContent = p.step || "Loading…";
      }
    } catch(e) {}
  }, 600);

  try {
    const response = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { action: "fetchScopeCatalog", tabId: activeTabId },
        result => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else resolve(result);
        }
      );
      // Allow up to 3 min — description batch-fetch for 657 processes takes time
      setTimeout(() => reject(new Error("Timeout — try again on the Process Navigator page")), 180000);
    });

    clearInterval(pollTimer);
    if (!response || !response.ok) throw new Error(response?.error || "Failed");

    pb.style.width = "100%"; pct.textContent = "✅ Done";

    // Counts are returned directly from background — no catalog object needed
    const count   = response.count   || 0;
    const ver     = response.version || "?";
    const l1Count = response.l1Count || 0;
    const l2Count = response.l2Count || 0;
    const l4Count = response.l4Count || 0;

    document.getElementById("rCatalogCount").textContent   = count + " processes";
    document.getElementById("rCatalogL1").textContent      = l1Count + " domains";
    document.getElementById("rCatalogL2").textContent      = l2Count + " functions";
    document.getElementById("rCatalogL4").textContent      = l4Count.toLocaleString() + " steps";
    document.getElementById("rCatalogVersion").textContent = ver;
    document.getElementById("catalogStatus").textContent   =
      "Loaded: v" + ver + " · " + count + " processes · " + l4Count.toLocaleString() + " L4 steps";
    res.style.display = "block";
    btn.textContent = "⚡ Reload"; btn.disabled = false;

  } catch(e) {
    clearInterval(pollTimer);
    err.textContent = e.message;
    err.style.display = "block";
    btn.textContent = "⚡ Load All Processes + Details"; btn.disabled = false;
  }
});

// ── Save to Cartridge + Generate Agent Files (merged handler) ─────────────────
document.getElementById("btnSaveCartridge").addEventListener("click", async () => {
  const btn      = document.getElementById("btnSaveCartridge");
  const saveErr  = document.getElementById("saveErr");
  const saveRes  = document.getElementById("saveResult");
  const saveTitle = document.getElementById("saveResultTitle");
  const genProg  = document.getElementById("genProgArea");
  const genPb    = document.getElementById("genPb");
  const genPct   = document.getElementById("genPct");
  const genErr   = document.getElementById("genErr");
  const genRes   = document.getElementById("genResult");
  const genTitle = document.getElementById("genResultTitle");
  const genRows  = document.getElementById("genResultRows");

  btn.disabled = true;
  saveErr.style.display = "none"; saveRes.style.display = "none";
  genProg.style.display = "none"; genErr.style.display = "none"; genRes.style.display = "none";

  try {
    // ── Step 1: Pick / reuse folder ──────────────────────────────────────────
    if (cartridgeDirHandle && !(await verifyPermission(cartridgeDirHandle))) {
      cartridgeDirHandle = null; // permission lapsed — re-pick below
    }
    if (!cartridgeDirHandle) {
      cartridgeDirHandle = await window.showDirectoryPicker({
        id: "explore-accelerator", mode: "readwrite", startIn: "desktop"
      });
    }

    // ── Step 2: Save catalog JSON files ──────────────────────────────────────
    const stored     = await chrome.storage.local.get(["sapScopeCatalog", "sapScopeCatalogF2S", "sapProcessDataList"]);
    const savedFiles = [];

    if (stored.sapScopeCatalog) {
      const fh = await cartridgeDirHandle.getFileHandle("scope-catalog.json", { create: true });
      const wr = await fh.createWritable(); await wr.write(stored.sapScopeCatalog); await wr.close();
      savedFiles.push("scope-catalog.json");
    }
    if (stored.sapScopeCatalogF2S) {
      const fh = await cartridgeDirHandle.getFileHandle("scope-catalog-f2s.json", { create: true });
      const wr = await fh.createWritable(); await wr.write(stored.sapScopeCatalogF2S); await wr.close();
      savedFiles.push("scope-catalog-f2s.json");
    }
    if (stored.sapProcessDataList) {
      const fh = await cartridgeDirHandle.getFileHandle("process-data.json", { create: true });
      const wr = await fh.createWritable(); await wr.write(stored.sapProcessDataList); await wr.close();
      savedFiles.push("process-data.json");
    }
    if (savedFiles.length === 0) throw new Error("Nothing to save — load the catalog first.");

    saveTitle.textContent = "✓ Saved: " + savedFiles.join(" + ");
    saveRes.style.display = "block";

    // ── Step 3: Generate agent files (runs immediately after save) ────────────
    if (!stored.sapScopeCatalogF2S) throw new Error("No F2S catalog — load processes first.");

    genProg.style.display = "block";
    genPb.style.width = "3%"; genPct.textContent = "Reading catalogs…";

    const f2s      = JSON.parse(stored.sapScopeCatalogF2S);
    const kddRaw   = stored.sapScopeCatalog ? JSON.parse(stored.sapScopeCatalog) : null;
    const f2sProcs = f2s.processes || [];
    const ver      = f2s.sapVersion || f2s.version || "?";
    const saved    = [];

    const byL1 = {};
    f2sProcs.forEach(p => {
      if (!byL1[p.l1])        byL1[p.l1] = {};
      if (!byL1[p.l1][p.l2]) byL1[p.l1][p.l2] = [];
      byL1[p.l1][p.l2].push(p);
    });
    const kddByLob = {};
    (kddRaw ? kddRaw.processes || [] : []).forEach(p => {
      const lob = p.lob || "Other";
      if (!kddByLob[lob]) kddByLob[lob] = [];
      kddByLob[lob].push(p);
    });

    // ── Create kdd/ and f2s/ subfolders ────────────────────────────────────────
    genPb.style.width = "6%"; genPct.textContent = "Creating kdd/ and f2s/ folders…";
    const kddDir = await cartridgeDirHandle.getDirectoryHandle('kdd', { create: true });
    const f2sDir = await cartridgeDirHandle.getDirectoryHandle('f2s', { create: true });

    genPb.style.width = "8%"; genPct.textContent = "KDD: writing kdd-master.md…";
    await agentWriteFile(kddDir, "kdd-master.md", buildKddMasterMd(kddByLob, ver));
    saved.push("kdd/kdd-master.md");

    const lobs = Object.keys(kddByLob).sort();
    for (let i = 0; i < lobs.length; i++) {
      const lob  = lobs[i];
      const slug = agentSlug("kdd-lob-", lob, 38);
      genPb.style.width = Math.round(8 + (i / lobs.length) * 37) + "%";
      genPct.textContent = `KDD: ${slug}.md (${i + 1}/${lobs.length})`;
      await agentWriteFile(kddDir, slug + ".md", buildKddLobMd(lob, kddByLob[lob], ver));
      saved.push("kdd/" + slug + ".md");
    }

    genPb.style.width = "50%"; genPct.textContent = "F2S: writing f2s-master.md…";
    await agentWriteFile(f2sDir, "f2s-master.md", buildF2sMasterMd(byL1, ver, f2sProcs.length));
    saved.push("f2s/f2s-master.md");

    genPb.style.width = "55%"; genPct.textContent = "F2S: writing f2s-index.json…";
    const index = f2sProcs.map(p => ({
      id: p.id, name: p.name, l1: p.l1, l2: p.l2,
      hint: ((p.overview || "").trim() || (p.l4 && p.l4[0]) || "").substring(0, 130)
    }));
    await agentWriteFile(f2sDir, "f2s-index.json", JSON.stringify({
      _usage: "KDD: ignore. F2S: scan this to identify scope item IDs (L4) by l1/l2/name/hint, then load f2s-{domain}.md for L5 process steps and full detail.",
      generated: new Date().toISOString(), sapVersion: ver,
      total: index.length, hierarchy: "L1>L2>L4(scope item)>L5(steps)", processes: index
    }, null, 2));
    saved.push("f2s/f2s-index.json");

    const domains = Object.keys(byL1).sort();
    for (let i = 0; i < domains.length; i++) {
      const l1   = domains[i];
      const slug = agentSlug("f2s-", l1, 50);
      genPb.style.width = Math.round(60 + (i / domains.length) * 37) + "%";
      genPct.textContent = `F2S: ${slug}.md (${i + 1}/${domains.length})`;
      await agentWriteFile(f2sDir, slug + ".md", buildF2sDomainMd(l1, byL1[l1], ver));
      saved.push("f2s/" + slug + ".md");
    }

    genPb.style.width = "100%"; genPct.textContent = "✅ Done";
    genTitle.textContent = `✓ ${saved.length} agent files saved`;
    genRows.innerHTML = saved.map(f =>
      `<div class="rrow"><span class="rk" style="color:#00C875">📄</span>` +
      `<span class="rv" style="color:#ccc;font-size:9px">${escHtml(f)}</span></div>`
    ).join("");
    genRes.style.display = "block";

  } catch(e) {
    if (e.name === "AbortError") { btn.disabled = false; genProg.style.display = "none"; return; }
    cartridgeDirHandle = null;
    const isStale = e.message && (e.message.includes("state had changed") || e.message.includes("state cached") || e.name === "InvalidStateError");
    // Show error in whichever section is relevant
    const errEl = genProg.style.display === "block" ? genErr : saveErr;
    errEl.textContent = isStale
      ? "Folder access expired — click Save & Generate again to re-select your folder."
      : e.message;
    errEl.style.display = "block";
  }

  genProg.style.display = "none";
  btn.disabled = false;
});

// ════════════════════════════════════════════════════════════════════════════
// ── ROADMAP VIEWER MODE ──────────────────────────────────────────────────────
// ════════════════════════════════════════════════════════════════════════════

function initRoadmapMode() {
  document.getElementById("btnScan").addEventListener("click", handleScan);
  document.getElementById("btnDownloadBDCQ").addEventListener("click", handleDownloadBDCQ);
  document.getElementById("btnChangeFolder").addEventListener("click", async () => {
    bdcqDirHandle = null;
    await handleDownloadBDCQ();
  });
}

// ── Show chosen folder name in UI ─────────────────────────────────────────────
function showChosenFolder(name) {
  const row = document.getElementById("chosenFolderRow");
  document.getElementById("chosenFolderName").textContent = "📂 " + name;
  row.style.display = "flex";
}

// ── Scan page for BDCQ files ──────────────────────────────────────────────────

async function handleScan() {
  const btn      = document.getElementById("btnScan");
  const prog     = document.getElementById("scanProgArea");
  const result   = document.getElementById("scanResult");
  const err      = document.getElementById("scanErr");
  const pb       = document.getElementById("scanPb");
  const pct      = document.getElementById("scanPct");
  const noBdcq   = document.getElementById("noBdcqWarn");
  const dlBtn    = document.getElementById("btnDownloadBDCQ");

  btn.disabled = true;
  prog.style.display = "block";
  result.style.display = "none";
  err.style.display = "none";
  noBdcq.style.display = "none";
  pb.style.width = "15%"; pct.textContent = "Scanning DOM + APIs…";

  try {
    // ── Pre-check: warn if an SAP SSO tab is currently open ──────────────────
    // When SAP session expires, Chrome opens accounts.sap.com/saml2/... in a tab.
    // Scanning while SSO is still in progress will silently fail (no files captured).
    const allTabs = await chrome.tabs.query({});
    const ssoTab  = allTabs.find(t => t.url && t.url.includes('accounts.sap.com/saml2'));
    if (ssoTab) {
      noBdcq.textContent =
        '⚠ SAP SSO is running in another tab (accounts.sap.com/saml2/…). ' +
        'Wait for it to finish (tab closes automatically), then click Scan Again.';
      noBdcq.style.display = 'block';
      pb.style.background  = '#FFAB00';
      pct.textContent      = 'Waiting for SAP SSO…';
      btn.disabled = false;
      return;
    }

    // ── Phase 1: initial DOM + API scan ──────────────────────────────────────
    // Route through background.js which uses executeScript — no pre-injection needed
    await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { action: "scanRoadmapFiles", tabId: activeTabId },
        res => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else resolve(res);
        }
      );
      setTimeout(() => reject(new Error("Timeout — reload the page and try again")), 60000);
    });

    // ── Phase 2: auto-scroll all frames to trigger SAP's lazy URL generation ─
    // SAP's roadmap viewer generates signed download URLs only when a file row
    // becomes visible. Scrolling programmatically through every scrollable
    // container forces those network requests, so the background webRequest
    // listener can capture all URLs — no manual scrolling needed by the user.
    pb.style.width = "40%"; pct.textContent = "Auto-loading file URLs…";
    await new Promise(resolve => {
      chrome.scripting.executeScript({
        target: { tabId: activeTabId, allFrames: true },
        func: async () => {
          // Find scrollable containers sorted by height (file list panels first)
          const scrollEls = [...document.querySelectorAll('*')].filter(el => {
            try {
              const s = window.getComputedStyle(el);
              return /auto|scroll/.test(s.overflow + s.overflowY)
                  && el.scrollHeight > el.clientHeight + 80
                  && el.clientHeight > 60;
            } catch(e) { return false; }
          }).sort((a, b) => b.scrollHeight - a.scrollHeight).slice(0, 8);

          for (const el of scrollEls) {
            const step = Math.max(80, el.clientHeight * 0.5);
            for (let y = 0; y <= el.scrollHeight; y += step) {
              el.scrollTop = y;
              await new Promise(r => setTimeout(r, 160));
            }
            // Dispatch hover events — SAP uses mouseenter to fetch download tokens
            el.querySelectorAll('a,[role="link"],[data-file],[data-url]').forEach(a => {
              a.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
              a.dispatchEvent(new MouseEvent('mouseover',  { bubbles: true }));
            });
            el.scrollTop = 0;
            await new Promise(r => setTimeout(r, 150));
          }
          // Main window scroll (catches any overflow on the document itself)
          for (let y = 0; y <= document.body.scrollHeight; y += window.innerHeight * 0.6) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 140));
          }
          window.scrollTo(0, 0);
        }
      }, () => resolve());
      setTimeout(resolve, 25000); // cap at 25 s regardless
    });

    // Let webRequest finish capturing newly triggered URLs
    await new Promise(r => setTimeout(r, 1500));
    pb.style.width = "70%"; pct.textContent = "Re-scanning with loaded URLs…";

    // ── Phase 3: re-scan — all URLs should now be in the webRequest cache ────
    const response = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { action: "scanRoadmapFiles", tabId: activeTabId },
        res => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else resolve(res);
        }
      );
      setTimeout(() => reject(new Error("Timeout — reload the page and try again")), 60000);
    });

    if (!response || !response.ok) throw new Error(response && response.error || "Scan failed");

    pb.style.width = "100%"; pct.textContent = "Done";

    bdcqFiles = response.bdcqFiles || [];
    const allFiles = response.allFiles || [];

    document.getElementById("rBdcqCount").textContent = bdcqFiles.length;
    document.getElementById("rAllCount").textContent  = allFiles.length;

    // Render ALL found files — BDCQ highlighted, others shown dimly
    // This helps diagnose what the scanner is seeing
    const listEl = document.getElementById("bdcqFileList");
    listEl.innerHTML = "";

    if (allFiles.length === 0) {
      noBdcq.textContent =
        "No files found. Make sure the Accelerators tab is open and the file list " +
        "is visible on screen. Try scrolling so files render, then scan again.";
      noBdcq.style.display = "block";
      dlBtn.disabled = true;
    } else {
      // Show BDCQ files first (full opacity), then others dimmed
      const isBdcqFn = f => /bdcq|bdc[\s_-]*questionnaire|business[\s_-]*driven[\s_-]*configuration[\s_-]*questionnaire/i.test(f.name);
      const sorted = [
        ...allFiles.filter(isBdcqFn),
        ...allFiles.filter(f => !isBdcqFn(f))
      ];

      sorted.forEach(f => {
        const isBdcq   = isBdcqFn(f);
        const item     = document.createElement("div");
        item.className = "file-item";
        const icon     = f.ext === 'pdf' ? '📄' : (f.ext.startsWith('xls') ? '📊' : '📁');
        const urlWarn  = (isBdcq && f.noUrl)
          ? '<span style="color:#FFAB00;font-size:9px"> ⚠ no URL yet</span>' : '';
        const badge    = isBdcq ? '<span class="badge badge-bdcq">BDCQ</span>' : '';
        item.style.opacity = isBdcq ? '1' : '0.4';
        item.innerHTML =
          `<span class="file-icon">${icon}</span>` +
          `<div style="flex:1">` +
          `<div class="file-name">${escHtml(f.name)}${badge}${urlWarn}</div>` +
          `<div class="file-access">${escHtml(f.access)} · ${f.ext || '?'}</div>` +
          `</div>`;
        listEl.appendChild(item);
      });

      const bdcqWithUrl    = bdcqFiles.filter(f => !f.noUrl);
      const bdcqWithoutUrl = bdcqFiles.filter(f =>  f.noUrl);

      if (bdcqFiles.length === 0) {
        noBdcq.textContent =
          "No BDCQ-named files found on this phase. Navigate to the Explore phase " +
          "that lists BDC Questionnaire files, make sure the list is visible, then scan again.";
        noBdcq.style.display = "block";
        dlBtn.disabled = true;
      } else if (bdcqWithUrl.length === 0) {
        // Names found but no download URLs even after auto-scroll
        noBdcq.textContent =
          `Found ${bdcqFiles.length} BDCQ file${bdcqFiles.length > 1 ? "s" : ""} by name ` +
          `(shown ⚠ above) but download URLs could not be loaded. ` +
          `Make sure you are on the Explore phase Accelerators tab and your SAP session ` +
          `is active, then click Scan Again.`;
        noBdcq.style.display = "block";
        dlBtn.disabled = true;
      } else {
        if (bdcqWithoutUrl.length > 0) {
          noBdcq.textContent =
            `${bdcqWithUrl.length} of ${bdcqFiles.length} BDCQ files have download URLs. ` +
            `${bdcqWithoutUrl.length} marked ⚠ will be skipped.`;
          noBdcq.style.display = "block";
        }
        dlBtn.disabled = false;
        dlBtn.textContent = bdcqDirHandle
          ? "📥 Save BDCQ Files to " + bdcqDirHandle.name
          : "📥 Choose Folder & Save BDCQ Files";
      }
    }

    result.style.display = "block";
    btn.textContent = "🔍 Scan Again";
    btn.disabled = false;

  } catch(e) {
    pb.style.background = "#FF4D4D";
    pct.textContent = "Failed";
    err.textContent = e.message;
    err.style.display = "block";
    btn.disabled = false;
  }
}

// ── Download BDCQ files ───────────────────────────────────────────────────────
// Two paths:
//   A) bdcqDirHandle set → fetch bytes via content script → write to chosen folder
//   B) no handle → chrome.downloads → Downloads/BDCQ/ (CORS-safe fallback)

async function handleDownloadBDCQ() {
  const btn   = document.getElementById("btnDownloadBDCQ");
  const prog  = document.getElementById("dlProgArea");
  const res   = document.getElementById("dlResult");
  const title = document.getElementById("dlResultTitle");
  const rows  = document.getElementById("dlResultRows");
  const err   = document.getElementById("dlErr");
  const pb    = document.getElementById("dlPb");
  const pct   = document.getElementById("dlPct");

  if (bdcqFiles.length === 0) {
    err.textContent = "No BDCQ files — scan the page first.";
    err.style.display = "block";
    return;
  }

  // ── Step 1: pick a folder if we don't have one yet ───────────────────────
  if (bdcqDirHandle && !(await verifyPermission(bdcqDirHandle))) {
    bdcqDirHandle = null; // permission lapsed — re-pick below
  }
  if (!bdcqDirHandle) {
    try {
      bdcqDirHandle = await window.showDirectoryPicker({ id: 'bdcq-save', mode: 'readwrite' });
      showChosenFolder(bdcqDirHandle.name);
      // Update button label now that folder is known
      btn.textContent = "📥 Save BDCQ Files to " + bdcqDirHandle.name;
    } catch (e) {
      if (e.name === "AbortError") return; // user cancelled picker — do nothing
      // Any other error (NotAllowedError, SecurityError, etc.) — show message and stop.
      // Never silently fall through to chrome.downloads when the user intended to pick a folder.
      err.textContent = "Could not open folder picker: " + e.message + ". Try reloading the extension popup.";
      err.style.display = "block";
      btn.disabled = false;
      return;
    }
  }

  btn.disabled = true;
  prog.style.display = "block";
  res.style.display = "none";
  err.style.display = "none";
  pb.style.width = "5%"; pb.style.background = "#00AAFF";

  const downloadable  = bdcqFiles.filter(f => !f.noUrl);
  const skipped       = bdcqFiles.filter(f =>  f.noUrl);
  const total         = downloadable.length;
  const saved         = [];
  const failed        = [];
  const parsedDomains = [];   // accumulates parsed Excel data for bdcq-questions.json

  if (total === 0) {
    err.textContent = "No BDCQ files have download URLs. Navigate to the Explore phase, wait for files to load, then scan again.";
    err.style.display = "block";
    btn.disabled = false;
    prog.style.display = "none";
    return;
  }

  const useFolder  = !!bdcqDirHandle;
  const folderName = bdcqDirHandle?.name || "BDCQ";
  pct.textContent  = useFolder ? `Saving to 📂 ${folderName}…` : "Queuing downloads…";

  // ── Create xlsx/ subfolder for Excel files ────────────────────────────────────
  let xlsxHandle = bdcqDirHandle;   // fallback: root of chosen folder
  let xlsxPath   = folderName;
  if (useFolder) {
    try {
      xlsxHandle = await bdcqDirHandle.getDirectoryHandle('xlsx', { create: true });
      xlsxPath   = `${folderName}/xlsx`;
    } catch (e) {
      console.warn('Could not create xlsx/ subfolder, saving to root:', e);
    }
  }

  // ── Pre-check: if SAP SSO is already mid-flight, wait for it to settle ────────
  // Otherwise the first file fetch hits an auth-redirect and cascades to the
  // slow SSO-tab fallback. Only waits when a saml2 tab is actually open now.
  const preTabs = await chrome.tabs.query({});
  if (preTabs.some(t => t.url && /accounts\.sap\.com\/saml2/i.test(t.url))) {
    pct.textContent = "Waiting for SAP SSO to finish…";
    await waitForSsoTabToClose(30000);
  }

  for (let i = 0; i < total; i++) {
    const f    = downloadable[i];
    const name = sanitizeFilename(f.name) + (f.name.toLowerCase().endsWith(".xlsx") ? "" : ".xlsx");
    const short = name.length > 28 ? name.substring(0, 26) + "…" : name;

    pb.style.width = Math.round(10 + (i / total) * 85) + "%";

    if (useFolder) {
      // ── Path A: popup fetch (host_permissions CORS bypass) → chosen folder ─
      // Extension pages get CORS bypass for *.sap.com via host_permissions.
      // Credentials are included so SAP session cookies are sent.
      pct.textContent = `Fetching ${i + 1}/${total}: ${short}`;
      let fetchErr = null;
      try {
        // Security: only fetch from SAP domains
        const urlHost = new URL(f.url).hostname;
        if (urlHost !== 'sap.com' && !urlHost.endsWith('.sap.com')) {
          throw new Error('Blocked: URL is not a SAP domain');
        }

        const resp = await fetch(f.url, { credentials: 'include' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        let arrayBuf = await resp.arrayBuffer();
        let bytes    = new Uint8Array(arrayBuf);

        // XLSX files are ZIP archives — first two bytes are always 'PK' (0x50 0x4B).
        // An HTML auth-redirect page starts with '<', never 'PK'.
        if (!(bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B)) {

          // ── Strategy 2: fetch via SAP frame (executeScript — same-site) ──────────
          // The popup is chrome-extension:// (cross-site) so SameSite=Strict cookies
          // are never sent → SAP returns HTML auth redirect.
          // Injecting a fetch INTO the live SAP tab (pr.alm.me.sap.com frame) runs it
          // same-site, cookies are sent, SAP returns the real bytes.
          // This is faster and more reliable than the click-and-capture approach.
          pct.textContent = `⟳ Frame fetch ${i + 1}/${total}: ${short}…`;

          const frameResult = await new Promise(resolve => {
            chrome.runtime.sendMessage(
              { action: 'fetchFileBytes', url: f.url, tabId: activeTabId },
              res => resolve(chrome.runtime.lastError
                ? { ok: false, error: chrome.runtime.lastError.message }
                : (res || { ok: false, error: 'No response' }))
            );
            setTimeout(() => resolve({ ok: false, error: 'Frame fetch timeout' }), 35000);
          });

          if (frameResult.ok && frameResult.base64) {
            const frameBuf   = base64ToArrayBuffer(frameResult.base64);
            const frameBytes = new Uint8Array(frameBuf);
            if (frameBytes.length > 4 && frameBytes[0] === 0x50 && frameBytes[1] === 0x4B) {
              arrayBuf = frameBuf;
              bytes    = frameBytes;
            }
          }

          // ── Strategy 3: SSO navigation retry (fallback if frame fetch failed) ────
          // Injects a hidden <a> into the SAP page and clicks it → same-site nav →
          // SAP backend issues a fresh signed CDN URL → downloads.onCreated fires →
          // we cancel the browser download and fetch the signed URL from the popup.
          if (!(bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B)) {
            pct.textContent = `⟳ SAP auth ${i + 1}/${total}: ${short} (up to 45 s for SSO…)`;

            const freshResult = await new Promise(resolve => {
              chrome.runtime.sendMessage(
                { action: 'navigateAndCaptureSapFile', url: f.url, tabId: activeTabId, timeoutMs: 45000 },
                res => resolve(chrome.runtime.lastError
                  ? { ok: false, error: chrome.runtime.lastError.message }
                  : (res || { ok: false, error: 'No response from background' }))
              );
              // Safety wrapper — resolve after 50 s regardless
              setTimeout(() => resolve({ ok: false, error: 'Outer timeout' }), 50000);
            });

            if (freshResult.ok && freshResult.url) {
              const finalUrl  = freshResult.url;
              const finalHost = (() => { try { return new URL(finalUrl).hostname; } catch (e) { return ''; } })();
              const isSapHost = finalHost === 'sap.com' || finalHost.endsWith('.sap.com');

              // SAP-domain finalUrl: use credentials + host_permissions CORS bypass.
              // Non-SAP CDN (Oracle OCI, Azure Blob, etc.): fetch without credentials —
              // signed URLs from these CDNs include all auth in the URL and return
              // Access-Control-Allow-Origin: * so the popup can reach them.
              const resp2 = await fetch(finalUrl, isSapHost ? { credentials: 'include' } : {});
              if (resp2.ok) {
                const ab2 = await resp2.arrayBuffer();
                const b2  = new Uint8Array(ab2);
                if (b2.length > 4 && b2[0] === 0x50 && b2[1] === 0x4B) {
                  arrayBuf = ab2;
                  bytes    = b2;
                }
              }
            }

            // ── Strategy 3b: SSO settled — retry now that the session is fresh ──
            // navigateAndCaptureSapFile just triggered a SAML refresh (the SSO
            // tab in the screenshot). Once that tab closes the session cookie is
            // fresh, so retry the fetch instead of failing and forcing the user
            // to manually refresh the browser and run again.
            if (!(bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B)) {
              const ssoSettled = await waitForSsoTabToClose(30000);
              if (ssoSettled) {
                pct.textContent = `⟳ Retry after SSO ${i + 1}/${total}: ${short}…`;
                // 1) direct fetch (popup — host_permissions CORS bypass)
                try {
                  const respR = await fetch(f.url, { credentials: 'include' });
                  if (respR.ok) {
                    const abR = await respR.arrayBuffer();
                    const bR  = new Uint8Array(abR);
                    if (bR.length > 4 && bR[0] === 0x50 && bR[1] === 0x4B) { arrayBuf = abR; bytes = bR; }
                  }
                } catch (_) {}
                // 2) same-site frame fetch if direct still didn't yield an XLSX
                if (!(bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B)) {
                  const retryFrame = await new Promise(resolve => {
                    chrome.runtime.sendMessage(
                      { action: 'fetchFileBytes', url: f.url, tabId: activeTabId },
                      res => resolve(chrome.runtime.lastError ? { ok: false } : (res || { ok: false }))
                    );
                    setTimeout(() => resolve({ ok: false }), 35000);
                  });
                  if (retryFrame.ok && retryFrame.base64) {
                    try {
                      const rBuf   = base64ToArrayBuffer(retryFrame.base64);
                      const rBytes = new Uint8Array(rBuf);
                      if (rBytes.length > 4 && rBytes[0] === 0x50 && rBytes[1] === 0x4B) { arrayBuf = rBuf; bytes = rBytes; }
                    } catch (_) {}
                  }
                }
              }
            }

            // Final check — if still not a valid XLSX after all strategies, throw
            if (!(bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B)) {
              const isSsoTimeout = (freshResult?.error || '').toLowerCase().includes('timeout') ||
                                   (frameResult?.error  || '').toLowerCase().includes('auth');
              throw new Error(
                isSsoTimeout
                  ? `SAP session expired — wait for the SAP SSO tab to finish loading, then go back to the Roadmap Viewer Accelerators tab and Scan + Download again immediately.`
                  : (frameResult?.error || freshResult?.error || `All fetch strategies failed — re-scan and try again immediately.`)
              );
            }
          }
        }

        pct.textContent = `Writing ${i + 1}/${total}: ${short}`;
        const fh = await xlsxHandle.getFileHandle(name, { create: true });
        const wr = await fh.createWritable();
        await wr.write(arrayBuf);
        await wr.close();
        saved.push({ name, via: 'folder' });

        // ── Parse Excel → structured data (arrayBuf still in memory) ──────────
        // SheetJS reads the same bytes we just saved — no extra file read needed.
        if (typeof XLSX !== 'undefined') {
          try {
            const wb     = XLSX.read(arrayBuf, { type: 'array' });
            const domain = name
              .replace(/^Business[\s_-]*Driven[\s_-]*Configuration[\s_-]*Questionnaire[\s_-]*/i, '')
              .replace(/\.xlsx$/i, '').trim();

            const sheets = wb.SheetNames.map(sheetName => {
              const ws   = wb.Sheets[sheetName];
              // sheet_to_json with header:1 returns array-of-arrays (raw rows)
              const raw  = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
              if (raw.length < 2) return null;

              // Find the first row that has ≥ 3 non-empty cells AND is not a title row.
              // Title rows contain "Business Driven Configuration" or "Questionnaire" as a
              // cell value — they appear before the real column headers and must be skipped.
              const isTitleRow = row =>
                row.some(c => /Business[\s_-]*Driven|Questionnaire/i.test(String(c)));

              let hdrIdx = 0;
              for (let r = 0; r < Math.min(raw.length, 10); r++) {
                const nonEmpty = raw[r].filter(c => String(c).trim()).length;
                if (nonEmpty >= 3 && !isTitleRow(raw[r])) { hdrIdx = r; break; }
              }
              const headers = raw[hdrIdx].map(h => String(h).trim());

              // Convert remaining rows to objects keyed by header name
              const rows = raw.slice(hdrIdx + 1)
                .filter(row => row.some(c => String(c).trim()))   // skip blank rows
                .map(row => {
                  const obj = {};
                  headers.forEach((h, idx) => { if (h) obj[h] = String(row[idx] ?? '').trim(); });
                  return obj;
                });

              return { sheet: sheetName, headers, rows };
            }).filter(Boolean);

            parsedDomains.push({ domain, filename: name, sheets });
          } catch (parseErr) {
            // Non-fatal — Excel still saved, just skip JSON for this file
            console.warn('XLSX parse error for', name, parseErr);
          }
        }

        pct.textContent = `✓ ${i + 1}/${total}: ${short}`;
      } catch (e) {
        fetchErr = e.message;
      }

      if (fetchErr) {
        // Folder was chosen — every file goes there or is reported as failed.
        // Never silently save to Downloads when the user picked a specific folder.
        failed.push({ name, reason: fetchErr });
      }

    } else {
      // ── Path B: chrome.downloads → Downloads/BDCQ/ ───────────────────────
      pct.textContent = `Downloading ${i + 1}/${total}: ${short}`;
      const dlOk = await chromeDl(f.url, name);
      if (dlOk) saved.push({ name, via: 'downloads' });
      else      failed.push({ name, reason: "Download failed" });
    }
  }

  // ── Write bdcq-questions.json ────────────────────────────────────────────────
  // All domains merged into one file the Fulcrum agent loads automatically.
  // Strategy: try to write into bdcq/ subdirectory first (so user can pick the
  // explore-accelerator/ root and the file lands where the server expects it).
  // Fallback: write to the root of the chosen folder if subdirectory creation fails.
  let jsonLine = "";

  if (useFolder && parsedDomains.length > 0 && bdcqDirHandle) {
    pct.textContent = `📊 Writing bdcq-questions.json…`;
    const master = {
      type:         'bdcq_questions',
      description:  'Business Driven Configuration Questionnaires — structured for agent use. ' +
                    'Each domain has sheets; each sheet has rows with question columns. ' +
                    'Rows where a cell maps to a Scope Item ID indicate that scope item is in scope when the answer is Yes/Y.',
      generatedAt:  new Date().toISOString(),
      totalDomains: parsedDomains.length,
      domains:      parsedDomains,
    };
    const jsonStr = JSON.stringify(master, null, 2);

    let jsonWritten = false;
    let jsonWhere   = "";

    // Try 1: write bdcq-questions.json into bdcq/ subfolder of the chosen folder.
    // This is where the Fulcrum server's first search path expects it.
    try {
      const subDir = await bdcqDirHandle.getDirectoryHandle('bdcq', { create: true });
      const jFh    = await subDir.getFileHandle('bdcq-questions.json', { create: true });
      const jWr    = await jFh.createWritable();
      await jWr.write(jsonStr);
      await jWr.close();
      jsonWritten = true;
      jsonWhere   = `${folderName}/bdcq/`;
    } catch (e1) {
      console.warn('Could not write to bdcq/ subfolder, trying root:', e1);
      // Try 2: fallback — write next to the xlsx files in the root of chosen folder
      try {
        const jFh = await bdcqDirHandle.getFileHandle('bdcq-questions.json', { create: true });
        const jWr = await jFh.createWritable();
        await jWr.write(jsonStr);
        await jWr.close();
        jsonWritten = true;
        jsonWhere   = `${folderName}/`;
      } catch (e2) {
        console.warn('Could not write bdcq-questions.json to root either:', e2);
      }
    }

    if (jsonWritten) {
      jsonLine = `<div class="rrow"><span class="rk">📊 bdcq-questions.json</span><span class="rv" style="color:#A100FF;font-size:9px">→ ${escHtml(jsonWhere)} (${parsedDomains.length} domains)</span></div>`;
    } else {
      jsonLine = `<div class="rrow"><span class="rk" style="color:#FFAB00">⚠ bdcq-questions.json</span><span class="rv" style="color:#FFAB00;font-size:9px">could not be written — paste manually to bdcq/</span></div>`;
    }
  } else if (useFolder && parsedDomains.length === 0) {
    jsonLine = `<div class="rrow"><span class="rk" style="color:#FFAB00">⚠ bdcq-questions.json</span><span class="rv" style="color:#FFAB00;font-size:9px">skipped — no files could be parsed</span></div>`;
  }

  pb.style.width = "100%";
  pct.textContent = "✅ Complete";

  const folderSaved    = saved.filter(s => s.via === 'folder').length;
  const downloadsSaved = saved.filter(s => s.via === 'downloads').length;

  title.textContent = `✓ ${saved.length} of ${total} files saved`;

  // When a folder was chosen, all saves go there — no Downloads fallback.
  // Path B (no folder) still uses chrome.downloads.
  const locationLine = useFolder && folderSaved > 0
    ? `<div class="rrow"><span class="rk">📂 xlsx files</span><span class="rv" style="color:#00C875">${escHtml(xlsxPath)}/ (${folderSaved} files)</span></div>`
    : "";
  const dlLine = !useFolder && downloadsSaved > 0
    ? `<div class="rrow"><span class="rk">Downloads</span><span class="rv" style="color:#00AAFF">Downloads/BDCQ/ (${downloadsSaved})</span></div>`
    : "";
  const skipLine = skipped.length > 0
    ? `<div class="rrow"><span class="rk" style="color:#FFAB00">⚠ skipped</span><span class="rv" style="color:#FFAB00;font-size:9px">${skipped.length} had no URL</span></div>`
    : "";
  const failLines = failed.map(f =>
    `<div class="rrow"><span class="rk" style="color:#FF6B6B">✗ failed</span>` +
    `<span class="rv" style="color:#FF6B6B;font-size:9px">${escHtml(f.name.replace(/\.xlsx$/i,''))}</span></div>`
  ).join("");

  rows.innerHTML = locationLine + dlLine + skipLine + failLines + jsonLine;
  res.style.display = "block";

  // Reset button
  btn.textContent = bdcqDirHandle
    ? "📥 Save BDCQ Files to " + folderName
    : "📥 Choose Folder & Save BDCQ Files";
  btn.disabled = false;

  // ── System notification — fires even if popup is closed ──────────────────
  const notifMsg = useFolder
    ? `${folderSaved} of ${total} files saved to 📂 ${folderName}` +
      (failed.length > 0 ? ` · ${failed.length} failed` : "")
    : `${downloadsSaved} file${downloadsSaved > 1 ? "s" : ""} saved to Downloads/BDCQ/`;

  chrome.notifications.create('bdcq-complete', {
    type:    'basic',
    iconUrl: 'icon48.png',
    title:   `✅ BDCQ Download Complete — ${saved.length}/${total} files`,
    message: notifMsg,
    priority: 2
  });
}

// ── chrome.downloads fallback helper ─────────────────────────────────────────

function chromeDl(url, name) {
  return new Promise(resolve => {
    chrome.runtime.sendMessage(
      { action: "downloadRoadmapFile", url, name },
      r => {
        if (chrome.runtime.lastError || !r?.ok) resolve(false);
        else resolve(true);
      }
    );
    setTimeout(() => resolve(false), 15000);
  });
}

// ── Generate Agent Files ──────────────────────────────────────────────────────
// Produces two fully-segregated sets of Claude-optimised files:
//
//   KDD agent (Knowledge-Driven Discovery) — grouped by L2 Business Function:
//     kdd-master.md                — all functions + process list
//     kdd-lob-{function}.md × N   — full process descriptions per L2 function
//
//   F2S agent (Fit-to-Standard) — grouped by L1 Business Domain:
//     f2s-master.md               — domain map + Option-A hierarchy note
//     f2s-index.json              — compact one-liner per scope item (L4)
//     f2s-{domain}.md × N        — L2 function → L4 scope items → L5 steps


// ── Helpers ───────────────────────────────────────────────────────────────────

async function agentWriteFile(dirHandle, name, content) {
  const fh = await dirHandle.getFileHandle(name, { create: true });
  const wr = await fh.createWritable();
  await wr.write(content);
  await wr.close();
}

function agentSlug(prefix, text, maxLen) {
  return prefix + text.toLowerCase()
    .replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-").replace(/^-|-$/g, "")
    .substring(0, maxLen);
}

function stripHtmlToText(html) {
  if (!html) return "";
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return (tmp.textContent || "").replace(/\s+/g, " ").trim();
}

// ── KDD: kdd-master.md ────────────────────────────────────────────────────────
function buildKddMasterMd(kddByLob, version) {
  let md  = `# SAP S/4HANA Cloud PE — KDD Knowledge Index\n`;
  md += `Version ${version} · Knowledge-Driven Discovery agent reference\n\n`;
  md += `## How to Use\n`;
  md += `1. Find the relevant **L2 Business Function** below.\n`;
  md += `2. Load \`kdd-lob-{function}.md\` — contains the full SAP process description for every scope item in that function.\n`;
  md += `3. Use descriptions for deep knowledge extraction, capability mapping and discovery.\n\n`;
  md += `## L2 Business Functions\n\n`;

  Object.keys(kddByLob).sort().forEach(lob => {
    const procs = kddByLob[lob].slice().sort((a, b) => a.name.localeCompare(b.name));
    const slug  = agentSlug("kdd-lob-", lob, 38);
    md += `### ${lob} (${procs.length} processes)\n`;
    md += `File: \`${slug}.md\`\n`;
    procs.forEach(p => { md += `- ${p.id} — ${p.name}\n`; });
    md += "\n";
  });
  return md;
}

// ── KDD: kdd-lob-{function}.md ────────────────────────────────────────────────
function buildKddLobMd(lob, processes, version) {
  const sorted = processes.slice().sort((a, b) => a.name.localeCompare(b.name));
  let md  = `# ${lob}\n`;
  md += `KDD Knowledge Base · SAP S/4HANA Cloud PE · v${version} · ${sorted.length} processes\n\n`;

  sorted.forEach(p => {
    md += `## ${p.id} — ${p.name}\n`;
    const text = stripHtmlToText(p.description || "");
    if (text) md += `${text}\n`;
    md += `\n---\n\n`;
  });
  return md;
}

// ── F2S: f2s-master.md ────────────────────────────────────────────────────────
function buildF2sMasterMd(byL1, version, total) {
  let md  = `# SAP S/4HANA Cloud PE — F2S Process Catalog\n`;
  md += `Version ${version} · ${total} scope items · Fit-to-Standard agent reference\n\n`;
  md += `## Hierarchy (Option A)\n`;
  md += `| Level | Label | Example |\n`;
  md += `|---|---|---|\n`;
  md += `| L1 | Business Domain | Finance & Controlling |\n`;
  md += `| L2 | Business Function | Accounts Payable |\n`;
  md += `| L3 | Process Group | *(reserved — future enrichment)* |\n`;
  md += `| L4 | Scope Item | BD6 — Vendor Invoice Management |\n`;
  md += `| L5 | Process Steps | Receive invoice · Validate PO · Post · Pay |\n\n`;
  md += `## How to Use\n`;
  md += `1. Check \`f2s-index.json\` — find scope item IDs (L4) by l1/l2/name/hint.\n`;
  md += `2. Load \`f2s-{domain}.md\` — get L5 process steps, benefits and features.\n\n`;
  md += `## L1 Business Domains\n\n`;

  Object.keys(byL1).sort().forEach(l1 => {
    const count = Object.values(byL1[l1]).reduce((s, a) => s + a.length, 0);
    const slug  = agentSlug("f2s-", l1, 50);
    const funcs = Object.keys(byL1[l1]).sort().join(" · ");
    md += `### ${l1} (${count} scope items)\n`;
    md += `L2 Functions: ${funcs}\n`;
    md += `File: \`${slug}.md\`\n\n`;
  });
  return md;
}

// ── F2S: f2s-{domain}.md ─────────────────────────────────────────────────────
function buildF2sDomainMd(l1, byL2, version) {
  const total = Object.values(byL2).reduce((s, a) => s + a.length, 0);
  let md  = `# ${l1} (L1)\n`;
  md += `F2S Scope Catalog · SAP S/4HANA Cloud PE · v${version} · ${total} scope items\n\n`;

  Object.keys(byL2).sort().forEach(l2 => {
    const procs = byL2[l2].slice().sort((a, b) => a.name.localeCompare(b.name));
    md += `## ${l2} (L2)\n\n`;

    procs.forEach(p => {
      md += `### ${p.id} — ${p.name} (L4)\n`;
      if (p.overview) md += `${p.overview}\n\n`;

      const parts = [];
      if (p.l4 && p.l4.length)                            parts.push(`**L5 Steps**: ${p.l4.join(" · ")}`);
      if (p.businessBenefits && p.businessBenefits.length) parts.push(`**Benefits**: ${p.businessBenefits.join(" · ")}`);
      if (p.keyFeatures      && p.keyFeatures.length)      parts.push(`**Features**: ${p.keyFeatures.join(" · ")}`);
      if (p.scope)                                          parts.push(`**Scope**: ${p.scope}`);
      if (parts.length) md += parts.join("\n") + "\n";
      md += `\n---\n\n`;
    });
  });
  return md;
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sanitizeFilename(name) {
  return name
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .substring(0, 200);
}
