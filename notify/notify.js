// notify.js — Fulcrum Standalone Email Notifier
// Auto-detects who is running (reads current Outlook user — no config needed)
// Uses PowerShell + Outlook COM — no SMTP, no IT restrictions
//
// Modes:
//   node notify.js                  → send test email to detected user
//   node notify.js --server         → HTTP server on :8323 + watch output/ for KDD
//   node notify.js bdcq "detail"    → one-shot BDCQ notification
//   node notify.js kdd  "detail"    → one-shot KDD  notification

"use strict";

const { execFile } = require("child_process");
const http         = require("http");
const fs           = require("fs");
const path         = require("path");
const os           = require("os");

const NOTIFY_PORT  = 8323;
const FULCRUM_URL  = "http://127.0.0.1:8321";
const OUTPUT_DIR   = path.resolve(__dirname, "..", "output");   // KDD exports land here
const CONFIG_FILE  = path.join(__dirname, "config.json");

// ── 1. Auto-detect user email from Outlook ────────────────────────────────────
function detectUserEmail() {
  return new Promise((resolve) => {
    const ps = `(New-Object -ComObject Outlook.Application).Session.CurrentUser.Address`;
    execFile(
      "powershell.exe",
      ["-NonInteractive", "-NoProfile", "-Command", ps],
      { timeout: 8000 },
      (err, stdout) => {
        const email = (stdout || "").trim();
        if (!err && email && email.includes("@")) {
          resolve(email);
        } else {
          resolve(null); // Outlook not open or COM failed
        }
      }
    );
  });
}

// Detect recipient — manual config.json overrides, otherwise always live from Outlook
async function getRecipient() {
  // Manual override only — never auto-created, so it can't carry over from another user
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
    if (cfg.email && cfg.email.includes("@")) return cfg.email;
  } catch { /**/ }

  // Always detect live from local Outlook — never cache to disk
  const detected = await detectUserEmail();
  if (detected) {
    console.log(`[Fulcrum Notify] 👤 User detected: ${detected}`);
    return detected;
  }

  return null; // Can't determine — caller will handle
}

// ── 2. Send email via Outlook COM ─────────────────────────────────────────────
function sendEmail({ to, subject, htmlBody }) {
  return new Promise((resolve, reject) => {
    // Escape for PowerShell here-string (@ signs need backtick)
    const safeBody = htmlBody.replace(/@/g, '`@');
    const safeSubj = subject.replace(/"/g, '\\"').replace(/`/g, '``');

    const ps = `
$o = New-Object -ComObject Outlook.Application
$m = $o.CreateItem(0)
$m.To = "${to}"
$m.Subject = "${safeSubj}"
$m.HTMLBody = @"
${safeBody}
"@
$m.Send()
Write-Output "SENT_OK"
`;

    execFile(
      "powershell.exe",
      ["-NonInteractive", "-NoProfile", "-Command", ps],
      { timeout: 20000 },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        if ((stdout || "").includes("SENT_OK")) {
          console.log(`[Fulcrum Notify] ✅ Email sent → ${to}`);
          resolve(true);
        } else {
          reject(new Error(`Outlook did not confirm. Output: ${stdout}`));
        }
      }
    );
  });
}

