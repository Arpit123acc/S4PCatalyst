// SAP Deck Agent v27.1 — Background Service Worker
// v27.1 changes:
//   - scanRoadmapFiles: injects comprehensive scan directly via executeScript
//     (3 strategies: Files-section DOM, performance API, JSON API re-fetch)
//   - fetchRoadmapFile:  injects file-fetch function via executeScript
//   - Existing Process Navigator handlers unchanged

// ── Security: SAP-domain guard ───────────────────────────────────────────────
// Every credentialed network call (fetch / chrome.downloads) is validated
// against this guard before proceeding.  No credentials are EVER sent to a
// non-SAP destination.
function isSapDomain(url) {
  try {
    const host = new URL(url).hostname;
    // Allow *.sap.com only — rejects anything else
    return host === 'sap.com' || host.endsWith('.sap.com');
  } catch (e) { return false; }
}

// ── Passive URL cache (in-memory, never persisted or transmitted) ─────────────
// When SAP's own JS fetches a document, webRequest sees the real download URL.
// We cache it so the scanner can resolve filenames that have no visible href.
// Keys: normalised filename slug  |  Values: SAP file URL (*.sap.com only)
const sapFileUrlCache = new Map();

// ── Fresh-URL waiters ─────────────────────────────────────────────────────────
// When click-and-capture is active for a file, a resolve fn is registered here.
// As soon as webRequest sees a new SAP file URL whose key matches, it resolves.
// Keys: normalised slug (same scheme as sapFileUrlCache)
const _freshUrlWaiters = new Map();

// ── Shared helper: check if a URL + key pair matches any active waiter ────────
// Returns true if a waiter was resolved.
function _notifyWaiters(url, key) {
  if (_freshUrlWaiters.size === 0) return false;
  const urlNorm = url.toLowerCase().replace(/\W+/g, '');
  for (const [waitSlug, resolve] of _freshUrlWaiters) {
    // 1. Slug-prefix match (filename vs filename)
    const shorter    = waitSlug.length < key.length ? waitSlug : key;
    const longer     = waitSlug.length < key.length ? key      : waitSlug;
    const slugMatch  = longer.startsWith(shorter.substring(0, Math.min(shorter.length, 25)));
    // 2. URL-content match — waitSlug appears anywhere in the normalised URL
    //    (handles opaque CDN paths where filename is embedded in the URL)
    const urlMatch   = urlNorm.includes(waitSlug.substring(0, Math.min(waitSlug.length, 20)));
    if (slugMatch || urlMatch) {
      resolve(url);
      _freshUrlWaiters.delete(waitSlug);
      return true;
    }
  }
  return false;
}

chrome.webRequest.onBeforeRequest.addListener(
  details => {
    const url = details.url;
    // Only cache confirmed SAP domains and known file extensions
    if (!isSapDomain(url)) return;
    if (!/\.(xlsx?|pdf|pptx?|docx?|zip|xlsm)\b/i.test(url)) return;
    try {
      const raw = decodeURIComponent(url.split('/').pop().split('?')[0]);
      const key = raw.replace(/\.(xlsx?|pdf|pptx?|docx?|zip|xlsm)$/i, '')
                     .toLowerCase().replace(/\W+/g, '');
      if (key.length < 4) return;
      sapFileUrlCache.set(key, url);
      _notifyWaiters(url, key);
    } catch (e) {}
  },
  { urls: ['https://*.sap.com/*'] }
);

// ── Waiter notification via downloads.onDeterminingFilename ──────────────────
// WHY onDeterminingFilename instead of onCreated:
//   onCreated fires as Chrome starts downloading — for small files (~200 KB) the
//   write completes before cancel() IPC arrives, so the file lands in Downloads.
//
//   onDeterminingFilename fires BEFORE Chrome opens the file handle on disk AND
//   it supports async suggest() — we return true to hold Chrome paused while
//   cancel() IPC completes, then call suggest() after cancel finishes.
//   Chrome sees the download as cancelled and never creates the file.
//
// Timeline with return true:
//   1. onDeterminingFilename fires → we return true (Chrome waits for suggest)
//   2. cancel() IPC sent → completes in ~10 ms
//   3. suggest() called inside cancel callback → Chrome cleans up (no file written)
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  const initiatedUrl = item.url      || '';
  const finalUrl     = item.finalUrl || item.url || '';

  // ── Priority 1: exact URL match (navigateAndCaptureSapFile) ───────────────
  // item.url is the pre-redirect URL — matches the key we registered in _freshUrlWaiters.
  if (initiatedUrl && _freshUrlWaiters.has(initiatedUrl)) {
    const resolve = _freshUrlWaiters.get(initiatedUrl);
    _freshUrlWaiters.delete(initiatedUrl);

    // cancel() first — Chrome is holding the download paused because we return true below.
    // suggest() is called INSIDE the cancel callback so it only fires after cancel completes.
    // This guarantees no file handle is ever opened on disk.
    chrome.downloads.cancel(item.id, () => {
      chrome.downloads.erase({ id: item.id }, () => {});
      try { suggest({ filename: item.filename || 'cancelled' }); } catch (_) {}
    });

    if (item.tabId && item.tabId !== -1) {
      setTimeout(() => chrome.tabs.remove(item.tabId, () => {}), 800);
    }
    resolve(finalUrl || initiatedUrl);  // finalUrl = post-redirect signed CDN URL

    return true;  // ← async suggest: Chrome holds download until suggest() is called above
  }

  // ── Priority 2: slug / URL-content match (webRequest fallback) ───────────
  const url = finalUrl || initiatedUrl;
  if (!url || !isSapDomain(url)) { suggest(); return; }
  try {
    const raw = decodeURIComponent(url.split('/').pop().split('?')[0]);
    const key = raw.replace(/\.(xlsx?|pdf|pptx?|docx?|zip|xlsm)$/i, '')
                   .toLowerCase().replace(/\W+/g, '');
    const effectiveKey = key.length >= 4 ? key : '';
    if (effectiveKey) sapFileUrlCache.set(effectiveKey, url);

    const notified = _notifyWaiters(url, effectiveKey || url.toLowerCase().replace(/\W+/g, ''));
    if (notified) {
      // Same pattern: hold download → cancel() → then suggest()
      chrome.downloads.cancel(item.id, () => {
        chrome.downloads.erase({ id: item.id }, () => {});
        try { suggest({ filename: item.filename || 'cancelled' }); } catch (_) {}
      });
      if (item.tabId && item.tabId !== -1) {
        setTimeout(() => chrome.tabs.remove(item.tabId, () => {}), 800);
      }
      return true;  // async suggest
    } else {
      suggest(); // not our file — let it download normally to Downloads
    }
  } catch (e) { suggest(); }
});


