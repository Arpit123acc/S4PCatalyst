// fulcrum-notify-client.js — Fulcrum auto-email notifier (browser side)
// BDCQ notification is sent directly from bdcqEnrich() in app.js (exact count known there).
// This file provides: showToast() for visual feedback on that notification.
// Does NOT modify any existing Fulcrum files.

(function () {
  "use strict";

  // ── Tiny toast (bottom-right, 4 seconds, non-blocking) ───────────────────
  window._fulcrumShowToast = function showToast(message) {
    const t = document.createElement("div");
    t.style.cssText = `
      position:fixed; bottom:20px; right:20px; z-index:99999;
      background:#0f172a; color:#f8fafc; border-radius:8px;
      padding:10px 16px; font-family:Segoe UI,Arial,sans-serif;
      font-size:13px; box-shadow:0 4px 20px rgba(0,0,0,.3);
      opacity:0; transition:opacity .3s ease; pointer-events:none;
    `;
    t.textContent = message;
    document.body.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = "1"; });
    setTimeout(() => {
      t.style.opacity = "0";
      setTimeout(() => t.remove(), 400);
    }, 4000);
  };

  // KDD notifications are handled by the server-side file watcher (notify.js --server)
  // which watches the output/ folder for KDD Excel files.

  console.log("[Fulcrum Notify] 🔔 Auto-notify active — BDCQ emails on enrich completion, KDD via file watcher");
})();