// ── 3. Email templates ────────────────────────────────────────────────────────
function buildEmail(type, detail, recipient) {
  const d    = new Date();
  const time = d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" });
  const date = d.toLocaleDateString("en-GB",  { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" });

  // First name only — skip middle initials (single letters)
  const firstName = recipient
    ? recipient.split("@")[0].split(".").filter(p => p.length > 1)[0] || recipient.split("@")[0]
    : "there";
  const name = firstName.charAt(0).toUpperCase() + firstName.slice(1).toLowerCase();

  // ── Shared layout builder ──────────────────────────────────────────────────
  const layout = ({ headerColor, heading, stat1, stat2, stat3, stat4, infoLines, ctaLabel }) => `
<!DOCTYPE html>
<html xmlns:v="urn:schemas-microsoft-com:vml">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background-color:#f4f4f8">
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f8">
<tr><td align="center" style="padding:24px 12px">
<table width="580" cellpadding="0" cellspacing="0" border="0" style="max-width:580px;width:100%;font-family:'Segoe UI',Helvetica,Arial,sans-serif">

  <!-- ═══ HEADER (logo row) ═══ -->
  <tr>
    <td bgcolor="${headerColor}" style="background-color:${headerColor};padding:22px 28px 0 28px">
      <table cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td valign="middle" style="padding-right:14px">
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="44" height="44" bgcolor="#ffffff" valign="middle" align="center"
                    style="background-color:#ffffff;width:44px;height:44px;text-align:center;
                           font-size:22px;line-height:44px">
                  &#9889;
                </td>
              </tr>
            </table>
          </td>
          <td valign="middle">
            <p style="margin:0;color:#ffffff;font-size:17px;font-weight:700;line-height:1.2">Fulcrum</p>
            <p style="margin:3px 0 0;color:#c4b5fd;font-size:11px">SAP S/4HANA Cloud PE &nbsp;&middot;&nbsp; Explore Phase</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ HEADER (greeting + badge + heading — still purple) ═══ -->
  <tr>
    <td bgcolor="${headerColor}" style="background-color:${headerColor};padding:20px 28px 28px 28px">

      <!-- greeting -->
      <p style="margin:0 0 16px;color:#ede9fe;font-size:14px">Hi ${name},</p>

      <!-- badge: ● COMPLETED -->
      <table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px">
        <tr>
          <td bgcolor="#ffffff" style="background-color:#ffffff;padding:5px 14px 5px 10px;border-radius:20px">
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="color:#22c55e;font-size:12px;padding-right:6px">&#9679;</td>
                <td style="color:#16a34a;font-size:11px;font-weight:700;letter-spacing:1px;
                           text-transform:uppercase;white-space:nowrap">COMPLETED</td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- main heading -->
      <p style="margin:0;color:#ffffff;font-size:26px;font-weight:700;line-height:1.3">${heading}</p>
    </td>
  </tr>

  <!-- ═══ STATS GRID ═══ -->
  <tr>
    <td bgcolor="#ffffff" style="background-color:#ffffff;padding:0 28px 20px">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="border-collapse:collapse;border:1px solid #e5e7eb">
        <!-- row 1 -->
        <tr>
          <td width="50%" valign="top"
              style="padding:14px 18px;border-right:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb">
            <p style="margin:0 0 4px;color:#9ca3af;font-size:10px;font-weight:700;
                      letter-spacing:1px;text-transform:uppercase">${stat1.label}</p>
            <p style="margin:0;color:${headerColor};font-size:20px;font-weight:700">${stat1.value}</p>
          </td>
          <td width="50%" valign="top"
              style="padding:14px 18px;border-bottom:1px solid #e5e7eb">
            <p style="margin:0 0 4px;color:#9ca3af;font-size:10px;font-weight:700;
                      letter-spacing:1px;text-transform:uppercase">${stat2.label}</p>
            <p style="margin:0;color:#111827;font-size:15px;font-weight:600">${stat2.value}</p>
          </td>
        </tr>
        <!-- row 2 -->
        <tr>
          <td width="50%" valign="top"
              style="padding:14px 18px;border-right:1px solid #e5e7eb">
            <p style="margin:0 0 4px;color:#9ca3af;font-size:10px;font-weight:700;
                      letter-spacing:1px;text-transform:uppercase">${stat3.label}</p>
            <p style="margin:0;color:#111827;font-size:15px;font-weight:600">${stat3.value}</p>
          </td>
          <td width="50%" valign="top"
              style="padding:14px 18px">
            <p style="margin:0 0 4px;color:#9ca3af;font-size:10px;font-weight:700;
                      letter-spacing:1px;text-transform:uppercase">${stat4.label}</p>
            <p style="margin:0;color:${headerColor};font-size:15px;font-weight:600">${stat4.value}</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ INFO BOX ═══ -->
  <tr>
    <td bgcolor="#ffffff" style="background-color:#ffffff;padding:0 28px 24px">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td bgcolor="#f5f3ff" style="background-color:#f5f3ff;padding:16px 18px;border-radius:6px">
            ${infoLines.map(l =>
              `<p style="margin:0 0 8px;color:#5b21b6;font-size:13px;line-height:1.7">${l}</p>`
            ).join("")}
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ CTA BUTTON ═══ -->
  <tr>
    <td bgcolor="#ffffff" style="background-color:#ffffff;padding:0 28px 32px">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td bgcolor="${headerColor}" align="center"
              style="background-color:${headerColor};border-radius:8px;padding:0">
            <a href="${FULCRUM_URL}"
               style="display:block;padding:14px 24px;color:#ffffff;font-size:15px;
                      font-weight:600;text-decoration:none;text-align:center;
                      letter-spacing:.3px">${ctaLabel}</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ FOOTER ═══ -->
  <tr>
    <td bgcolor="#f9fafb" style="background-color:#f9fafb;border-top:1px solid #e5e7eb;
                                 padding:14px 28px">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="color:#d1d5db;font-size:11px">Fulcrum v1</td>
          <td align="right" style="color:#d1d5db;font-size:11px">${recipient || "Accenture"}</td>
        </tr>
      </table>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>`;

  // ── BDCQ template ──────────────────────────────────────────────────────────
  if (type === "bdcq") {
    // Parse count from detail string if present e.g. "47 questions enriched…"
    const countMatch = (detail || "").match(/^(\d+)/);
    const count = countMatch ? countMatch[1] : "—";
    return {
      subject: `✅ Fulcrum — BDCQ Enrichment Complete`,
      htmlBody: layout({
        headerColor: "#5b21b6",
        heading:     "BDCQ enrichment complete",
        stat1: { label: "Questions Enriched", value: count,             accent: true  },
        stat2: { label: "Completed At",        value: `${time} IST`,    accent: false },
        stat3: { label: "Date",                value: date,             accent: false },
        stat4: { label: "Status",              value: "Ready to review", accent: true },
        infoLines: [
          `${count !== "—" ? count : "Your"} questions enriched with SAP standard values and watch-out points for your BDCQ session.`,
          "Review the enriched output in the BDCQ Editor, approve decisions, and proceed to the fit-to-standard workshop."
        ],
        ctaLabel: "Open Fulcrum to review"
      })
    };
  }

  // ── KDD template ───────────────────────────────────────────────────────────
  if (type === "kdd") {
    const countMatch = (detail || "").match(/(\d+)\s+scope/i);
    const items = countMatch ? countMatch[1] : "—";
    return {
      subject: `📋 Fulcrum — KDD Log Ready`,
      htmlBody: layout({
        headerColor: "#5b21b6",
        heading:     "KDD log generated",
        stat1: { label: "Scope Items",  value: items,             accent: true  },
        stat2: { label: "Completed At", value: `${time} IST`,     accent: false },
        stat3: { label: "Date",         value: date,              accent: false },
        stat4: { label: "Status",       value: "Ready to review", accent: true  },
        infoLines: [
          `KDD log generated${items !== "—" ? ` for ${items} scope item${items !== "1" ? "s" : ""}` : ""} with 15 design decisions each.`,
          "Review the KDD log in the Editor, complete the WRICEF inventory, and export for client sign-off."
        ],
        ctaLabel: "Open Fulcrum to review"
      })
    };
  }

  // ── Generic fallback ───────────────────────────────────────────────────────
  return {
    subject: `⚡ Fulcrum — Run Complete`,
    htmlBody: layout({
      headerColor: "#5b21b6",
      heading:     "Task complete",
      stat1: { label: "Status",       value: "Done",          accent: true  },
      stat2: { label: "Completed At", value: `${time} IST`,   accent: false },
      stat3: { label: "Date",         value: date,            accent: false },
      stat4: { label: "Next Step",    value: "Open Fulcrum",  accent: true  },
      infoLines: [ detail || "A Fulcrum task has completed successfully." ],
      ctaLabel: "Open Fulcrum"
    })
  };
}

// ── 4. KDD file watcher (watches output/ folder) ──────────────────────────────
const _notifiedFiles = new Set(); // files already processed (watcher dedup)
const _serverClaimed = new Set(); // files server.js explicitly notified (with scope detail)

function watchKddOutput(getRecipientFn) {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  console.log(`[Fulcrum Notify] 👀 Watching ${OUTPUT_DIR} for new KDD exports...`);

  // Seed existing files so we don't alert on old ones
  fs.readdirSync(OUTPUT_DIR).forEach(f => _notifiedFiles.add(f));

  fs.watch(OUTPUT_DIR, async (event, filename) => {
    if (!filename || _notifiedFiles.has(filename)) return;
    if (!/\.(xlsx|xls)$/i.test(filename)) return;
    if (!/KDD/i.test(filename)) return;

    // Mark immediately — fs.watch fires 2-3 events per write; only the first proceeds
    _notifiedFiles.add(filename);

    // Wait 4s — server.js sends /notify POST immediately after writing the file,
    // so it always arrives within this window and sets _serverClaimed.
    await new Promise(r => setTimeout(r, 4000));

    // If server already sent a richer email (with scope IDs), skip the watcher email
    if (_serverClaimed.has(filename)) {
      console.log(`[Fulcrum Notify] ⏭  ${filename} already notified by server — skipping watcher email`);
      return;
    }

    const fullPath = path.join(OUTPUT_DIR, filename);
    if (!fs.existsSync(fullPath)) return;
    console.log(`[Fulcrum Notify] 📁 New KDD file detected: ${filename}`);

    try {
      const recipient = await getRecipientFn();
      if (!recipient) {
        console.error("[Fulcrum Notify] ❌ Cannot determine recipient — is Outlook open?");
        return;
      }
      const detail = `KDD log <strong>${filename}</strong> is ready in the output/ folder.`;
      const { subject, htmlBody } = buildEmail("kdd", detail, recipient);
      await sendEmail({ to: recipient, subject, htmlBody });
    } catch (e) {
      console.error("[Fulcrum Notify] ❌ Send failed:", e.message);
    }
  });
}

// ── 5. HTTP server (receives BDCQ trigger from browser) ───────────────────────
function startServer() {
  const CLIENT_JS = path.join(__dirname, "fulcrum-notify-client.js");

  const server = http.createServer((req, res) => {
    // CORS — Fulcrum UI is on 8321, notifier is on 8323
    res.setHeader("Access-Control-Allow-Origin",  "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");

    if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

    // Serve the browser-side client script
    if (req.method === "GET" && req.url === "/notify-client.js") {
      try {
        const js = fs.readFileSync(CLIENT_JS, "utf8");
        res.writeHead(200, { "Content-Type": "application/javascript" });
        res.end(js);
      } catch {
        res.writeHead(404); res.end("// notify client not found");
      }
      return;
    }

    if (req.method !== "POST" || req.url !== "/notify") {
      res.writeHead(404); res.end("Not found"); return;
    }

    let body = "";
    req.on("data", d => { body += d; });
    req.on("end", async () => {
      let payload = {};
      try { payload = JSON.parse(body); } catch { /**/ }

      const { type = "test", detail = "", to: overrideTo, claimedFile } = payload;
      // Mark in both sets: watcher won't start a new send, and in-flight watcher will skip
      if (claimedFile) {
        _notifiedFiles.add(claimedFile);
        _serverClaimed.add(claimedFile);
      }

      try {
        const recipient = overrideTo || await getRecipient();
        if (!recipient) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: false, error: "Could not detect user email — is Outlook open?" }));
          return;
        }
        const { subject, htmlBody } = buildEmail(type, detail, recipient);
        await sendEmail({ to: recipient, subject, htmlBody });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, sentTo: recipient }));
      } catch (e) {
        console.error("[Fulcrum Notify] ❌", e.message);
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
  });

  server.listen(NOTIFY_PORT, "127.0.0.1", () => {
    console.log(`[Fulcrum Notify] 🔔 Server ready  → http://127.0.0.1:${NOTIFY_PORT}/notify`);
  });

  // Also start the KDD file watcher
  watchKddOutput(getRecipient);
}