chrome.runtime.onMessage.addListener((msg, sender, respond) => {

  if (msg.action === "fetchScopeCatalog") {
    fetchCatalogViaScript(msg.tabId, respond);
    return true;
  }

  // ── Roadmap Viewer: scan page for accelerator files ──────────────────────
  if (msg.action === "scanRoadmapFiles") {
    chrome.scripting.executeScript({
      target: { tabId: msg.tabId, allFrames: true }, // scan ALL frames — file list lives in pr.alm.me.sap.com iframe
      func: async function scanRoadmapPage() {
        // ── helpers ─────────────────────────────────────────────────────────
        const ACCESS_RE  = /\((SAP\s+Partner|SAP\s+Customer|SAP\s+Internal|Public|Partner|Customer)\)/i;
        const BDCQ_RE    = /bdcq|bdc[\s_-]*questionnaire|business[\s_-]*driven[\s_-]*configuration[\s_-]*questionnaire/i;
        const FILE_EXT   = /\.(xlsx?|pdf|docx?|pptx?|zip)\b/i;
        const SKIP_ASSET = /\.(css|js|png|jpg|jpeg|svg|woff2?|gif|ico|map|ttf|eot)(\?|$)/i;

        // Fetch with a hard timeout so one slow endpoint can't stall everything
        async function timedFetch(url, ms = 4000) {
          const ctrl  = new AbortController();
          const timer = setTimeout(() => ctrl.abort(), ms);
          try {
            const r = await fetch(url, { credentials: 'include', signal: ctrl.signal });
            clearTimeout(timer);
            return r;
          } catch (e) { clearTimeout(timer); throw e; }
        }

        function cleanName(raw) {
          return (raw || '')
            .replace(/\s*\((SAP\s+Partner|SAP\s+Customer|SAP\s+Internal|Public|Partner|Customer)\)\s*$/i, '')
            .replace(/\s+/g, ' ').trim();
        }

        function resolveHref(href) {
          if (!href) return '';
          if (/^(javascript:|#)/i.test(href)) return '';
          try { return /^https?:\/\//i.test(href) ? href : new URL(href, location.origin).href; }
          catch (e) { return ''; }
        }

        function getExt(text, url) {
          const m = (text + ' ' + url).match(/\.(xlsx?|pdf|docx?|pptx?|zip)/i);
          return m ? m[1].toLowerCase() : '';
        }

        const files  = [];
        const seenN  = new Set(); // dedupe by cleaned name
        const seenU  = new Set(); // dedupe by URL

        function add(name, url, access, rawText) {
          const key = name.toLowerCase().replace(/\W/g, '');
          if (!name || name.length < 3 || seenN.has(key)) return;
          if (url && seenU.has(url)) return;
          seenN.add(key);
          if (url) seenU.add(url);
          files.push({
            name,
            url:    url  || '',
            access: access || 'Unknown',
            ext:    getExt(rawText || name, url || ''),
            noUrl:  !url
          });
        }

        // ── Strategy 1 — "Files" section awareness ──────────────────────────
        // SAP Roadmap Viewer renders headings like: <span>Files</span>
        // followed by a list of <a> links (often href="javascript:void(0)").
        // We find any element whose ONLY text is "Files" and walk up to grab links.

        const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
          if (el.children.length > 2) return false;
          const t = (el.textContent || '').trim();
          return t === 'Files' || t === 'Files:';
        });

        candidates.forEach(header => {
          // Walk up to find a container that has <a> children
          let node = header;
          for (let i = 0; i < 8; i++) {
            if (!node.parentElement) break;
            node = node.parentElement;
            const links = node.querySelectorAll('a');
            if (links.length > 0) {
              links.forEach(a => {
                const text  = (a.textContent || '').trim();
                const name  = cleanName(text);
                const href  = resolveHref(a.getAttribute('href'));
                const accM  = text.match(ACCESS_RE);
                if (name && name.length >= 3) add(name, href, accM ? accM[1] : 'Unknown', text);
              });
              break;
            }
          }
        });

        // ── Strategy 2 — All <a> elements with file extensions in text ───────
        document.querySelectorAll('a').forEach(a => {
          const text = (a.textContent || '').trim();
          if (!FILE_EXT.test(text)) return;
          const name = cleanName(text);
          const href = resolveHref(a.getAttribute('href'));
          const accM = text.match(ACCESS_RE);
          add(name, href, accM ? accM[1] : 'Public', text);
        });

        // ── Strategy 3 — data-* attributes on any element ───────────────────
        document.querySelectorAll('[data-download-url],[data-file-url],[data-url]').forEach(el => {
          const url  = el.getAttribute('data-download-url') ||
                       el.getAttribute('data-file-url')     ||
                       el.getAttribute('data-url') || '';
          const text = (el.textContent || '').trim();
          const name = cleanName(text) || cleanName(url.split('/').pop().split('?')[0]);
          if (name && url) add(name, url, 'Unknown', text + ' ' + url);
        });

        // ── Strategy 4 — Performance resource entries (actual fetched URLs) ──
        try {
          performance.getEntriesByType('resource').forEach(entry => {
            const url = entry.name;
            if (!FILE_EXT.test(url)) return;
            const rawName = decodeURIComponent(url.split('/').pop().split('?')[0]);
            const name    = cleanName(rawName);
            // If we already have it by name without a URL, patch in the URL
            const existing = files.find(f =>
              f.noUrl && f.name.toLowerCase().replace(/\W/g,'').startsWith(
                name.toLowerCase().replace(/\W/g,'').substring(0, 20)
              )
            );
            if (existing) {
              existing.url   = url;
              existing.noUrl = false;
              if (!existing.ext) existing.ext = getExt('', url);
            } else {
              add(name, url, 'Public', url);
            }
          });
        } catch (e) {}

        // ── Strategies 5+6 — Parallel API probes (4 s timeout each) ────────
        // Run ALL JSON API calls in parallel so one slow endpoint doesn't stall
        // the others. Individual timeout: 4 s. Overall budget: ~5 s.

        function applyJsonResponse(text) {
          // Extract direct xlsx URLs
          [...text.matchAll(/"(https?:[^"\\]*\.xlsx?[^"\\]*)"/gi)].forEach(m => {
            const url  = m[1];
            const name = cleanName(decodeURIComponent(url.split('/').pop().split('?')[0]));
            const ex   = files.find(f => f.noUrl && name && f.name.toLowerCase().replace(/\W/g,'').startsWith(name.toLowerCase().replace(/\W/g,'').substring(0,15)));
            if (ex) { ex.url = url; ex.noUrl = false; } else add(name, url, 'Unknown', url);
          });
          // Parse structured items
          try {
            const data  = JSON.parse(text);
            const items = Array.isArray(data) ? data :
              (data.value || data.d?.results || data.results ||
               data.items || data.PhaseAcceleratorItems || data.acceleratorItems || []);
            if (Array.isArray(items)) {
              items.forEach(item => {
                const name = cleanName(
                  item.FileName || item.fileName || item.DocumentName || item.documentName ||
                  item.Title    || item.title    || item.Name         || item.name         || ''
                );
                const url =
                  item.DownloadUrl   || item.downloadUrl   || item.ContentUrl  || item.contentUrl  ||
                  item.Url           || item.url           || item.LinkUrl     || item.linkUrl     ||
                  item.AttachmentUrl || item.attachmentUrl || '';
                if (!name) return;
                const ex = files.find(f => f.noUrl && f.name.toLowerCase().replace(/\W/g,'').startsWith(name.toLowerCase().replace(/\W/g,'').substring(0,15)));
                if (ex) { if (url) { ex.url = url; ex.noUrl = false; } }
                else add(name, url, 'Unknown', name + ' ' + url);
              });
            }
          } catch (e) {}
        }

        // Collect all API URLs to probe: performance entries + direct candidates
        const probeUrls = new Set();

        // From performance entries (strategy 5)
        try {
          performance.getEntriesByType('resource')
            .filter(e => (e.initiatorType === 'fetch' || e.initiatorType === 'xmlhttprequest') && !SKIP_ASSET.test(e.name))
            .slice(0, 25)
            .forEach(e => probeUrls.add(e.name));
        } catch (e) {}

        // From URL-derived candidates (strategy 6)
        try {
          const accM = location.href.match(/phaseaccelerator\/([a-f0-9A-F]{8,}):?([^?#]*)/i);
          if (accM) {
            const accId    = accM[1];
            const phaseIds = (accM[2] || '').split(',').map(s => s.trim()).filter(s => s.length > 10);
            for (const base of ['/api/rm-service/v1', '/ui/rm-service/v1', '/api/rm/v1']) {
              probeUrls.add(`${base}/PhaseAccelerators('${accId}')/PhaseAcceleratorItems`);
              probeUrls.add(`${base}/PhaseAcceleratorItems?$filter=PhaseAcceleratorId eq '${accId}'&$top=500`);
              probeUrls.add(`${base}/phaseAccelerators/${accId}/items`);
              for (const pid of phaseIds.slice(0, 2)) {
                probeUrls.add(`${base}/PhaseAcceleratorItems?$filter=PhaseId eq '${pid}'&$top=200`);
              }
            }
          }
        } catch (e) {}

        // Fire all probes in parallel — each has its own 4 s abort timer
        await Promise.allSettled([...probeUrls].slice(0, 35).map(async url => {
          try {
            const resp = await timedFetch(url, 4000);
            if (!resp.ok) return;
            const ct = resp.headers.get('content-type') || '';
            if (!ct.includes('json') && !ct.includes('odata') && !ct.includes('xml')) return;
            const text = await resp.text();
            if (!text.includes('xls') && !text.includes('Questionnaire') &&
                !text.includes('Backlog') && !text.includes('ttach') &&
                !text.includes('ownload') && !text.includes('Template')) return;
            applyJsonResponse(text);
          } catch (e) {}
        }));

        // ── Build BDCQ-filtered list ─────────────────────────────────────────
        // Include ALL name-matched BDCQ files regardless of whether we have
        // a download URL — the popup handles noUrl files with a clear message.
        const bdcqFiles = files.filter(f => /^xlsx?$/i.test(f.ext) && BDCQ_RE.test(f.name));

        return { ok: true, allFiles: files, bdcqFiles };
      }
    },
    results => {
      if (chrome.runtime.lastError) {
        respond({ ok: false, error: chrome.runtime.lastError.message }); return;
      }
      if (!results || results.length === 0) {
        respond({ ok: false, error: "Scan returned no result — make sure the page is fully loaded." });
        return;
      }

      // Merge files found across ALL frames (main frame + pr.alm.me.sap.com iframe).
      // Priority rule: if the same file name appears in multiple frames, prefer the
      // entry that HAS a URL (noUrl:false) over one without.
      const byName   = new Map(); // nameKey → file entry
      const seenUrls = new Set();

      results.forEach(r => {
        if (!r.result?.allFiles) return;
        r.result.allFiles.forEach(f => {
          const nameKey = f.name.toLowerCase().replace(/\W/g, '');
          if (f.url && seenUrls.has(f.url)) return;
          const existing = byName.get(nameKey);
          if (!existing) {
            byName.set(nameKey, f);
            if (f.url) seenUrls.add(f.url);
          } else if (existing.noUrl && !f.noUrl) {
            // Upgrade: replace the noUrl entry with one that has a URL
            byName.set(nameKey, f);
            if (f.url) seenUrls.add(f.url);
          }
        });
      });

      const allFiles  = Array.from(byName.values());

      // ── Enrich noUrl entries from webRequest-captured URLs ─────────────────
      // When SAP's JS previously fetched/downloaded a file, the real URL was
      // stored in sapFileUrlCache. Use it to upgrade any noUrl entry.
      allFiles.forEach(f => {
        if (!f.noUrl) return;
        const key = f.name.toLowerCase().replace(/\W+/g, '');
        // Try exact key match first, then prefix match for files with long names
        let captured = sapFileUrlCache.get(key);
        if (!captured) {
          for (const [k, v] of sapFileUrlCache) {
            if (k.startsWith(key.substring(0, Math.min(key.length, 20))) ||
                key.startsWith(k.substring(0, Math.min(k.length, 20)))) {
              captured = v; break;
            }
          }
        }
        if (captured) { f.url = captured; f.noUrl = false; }
      });

      // Include ALL name-matched BDCQ files — noUrl ones shown with guidance in popup
      const bdcqFiles = allFiles.filter(f =>
        /^xlsx?$/i.test(f.ext) &&
        /bdcq|bdc[\s_-]*questionnaire|business[\s_-]*driven[\s_-]*configuration[\s_-]*questionnaire/i.test(f.name)
      );

      respond({ ok: true, allFiles, bdcqFiles });
    });
    return true;
  }

  // ── Same-site file navigation (replaces old click-and-capture) ───────────────
  //
  // WHY: SAP CDN files require SameSite=Strict session cookies.  The extension
  // popup is chrome-extension:// origin → cross-site → those cookies are never
  // sent → CDN returns HTML auth redirect instead of XLSX bytes.
  //
  // SAP DOES NOT regenerate URLs when existing page links are clicked; it reuses
  // the cached URL from page load (so click-on-existing-link never helped).
  //
  // SOLUTION: inject a hidden <a> into the live SAP page and click it.
  //   • The SAP page is on me.sap.com / pr.alm.me.sap.com → same eTLD+1 (sap.com)
  //   • Browser sends SameSite=Strict cookies to CDN (same-site navigation ✓)
  //   • SAP backend validates session → redirects to a fresh signed CDN URL
  //   • downloads.onDeterminingFilename fires (before any bytes written to disk)
  //   • We cancel the browser download and return finalUrl to popup
  //   • Popup fetches finalUrl with host_permissions CORS bypass → saves to folder
  if (msg.action === "navigateAndCaptureSapFile") {
    const fileUrl = msg.url;
    const tabId   = msg.tabId;

    if (!isSapDomain(fileUrl)) {
      respond({ ok: false, error: "Blocked: not a SAP domain" });
      return true;
    }

    // Guard: respond is called exactly once (waiter / timeout / inject-error).
    let responded    = false;
    let openedTabId  = null;   // track the new tab so we can close it on timeout

    function safeRespond(result) {
      if (!responded) { responded = true; respond(result); }
    }
    function closeOpenedTab() {
      if (openedTabId) { chrome.tabs.remove(openedTabId, () => {}); openedTabId = null; }
    }

    // Track the next tab Chrome creates — that will be ours.
    // We listen once, capture the ID, then remove the listener.
    function onTabCreated(tab) {
      openedTabId = tab.id;
      chrome.tabs.onCreated.removeListener(onTabCreated);
    }
    chrome.tabs.onCreated.addListener(onTabCreated);

    // Register a waiter keyed by the EXACT original URL.
    // downloads.onDeterminingFilename matches item.url against this key and resolves here.
    // Timeout: 45 s — enough for a SAML SSO roundtrip that happens when the
    // file download backend needs a fresh session assertion (first file only).
    new Promise((resolve, reject) => {
      _freshUrlWaiters.set(fileUrl, resolve);
      setTimeout(() => {
        _freshUrlWaiters.delete(fileUrl);
        chrome.tabs.onCreated.removeListener(onTabCreated); // clean up if not yet fired
        closeOpenedTab();
        reject(new Error(
          "Timeout — SAP did not complete the download (SAML or token issue). " +
          "Try: make sure you are on the Roadmap Viewer Accelerators tab, then re-scan and download immediately."
        ));
      }, msg.timeoutMs || 45000);
    })
      .then(url  => safeRespond({ ok: true, url }))
      .catch(err => safeRespond({ ok: false, error: err.message }));

    // Inject a hidden anchor into the SAP page and click it.
    // • NO rel="noopener noreferrer" — SSO redirect chain needs the Referer header
    //   and opener context to complete correctly.
    // • target=_blank → opens in a new background tab; roadmap viewer stays intact.
    // • Navigation origin is the SAP page (me.sap.com) → same eTLD+1 as the CDN
    //   → browser sends SameSite=Strict cookies → SAP validates session.
    // If SAP's file backend needs a fresh SAML assertion it redirects to
    // accounts.sap.com SSO, which completes silently (user already logged in)
    // and then redirects back → download starts → downloads.onCreated fires.
    chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      func: url => {
        const a = document.createElement('a');
        a.href          = url;
        a.target        = '_blank';
        // No rel=noopener — keep opener context so SSO Referer chain is preserved
        a.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => a.remove(), 3000);
      },
      args: [fileUrl]
    }, () => {
      if (chrome.runtime.lastError) {
        chrome.tabs.onCreated.removeListener(onTabCreated);
        _freshUrlWaiters.delete(fileUrl);
        safeRespond({ ok: false, error: "Could not inject into SAP tab: " + chrome.runtime.lastError.message });
      }
    });

    return true;
  }

  // ── Roadmap Viewer: fetch file bytes via executeScript ───────────────────────
  // Injects fetch directly into EVERY frame (main + all iframes) simultaneously.
  // The frame whose origin matches the file URL will succeed; others will CORS-fail.
  // Returns the first successful base64 result, or {ok:false} with per-frame errors.
  if (msg.action === "fetchFileBytes") {
    chrome.scripting.executeScript({
      target: { tabId: msg.tabId, allFrames: true },
      func: async (fileUrl) => {
        try {
          // Security: only fetch from SAP domains
          const host = new URL(fileUrl).hostname;
          if (host !== 'sap.com' && !host.endsWith('.sap.com')) {
            return { ok: false, error: 'Blocked: not a SAP domain' };
          }
          const resp = await fetch(fileUrl, { credentials: 'include' });
          if (!resp.ok) return { ok: false, error: 'HTTP ' + resp.status, origin: location.origin };
          const buf = await resp.arrayBuffer();
          if (buf.byteLength < 500) return { ok: false, error: 'Response too small (' + buf.byteLength + 'B) — likely auth redirect', origin: location.origin };
          const bytes = new Uint8Array(buf);
          // CHUNK must be a multiple of 3: base64 encodes 3 bytes → 4 chars, so a
          // non-multiple-of-3 chunk makes btoa emit '=' padding MID-STREAM, which
          // corrupts the concatenation and makes atob() throw on the other side.
          const CHUNK = 8190; // 8190 = 3 × 2730
          let b64 = '';
          for (let i = 0; i < bytes.length; i += CHUNK) {
            b64 += btoa(String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + CHUNK, bytes.length))));
          }
          return { ok: true, base64: b64, size: buf.byteLength, origin: location.origin };
        } catch (e) {
          return { ok: false, error: String(e), origin: location.origin };
        }
      },
      args: [msg.url]
    }, results => {
      if (chrome.runtime.lastError) {
        respond({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      const success = results?.find(r => r.result?.ok === true);
      if (success) {
        respond(success.result);
      } else {
        // Collect per-frame errors so popup can show what went wrong
        const errs = (results || [])
          .map(r => (r.result?.origin || '?') + ': ' + (r.result?.error || 'no result'))
          .join(' | ');
        respond({ ok: false, error: errs || 'Fetch failed in all frames' });
      }
    });
    return true;
  }

  // ── Roadmap Viewer: download a file via chrome.downloads ────────────────────
  // chrome.downloads uses the browser's native download engine — no CORS,
  // full session auth, follows redirects. Files land in Downloads/BDCQ/.
  if (msg.action === "downloadRoadmapFile") {
    // Security: reject any non-SAP URL before chrome.downloads touches it
    if (!isSapDomain(msg.url)) {
      respond({ ok: false, error: 'Blocked: URL is not a SAP domain — ' + msg.url });
      return true;
    }
    function sanitizeDlName(name) {
      return name.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, ' ').trim().substring(0, 200);
    }
    chrome.downloads.download({
      url:            msg.url,
      filename:       'BDCQ/' + sanitizeDlName(msg.name),
      conflictAction: 'uniquify',
      saveAs:         false
    }, downloadId => {
      if (chrome.runtime.lastError) {
        respond({ ok: false, error: chrome.runtime.lastError.message });
      } else if (!downloadId) {
        respond({ ok: false, error: 'Download failed to start — URL may have expired. Try scanning again.' });
      } else {
        respond({ ok: true, downloadId });
      }
    });
    return true;
  }

});

