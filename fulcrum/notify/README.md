# Fulcrum Notify — Standalone Email Notifier

Sends email via **Outlook desktop app** (no SMTP credentials, no IT restrictions).
Works because you're already signed into Outlook on this machine.

---

## Test it now

Open a terminal in the `notify/` folder and run:

```
node notify.js
```

You should get an email at neha.a.bhardwaj@accenture.com within seconds.

---

## Usage modes

### 1. One-shot (command line)
```
node notify.js test        "Test notification"
node notify.js bdcq        "Finance domain enrichment complete — 47 questions ready"
node notify.js kdd         "KDD log generated for Acme Corp · BD6, J59, BEI"
```

### 2. Server mode (runs in background, receives HTTP calls)
```
node notify.js --server
```

Then trigger from anywhere with a POST:
```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8323/notify" `
  -ContentType "application/json" `
  -Body '{"type":"bdcq","detail":"Finance domain complete"}'
```

Or from Node.js / fetch:
```js
fetch("http://127.0.0.1:8323/notify", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ type: "bdcq", detail: "Finance enrichment done" })
});
```

---

## Email types

| type | Subject |
|------|---------|
| `bdcq` | ✅ Fulcrum — BDCQ Enrichment Complete |
| `kdd`  | ✅ Fulcrum — KDD Generation Complete |
| anything else | ⚡ Fulcrum — Run Complete |

---

## Troubleshooting

**"Outlook COM failed"** — Make sure Outlook desktop app is open and signed in.  
**No email received** — Check Sent Items in Outlook to confirm it was sent.  
**Port 8323 in use** — Change `NOTIFY_PORT` at the top of `notify.js`.

---

No npm install needed — uses only Node.js built-ins.