// ── 6. Entry point ────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

if (args.includes("--server")) {
  // Detect user eagerly at startup so we know it works
  getRecipient().then(email => {
    if (email) {
      console.log(`[Fulcrum Notify] 👤 Notifications will go to: ${email}`);
    } else {
      console.warn("[Fulcrum Notify] ⚠️  Could not auto-detect email — is Outlook open?");
      console.warn("                    You can set it manually: edit notify/config.json");
    }
    startServer();
  });

} else {
  // One-shot mode
  const type   = args[0] || "test";
  const detail = args[1] || "Test notification from Fulcrum — your run is complete!";

  getRecipient().then(async recipient => {
    if (!recipient) {
      console.error("[Fulcrum Notify] ❌ Cannot detect user email. Is Outlook open?");
      console.error("   Or create notify/config.json: { \"email\": \"you@accenture.com\" }");
      process.exit(1);
    }
    console.log(`[Fulcrum Notify] Sending '${type}' notification to ${recipient}...`);
    const { subject, htmlBody } = buildEmail(type, detail, recipient);
    try {
      await sendEmail({ to: recipient, subject, htmlBody });
      process.exit(0);
    } catch (e) {
      console.error("[Fulcrum Notify] ❌", e.message);
      process.exit(1);
    }
  });
}