// ── Download integrity check ──────────────────────────────────────────────────
// If an xlsx lands at < 15 KB it is almost certainly an HTML redirect/auth page.
// Store a flag so popup can warn the user.
const _badDownloads = new Set(); // downloadIds that look like HTML redirects

chrome.downloads.onChanged.addListener(delta => {
  if (!delta.filename || !delta.fileSize) return;
  const fname = (delta.filename.current || '').toLowerCase();
  const size  = delta.fileSize.current || 0;
  if (/\.xlsx?$/.test(fname) && size > 0 && size < 15000) {
    _badDownloads.add(delta.id);
    // flag silently — popup checks for this on its next query
  }
});

// ── Process Navigator helpers (unchanged from v26) ────────────────────────────

function getSapFrame(tabId, callback) {
  chrome.webNavigation.getAllFrames({ tabId }, frames => {
    if (chrome.runtime.lastError || !frames) {
      callback(null, "No frames found — refresh the page and try again.");
      return;
    }
    const f = frames.find(f => f.url && f.url.includes("pr.alm.me.sap.com") && f.frameId !== 0);
    if (!f) {
      callback(null, "SAP frame not found. Make sure you are on me.sap.com/processnavigator and fully logged in.");
      return;
    }
    callback(f, null);
  });
}

function fetchViaScript(tabId, scopeIds, respond) {
  chrome.tabs.get(tabId, tab => {
    const tabUrl = tab.url || "";
    const verM2  = tabUrl.match(/\/(\d{4})\//);
    const tabVer = verM2 ? verM2[1] : "2608";

    getSapFrame(tabId, async (frame, err) => {
      if (err) { respond({ ok: false, error: err }); return; }

      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId, frameIds: [frame.frameId] },
          func: async (scopeIds, tabVer) => {
            const url  = window.location.href;
            const verM = url.match(/\/(\d{4})\//);
            const ver  = verM ? verM[1] : tabVer;

            const scenR = await fetch(`/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenario?$filter=stableId%20eq%20'EARL_SolS-013'%20and%20targetRelease%20eq%20'${ver}'&$top=1`);
            const scenD = await scenR.json();
            const guid  = scenD.value && scenD.value[0] && scenD.value[0].ID;
            if (!guid) return { ok: false, error: "Cannot find scenario GUID. Make sure you are logged into SAP for Me." };

            const processes = [];
            for (const pid of scopeIds) {
              try {
                const r = await fetch(`/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenarioTranslation(ID=${guid},lanCode='en-US')/solutionProcessTranslation?$filter=country_ID%20eq%20'DE'%20and%20externalId%20eq%20'${pid}'&$top=1`);
                const d = await r.json();
                const p = d.value?.[0];
                if (p) {
                  const tmp      = document.createElement("div");
                  tmp.innerHTML  = p.description || "";
                  const fullDesc = (tmp.textContent || "").trim();
                  const desc     = fullDesc.substring(0, 3000);
                  processes.push({
                    _meta: {
                      processId:                pid,
                      processName:              (p.name || "").replace(/\s*\([A-Z0-9]{2,4}\)\s*$/, "").trim(),
                      version:                  p.solutionScenarioTargetRelease || "2608",
                      region:                   "DE",
                      cloudLabel:               "S/4HANA Cloud Public Edition",
                      cloudType:                "public",
                      isPublicCloud:            true,
                      businessProcessGroupName: p.businessProcessGroupName || "",
                      fetchedAt:                new Date().toISOString(),
                      source:                   "SAP Deck Agent v27 — OData API",
                      extractedChars:           desc.length,
                    },
                    tabs:    { "All Content": { raw: desc, charCount: desc.length } },
                    rawData: { fullPageText: desc, diagrams: [], diagramUrl: null }
                  });
                }
              } catch(e) {
                return { ok: false, error: "Failed to fetch " + pid + ": " + e.message };
              }
            }

            return { ok: true, count: processes.length, processes };
          },
          args: [scopeIds, tabVer]
        });

        const result = results?.[0]?.result;
        if (!result)    { respond({ ok: false, error: "Script returned no result" }); return; }
        if (!result.ok) { respond(result); return; }

        const payload = JSON.stringify({
          type:      "process_data_list",
          fetchedAt: new Date().toISOString(),
          count:     result.count,
          processes: result.processes
        });
        await chrome.storage.local.set({ sapProcessDataList: payload });
        respond({ ok: true, count: result.count });

      } catch(e) {
        respond({ ok: false, error: e.message });
      }
    });
  });
}

