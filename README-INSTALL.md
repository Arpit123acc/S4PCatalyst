# Quick Start
**SAP S/4HANA Cloud Public Edition · Explore Phase Accelerator · v1.1.0**

> 📖 **Full instructions are in [USER-GUIDE.md](USER-GUIDE.md)** — installation, both workflows, and troubleshooting. This page is the 60-second version.

---

## 1. Install prerequisites (once)
1. **Node.js** — install the LTS from **https://nodejs.org**. Verify: `node --version`.
2. **Claude CLI** — install from **https://claude.ai/code** and sign in. Verify: `claude --version`.
3. **Chrome** — needed for the SAP Deck Agent extension.

## 2. Set up the tool (once)
1. Unzip this folder somewhere permanent (not inside OneDrive).
2. Double-click **`SETUP.bat`** — installs dependencies (needs internet, ~1 min).

## 3. Install the Chrome extension (once)
1. Unzip `SAPDeckAgent-ChromeExtension.zip`.
2. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick the unzipped folder.
   *(After any update, click ↻ reload on the extension.)*

## 4. Run it
- Double-click **`START.bat`**.
- Wait for the banner, then use **http://localhost:8321** (Chrome opens it automatically).
- Close the console window to stop.

---

## The two workflows (short version)
- **BDCQ:** SAP Roadmap Viewer → extension **Scan** → **Save BDCQ Files** → in the app, **BDCQ Agent** page → pick country + industry → **Enrich** → export.
- **KDD:** SAP Process Navigator → extension **Load All Processes** → **Save & Generate** → in the app, **KDD Generator** page → select scope items → **Generate** → export.

Generated Excel files land in the **`output\`** folder.

---

## Most common issue
**"Claude CLI not found"** — confirm `claude --version` works in a new terminal, then restart `START.bat`. See [USER-GUIDE.md](USER-GUIDE.md#13-troubleshooting) for the rest.

---

*For internal Accenture use only.*