function fetchCatalogViaScript(tabId, respond) {
  getSapFrame(tabId, async (frame, err) => {
    if (err) { respond({ ok: false, error: err }); return; }

    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId, frameIds: [frame.frameId] },
        func: async () => {

          // ── L1 domain derivation ─────────────────────────────────────────────
          // name-based override runs first: SAP tags PS items as "Solutions for
          // Specific Industries" or "Human Resources" in businessProcessGroupName,
          // so pattern-matching on the scope item name is the reliable signal.
          function deriveL1(l2, name) {
            const n = (name || "").toLowerCase();
            if (/project.based service|resource management for project|project billing|customer project management|internal project management|intercompany.*project|time recording.*project|staffing.*project/.test(n)) return "Professional Services";
            const t = (l2 || "").toLowerCase();
            if (/finance|accounting|treasury|tax|asset account|controlling|revenue|ledger|payment|invoice|receivable|payable|cash/.test(t)) return "Finance & Controlling";
            if (/procurement|purchasing|sourcing|supplier|vendor|ariba|spend/.test(t))                                                       return "Sourcing & Procurement";
            if (/manufactur|production|quality|plant|shop floor|mrp|capacity/.test(t))                                                       return "Manufacturing";
            if (/supply chain|warehouse|inventory|logistics|transport|shipping|import|export/.test(t))                                       return "Supply Chain";
            if (/sales|customer order|pricing|billing|crm|lead|opportunity|trade/.test(t))                                                   return "Sales & Distribution";
            if (/service|field service|customer service|after.?sales/.test(t))                                                               return "Customer Service";
            if (/human resource|hr |payroll|talent|workforce|employee|benefit|time management|personnel/.test(t))                            return "Human Resources";
            if (/project|professional service|ps |time.?expense/.test(t))                                                                    return "Professional Services";
            if (/real estate|facility|lease/.test(t))                                                                                        return "Real Estate";
            if (/r&d|engineering|product develop|innovat/.test(t))                                                                           return "R&D & Engineering";
            if (/retail/.test(t))                                                                                                            return "Retail";
            if (/public sector|government/.test(t))                                                                                          return "Public Sector";
            if (/asset management|maintenance|pm |equipment/.test(t))                                                                        return "Asset Management";
            return "Cross-Functional";
          }

          // ── HTML description → structured L1–L4 sections ─────────────────────
          function parseDesc(html) {
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
                if (section === "steps")         r.keyProcessSteps.push(text);
                else if (section === "benefits") r.businessBenefits.push(text);
                else if (section === "features") r.keyFeatures.push(text);
              } else if (tag === "p") {
                if (el.closest("li")) return;
                if (section === "overview")      r.overview = (r.overview + " " + text).trim();
                else if (section === "features") r.scope = (r.scope + " " + text).trim();
              }
            });
            return r;
          }

          // ── Step 1: Resolve latest scenario GUID ─────────────────────────────
          const scenR = await fetch("/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenario?$filter=stableId%20eq%20'EARL_SolS-013'&$orderby=targetRelease%20desc&$top=1");
          const scenD = await scenR.json();
          const guid    = scenD.value?.[0]?.ID;
          const version = scenD.value?.[0]?.targetRelease || "2608";
          if (!guid) return { ok: false, error: "Cannot find scenario GUID. Make sure you are logged into SAP for Me." };

          // ── Step 2: Bulk list ─────────────────────────────────────────────────
          const listR   = await fetch(`/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenarioTranslation(ID=${guid},lanCode='en-US')/solutionProcessTranslation?$count=true&$orderby=name&$filter=country_ID%20eq%20'DE'&$skip=0&$top=700`);
          const listD   = await listR.json();
          const rawList = listD.value || [];
          const total   = rawList.length;

          // ── Step 3: Batch-fetch descriptions if not in bulk response ──────────
          const bulkHasDesc = total > 0 && (rawList[0].description || "").length > 20;
          if (!bulkHasDesc) {
            const BATCH = 25;
            for (let i = 0; i < total; i += BATCH) {
              await Promise.allSettled(rawList.slice(i, i + BATCH).map(async (p, idx) => {
                try {
                  const exId = p.externalId || "";
                  if (!exId) return;
                  const dr = await fetch(`/ui/earl-pn-ui/v1/odata/v4/EAXService/SolutionScenarioTranslation(ID=${guid},lanCode='en-US')/solutionProcessTranslation?$filter=country_ID%20eq%20'DE'%20and%20externalId%20eq%20'${exId}'&$top=1`);
                  const dd = await dr.json();
                  if (dd.value?.[0]) Object.assign(rawList[i + idx], dd.value[0]);
                } catch(e) {}
              }));
            }
          }

          // ── Step 4a: KDD catalog — original format (id, lob, name, description) ──
          // Keeps raw HTML description so the KDD agent can parse it itself.
          // lob: use name-based PS override — SAP tags PS items as "Solutions for
          // Specific Industries" or "Human Resources" in businessProcessGroupName.
          const PS_NAME_RE = /project.based service|resource management for project|project billing|customer project management|internal project management|intercompany.*project|time recording.*project|staffing.*project/i;
          const kddProcesses = rawList.map(p => {
            const nm   = p.name || "";
            const lp   = nm.lastIndexOf("(");
            const rp   = nm.lastIndexOf(")");
            const id   = (lp > 0 && rp > lp) ? nm.substring(lp+1, rp).trim() : (p.externalId || "");
            const name = lp > 0 ? nm.substring(0, lp).trim() : nm.trim();
            const lob  = PS_NAME_RE.test(name) ? "Professional Services" : (p.businessProcessGroupName || "Other");
            return { description: p.description || "", id, lob, name };
          }).filter(p => p.id);

          const kddCatalog = {
            type:        "scope_catalog",
            version,
            country:     "DE",
            extractedAt: new Date().toISOString(),
            total:       kddProcesses.length,
            processes:   kddProcesses,
          };

          // ── Step 4b: F2S catalog — enriched L1–L4 format ─────────────────────
          const f2sProcesses = rawList.map(p => {
            const nm   = p.name || "";
            const lp   = nm.lastIndexOf("(");
            const rp   = nm.lastIndexOf(")");
            const id   = (lp > 0 && rp > lp) ? nm.substring(lp+1, rp).trim() : (p.externalId || "");
            const name = lp > 0 ? nm.substring(0, lp).trim() : nm.trim();
            const l2   = p.businessProcessGroupName || "Other";
            const l1   = deriveL1(l2, name);
            const desc = parseDesc(p.description || "");
            return {
              id, name, l1, l2, l3: name,
              l4:               desc.keyProcessSteps,
              overview:         desc.overview,
              businessBenefits: desc.businessBenefits,
              keyFeatures:      desc.keyFeatures,
              scope:            desc.scope,
              fullDescription:  desc.fullText,
              version:          p.solutionScenarioTargetRelease || version,
            };
          }).filter(p => p.id);

          // ── Step 5: Hierarchy ─────────────────────────────────────────────────
          const hierarchy = {};
          f2sProcesses.forEach(p => {
            if (!hierarchy[p.l1])        hierarchy[p.l1] = {};
            if (!hierarchy[p.l1][p.l2]) hierarchy[p.l1][p.l2] = [];
            hierarchy[p.l1][p.l2].push(p.id);
          });

          const f2sCatalog = {
            type:          "f2s_process_catalog",
            schemaVersion: "2.0",
            sapVersion:    version,
            version,
            extractedAt:   new Date().toISOString(),
            country:       "DE",
            cloudEdition:  "S/4HANA Cloud Public Edition",
            total:         f2sProcesses.length,
            hierarchy,
            processes:     f2sProcesses,
          };

          // ── Step 6: Persist KDD catalog (scope-catalog.json) ─────────────────
          // Written here inside the frame context where chrome.storage is confirmed to work.
          await chrome.storage.local.set({ sapScopeCatalog: JSON.stringify(kddCatalog) });

          // Return F2S catalog WITHOUT fullDescription (that's in KDD catalog already).
          // background.js writes sapScopeCatalogF2S outside the frame to avoid any size limits.
          const l1Count = Object.keys(hierarchy).length;
          const l2Count = Object.values(hierarchy).reduce((s, v) => s + Object.keys(v).length, 0);
          const l4Count = f2sProcesses.reduce((s, p) => s + p.l4.length, 0);

          // Strip fullDescription before returning to keep IPC payload small
          const f2sLean = { ...f2sCatalog, processes: f2sProcesses.map(p => {
            const { fullDescription, ...rest } = p; return rest;
          }) };

          return { ok: true, count: f2sProcesses.length, version, l1Count, l2Count, l4Count, f2sCatalog: f2sLean };
        },
        args: []
      });

      const result = results?.[0]?.result;
      if (!result)    { respond({ ok: false, error: "Script returned no result" }); return; }
      if (!result.ok) { respond(result); return; }

      // Write F2S catalog from background.js — outside the frame, no size concerns
      if (result.f2sCatalog) {
        await chrome.storage.local.set({ sapScopeCatalogF2S: JSON.stringify(result.f2sCatalog) });
      }

      // Respond with counts — popup displays them directly
      const { f2sCatalog: _dropped, ...meta } = result;
      respond(meta);

    } catch(e) {
      respond({ ok: false, error: e.message });
    }
  });
}
