# User Guide
**SAP S/4HANA Cloud Public Edition · Explore Phase Accelerator**
Version 1.1.0 · For internal Accenture use

---

## Table of Contents
1. [What This Tool Does](#1-what-this-tool-does)
2. [How It Works (the big picture)](#2-how-it-works-the-big-picture)
3. [System Requirements](#3-system-requirements)
4. [Prerequisites](#4-prerequisites)
5. [Installation](#5-installation)
6. [Starting and Stopping](#6-starting-and-stopping)
7. [The Web Interface — a Tour](#7-the-web-interface--a-tour)
8. [Workflow A — BDCQ Enrichment](#8-workflow-a--bdcq-enrichment)
9. [Workflow B — KDD Generation](#9-workflow-b--kdd-generation)
10. [Working in the KDD Editor](#10-working-in-the-kdd-editor)
11. [Gap Analysis](#11-gap-analysis)
12. [Managing Projects](#12-managing-projects)
13. [Output Files](#13-output-files)
14. [Data Files and Refresh Cadence](#14-data-files-and-refresh-cadence)
15. [Settings and MCP Server](#15-settings-and-mcp-server)
16. [Security and Data Handling](#16-security-and-data-handling)
17. [Updating, Uninstalling, and Backups](#17-updating-uninstalling-and-backups)
18. [Known Limitations](#18-known-limitations)
19. [Troubleshooting](#19-troubleshooting)
20. [FAQ](#20-faq)
21. [Glossary](#21-glossary)
22. [Getting Help](#22-getting-help)

---

## 1. What This Tool Does

This is a workshop accelerator for the **SAP Activate "Explore" phase** on S/4HANA Cloud Public Edition projects. It runs on your own laptop as a local web app and uses **Claude** as its AI engine. It has two main jobs:

| Agent | What it produces |
|---|---|
| **BDCQ Enricher** | Takes SAP's Business Driven Configuration Questionnaire (BDCQ) and enriches every question with **country localisation** flags, **industry** context, and **Cloud PE constraints** — so you walk into the workshop with mandatory statutory items pre-answered. |
| **KDD Generator** | Takes a list of SAP scope items and generates a **Key Design Decisions (KDD)** log — Cloud PE-compliant design questions with options, RICEF/WRICEF classification, owner and effort — exported to **Excel** and **PowerPoint**. |

Everything runs **locally on `http://localhost:8321`**.

> **Cloud PE only.** The tool strictly enforces S/4HANA **Cloud Public Edition** standards. It will not produce on-premise / ECC concepts (ABAP, BAdI, SE16, SAP GUI, etc.) — configuration is expressed only through SAP Fiori apps and SAP BTP.

---

## 2. How It Works (the big picture)

Three pieces work together:

```
   ┌─────────────────────┐        ┌──────────────────────┐        ┌────────────────┐
   │  Chrome Extension    │  gets  │  Local Web App       │  uses  │  Claude CLI    │
   │  "SAP Deck Agent"    │ ─────► │  localhost:8321      │ ─────► │  (AI engine)   │
   │  scans SAP portals   │  data  │  BDCQ + KDD agents   │        │                │
   └─────────────────────┘        └──────────────────────┘        └────────────────┘
```

1. The **Chrome extension** pulls the raw source data out of SAP's own portals (Roadmap Viewer for BDCQ files, Process Navigator for the scope catalog) and saves it into your project folder.
2. The **local web app** reads that data, and when you click *Enrich*, *Generate*, *Quality Check*, or *Gap Analysis* it calls **Claude** to do the intelligent work.
3. **Claude CLI** must be installed and signed in — the web app shells out to it.

You set up all three once. After that, day-to-day use is just the web app plus the extension.

---

## 3. System Requirements

| Item | Requirement |
|---|---|
| **Operating system** | Windows 10 or 11 (the launchers are `.bat` files; Windows only) |
| **Browser** | Google Chrome (required for the SAP Deck Agent extension) |
| **Disk space** | ~1 MB for the zip; grows to **~70 MB** after setup installs its libraries locally |
| **Memory** | Any modern laptop (2 GB free RAM is plenty) |
| **Internet** | Required for: one-time setup (`npm install`), all Claude AI calls, and reaching SAP portals |
| **Accounts** | An **Anthropic/Claude** login and an active **SAP for Me** login |

> The app listens on **127.0.0.1 only** (your machine's loopback address). It is **not** reachable by anyone else on the network. See [Security and Data Handling](#16-security-and-data-handling).

---

## 4. Prerequisites

Install these **once**, in this order (~10 minutes).

### 4.1 Node.js (required)
The web app runs on Node.js.
1. Go to **https://nodejs.org** → download the **LTS** installer for Windows.
2. Run it, accept the defaults.
3. Verify: open a **new** Command Prompt → `node --version` → you should see `v20.x` or higher.

### 4.2 Claude CLI (required)
The AI engine. Nothing generates without it.
1. Go to **https://claude.ai/code** → download the Windows installer.
2. Sign in with your Anthropic account (or Accenture SSO if provisioned).
3. Verify: in a **new** Command Prompt → `claude --version` → you should see a version number.

> As long as `claude --version` works in your terminal, the app finds it automatically — it checks the standard install locations and your system PATH. Custom install location? See [Troubleshooting](#19-troubleshooting).

### 4.3 Google Chrome (required for SAP data)
The SAP Deck Agent extension is Chrome-only. You also need an active SAP for Me login.

---

## 5. Installation

### 5.1 Unzip the tool
1. Copy `Fulcrum-v1.1.0-win.zip` somewhere permanent, e.g. `C:\Tools\ExploreAccelerator\`.
   *(Avoid OneDrive/Desktop sync folders — they can lock files while the app runs.)*
2. Right-click → **Extract All**.

### 5.2 Run one-time setup
Double-click **`SETUP.bat`**.
- Downloads the app's dependencies (needs internet, ~1 minute).
- If it reports *"npm install failed"*, Node.js isn't installed correctly — redo [4.1](#41-nodejs-required).
- When it says **"Setup complete. Run START.bat to launch."**, you're done.

### 5.3 Install the Chrome extension
1. Unzip **`SAPDeckAgent-ChromeExtension.zip`** (inside the tool folder) into its own folder.
2. Chrome → `chrome://extensions`.
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked** → select the unzipped extension folder.
5. **SAP Deck Agent** appears in your extensions list. Pin it for easy access.

> **After an update:** if you receive a new extension build, go to `chrome://extensions` and click **↻ reload** on SAP Deck Agent (or remove and Load unpacked again). Chrome caches extension code, so a reload is required for fixes to take effect.

---

## 6. Starting and Stopping

**Start:** double-click **`START.bat`**.
- A console window opens. If dependencies are missing it runs setup automatically.
- When you see this banner it's ready:
  ```
  ╔═══════════════════════════════════════════════╗
  ║  Fulcrum · SAP S/4HANA Cloud PE               ║
  ║  http://127.0.0.1:8321                         ║
  ╚═══════════════════════════════════════════════╝
  ```
- Chrome opens automatically to **http://localhost:8321** after a few seconds. If not, open it manually.

**Stop:** close the console window (or press `Ctrl+C` in it).

> **Keep the console window open** while you work — it *is* the app. Minimise it, don't close it.

---

## 7. The Web Interface — a Tour

The sidebar follows the Explore-phase journey:

| Section | Page | What it's for |
|---|---|---|
| **Workspace** | Home | Landing page and status |
| **Explore Agents** | ① **BDCQ Agent** | Enrich the BDCQ (Workflow A) |
| | ② Fit-to-Standard | *Coming soon* |
| | ③ **KDD Generator** | Generate the KDD log (Workflow B) |
| | ③ **KDD Editor** | Review, edit, quality-check, and export decisions |
| | ③ **KDD Analysis** | AI gap analysis and WRICEF effort view |
| **Reference** | Scope Catalog | Browse the SAP process catalog you loaded |
| | Skills | What each agent does |
| | Output Files | Download everything you've generated |
| **System** | MCP Server | Advanced: let Claude read the data directly |
| | Settings | Configuration |

The pages you'll use most: **BDCQ Agent**, **KDD Generator**, and **KDD Editor**.

---

## 8. Workflow A — BDCQ Enrichment

**Goal:** turn SAP's raw questionnaire into a workshop-ready brief with localisation and industry intelligence.

### Step 1 — Get the BDCQ out of SAP (Chrome extension)
1. In Chrome, open the **SAP Roadmap Viewer** → your project → **Explore** phase → **Accelerators** tab.
2. Make sure the file list is visible (scroll so files render).
3. Click the **SAP Deck Agent** icon → **Scan**. It finds the BDCQ questionnaire files.
4. Click **Save BDCQ Files** → choose your project folder (the unzipped tool folder).
   - Downloads the Excel files into `bdcq\xlsx\` **and** writes a combined `bdcq\bdcq-questions.json` — that JSON is what the app reads.

### Step 2 — Enrich in the web app
1. Open the **BDCQ Agent** page.
2. Confirm your BDCQ questions have loaded (from `bdcq\bdcq-questions.json`).
3. Choose the client's **country/countries** and **industry**.
4. Click **Enrich**. Claude works through each domain, flags mandatory statutory/localisation items, and adds Cloud PE constraints.
5. Review the enriched output on screen.

### Step 3 — Export
Export the enriched brief to Excel — it lands in **`output\`** (also on the **Output Files** page).

> **Localisation note:** enriched country rows show the country by name (e.g. *"Local – India"*, *"Local – Saudi Arabia"*). If a domain appears as a single row where you expected per-country rows, that domain wasn't broken out in the source SAP file — a data gap, not a tool error.

---

## 9. Workflow B — KDD Generation

**Goal:** produce a Cloud PE-compliant Key Design Decisions log for a set of scope items.

### Step 1 — Load the scope catalog (Chrome extension, once per SAP release)
1. In Chrome, open **SAP for Me → Process Navigator**.
2. Click the **SAP Deck Agent** icon → **Load All Processes + Details**. Pulls the full catalog (~650 processes) with descriptions.
3. Click **Save & Generate** → choose your project folder.
   - Writes `scope-catalog.json` plus agent reference files.
   - Only needed **once every 6 months** (SAP releases February and August).

### Step 2 — Generate decisions
1. Open the **KDD Generator** page.
2. Browse by **Line of Business (LOB)** and select the scope items for your workshop.
3. Enter project details (client, project name).
4. Click **Generate**. Claude produces ~15 KDD questions per scope item — each with options, rationale, RICEF classification, decision owner, and SAP Activate phase.
5. A **quality critique runs automatically** after generation, flagging weak or non-compliant rows for your attention.

### Step 3 — Refine and export
Move to the **KDD Editor** to review, refine, quality-check, and export — see the next section.

---

## 10. Working in the KDD Editor

The **KDD Editor** (Review · Edit · Export) is where decisions become client-ready. Key controls, top to bottom:

### Editing decisions
- Edit any field inline — decision text, rationale, notes, owner.
- Set each row's **Fit / Partial Fit / Gap** classification.
- For **Gap** rows, set the **WRICEF Type** and resolution so the item flows into the WRICEF register.

### ⚡ Quality Check
Click **⚡ Quality Check** to run an AI critique across your decisions. Claude flags rows that are vague, non-compliant with Cloud PE, or missing detail, so you can fix them before the workshop.

### AI Suggestions (per question)
On an individual decision, request **AI suggestions** — Claude proposes **three** decision options (typically: SAP standard, a common client variation, and an extensibility/BTP path). Pick one as a starting point and edit. Suggestions are informed by **past approved decisions** you've saved, so they get more on-point over time.

### 🔧 WRICEF Inventory tab
Switch to the **WRICEF Inventory** tab to see every Gap item classified as a WRICEF object, with an **effort summary and breakdown**. Set the **WRICEF Type** on Gap rows in the editor and they appear here automatically.

### 📁 Projects
Click **📁 Projects** to save the current work or load a different client's KDD. See [Managing Projects](#12-managing-projects).

### Exporting
- **⬇ Export Excel** — the formatted 5-sheet KDD log → `output\`.
- **📊 PPT** — a **PowerPoint** version of the KDD → `output\`.
- From the **WRICEF Inventory** tab you can also export a focused **WRICEF Inventory** Excel (Gap items with resolution = WRICEF).

---

## 11. Gap Analysis

The **KDD Analysis** page runs an **AI Gap Analysis** (powered by Claude) across your decision set — a multi-step review that summarises where the client deviates from SAP standard and the associated **WRICEF effort**. Use it to brief leadership on the shape and size of the gaps before detailed design.

---

## 12. Managing Projects

You can keep **multiple client projects** on one machine.
- **Save** the current KDD as a named project from the editor's **📁 Projects** panel.
- **Load** a different project to switch clients — you'll be warned that unsaved changes to the current project will be lost, so export or save first.
- **Delete** projects you no longer need from the same panel.

Approved decisions are also retained to power the editor's **AI Suggestions** on future projects.

---

## 13. Output Files

| Location | Contents |
|---|---|
| `output\` | Everything you generate: KDD Excel logs, **KDD PowerPoint decks**, WRICEF Inventory Excel, and enriched BDCQ briefs. Also downloadable from the **Output Files** page. |
| `bdcq\xlsx\` | The raw BDCQ Excel files the extension downloaded. |
| `bdcq\bdcq-questions.json` | Parsed BDCQ questions the BDCQ Agent reads. |
| `scope-catalog.json` | The SAP process catalog the KDD Generator reads. |

**KDD Excel** contains five sheets: **Cover · Instructions · Scope Overview · KDD Master Log · Summary**. Filenames follow:
```
output\Accenture_KDD_<Client>_<Project>_<Date>.xlsx
output\WRICEF_Inventory_<Client>_<Project>_<Date>.xlsx
```

---

## 14. Data Files and Refresh Cadence

| File | What it is | Refresh cadence |
|---|---|---|
| `bdcq\bdcq-questions.json` | The client's BDCQ questions | **Per project** — re-scan the Roadmap Viewer each engagement |
| `scope-catalog.json` | SAP's process catalog | **Every 6 months** — SAP releases February (xx02) and August (xx08). The app warns you when your catalog is out of date. |

---

## 15. Settings and MCP Server

- **Settings** — general configuration for the app.
- **MCP Server** — an advanced option that exposes the tool's data to the Claude CLI via the Model Context Protocol, so you can query the data with Claude directly. Not needed for standard BDCQ/KDD work — leave it unless a project lead asks you to use it.

---

## 16. Security and Data Handling

This handles client data, so treat it accordingly.

- **Local only.** The app binds to `127.0.0.1:8321` (loopback). Nobody else on your network — office or client — can reach it. There is no login because it only serves *you* on *your* machine.
- **What leaves your machine.** Only the content needed for a given AI action is sent to **Claude** (via the Claude CLI), exactly as any Claude Code session would. Nothing is sent to any other server. The Chrome extension talks only to SAP domains (`*.sap.com`) and refuses any non-SAP URL.
- **Where data lives.** Your BDCQ files, scope catalog, generated outputs, and saved decisions all sit in the tool folder on your disk — not in any cloud service the tool controls.
- **Accenture data classification.** Follow your engagement's data-handling rules for anything you feed in and anything you export. When in doubt, check with your project lead before processing client-confidential material.

---

## 17. Updating, Uninstalling, and Backups

### Updating to a new version
1. Stop the app (close the console window).
2. **Back up** your `output\`, `decisions\`, and any saved projects (copy them somewhere safe).
3. Unzip the new build into a **fresh** folder and copy your `bdcq\`, `scope-catalog.json`, `output\`, and `decisions\` across — *or* overwrite in place if instructed.
4. Run **`SETUP.bat`** again (dependencies may have changed).
5. In Chrome, **reload the extension** at `chrome://extensions`.

### Uninstalling
- **App:** just delete the tool folder. It's self-contained — nothing is written to the Windows registry.
- **Extension:** `chrome://extensions` → **Remove** on SAP Deck Agent.
- **Node.js / Claude CLI:** uninstall from Windows *Apps & features* only if you don't use them elsewhere.

### Backing up your work
Your important, non-regenerable data is:
- `output\` — everything you've exported.
- `decisions\` and saved projects — your KDD history and the AI-suggestion memory.

Copy these periodically to a safe location (or your engagement's document store).

---

## 18. Known Limitations

- **Fit-to-Standard** and **Business Process Design** agents are marked *Coming Soon* — not yet available.
- The **SAP Deck Agent extension is Chrome-only**, and the launchers (`SETUP.bat` / `START.bat`) are **Windows-only**.
- Enrichment and KDD quality are only as complete as the **source SAP data**. If a domain isn't broken out in the BDCQ, or a process lacks description in the catalog, the output reflects that gap.
- AI generation needs **internet** and a working **Claude** login; large scope sets can take a minute or two per step.
- The tool targets **Cloud Public Edition only** — it deliberately will not produce on-premise/private-cloud concepts.

---

## 19. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **"Claude CLI not found"** in the app | Claude isn't installed or isn't on your PATH. Install from **claude.ai/code**, confirm `claude --version` in a **new** terminal, then restart `START.bat`. Custom install location? Set an environment variable `CLAUDE_PATH` to the full path of `claude.exe`. |
| **`SETUP.bat` says "npm install failed"** | Node.js missing/not on PATH. Install LTS from **nodejs.org**, open a new terminal, confirm `node --version`, re-run `SETUP.bat`. |
| **Browser "can't reach this page"** at localhost:8321 | The console window isn't running or is still starting. Start `START.bat`, wait for the banner, refresh. |
| **"Port 8321 is already in use"** | Another copy is running — use that tab, or close the other console window and start again. |
| **Blank / half-loaded page** | Still starting. Wait 5 seconds and refresh. |
| **Extension: "Folder access expired"** | Click the save button again — the extension re-opens the folder picker. Make sure you're on the current extension build. |
| **Extension opens an SSO tab and the file doesn't save** | Current builds wait for SSO and retry automatically. If it still stalls: let the **accounts.sap.com** tab finish (it closes itself), confirm you're logged into SAP, then click the save button again. |
| **Extension finds no files** | Turn on **Developer mode** in `chrome://extensions`; be on the right SAP page with the file list visible; scroll so files render; click **Scan Again**. |
| **Excel or PPT export fails** | Ensure `SETUP.bat` completed (it installs the Excel/PPT builders in `kdd-generator\`). Don't delete the `output\` folder. |
| **"No WRICEF items"** on export | Set **WRICEF Type** on your **Gap** rows in the editor first. |
| **Generation is slow / times out** | Large scope sets take time. Retry — partial results are cached. |
| **Extension changed but behaves the same** | Reload it: `chrome://extensions` → ↻ on SAP Deck Agent. |

---

## 20. FAQ

**Does my client's data get uploaded anywhere?**
The app runs locally and binds to loopback only. The single external call is to **Claude** (via the CLI) for AI generation — no other service. See [Security and Data Handling](#16-security-and-data-handling).

**Why do I need both Node.js and Claude CLI?**
Node.js runs the local web app; Claude CLI is the AI engine the app calls. Different jobs.

**How big is the install?**
The zip is under 1 MB. After `SETUP.bat`, the folder grows to ~**70 MB** on disk (the app's libraries, mostly the Excel/PPT engines). It's never transferred; normal for a Node app.

**Can I move the folder after installing?**
Yes — re-run `START.bat` from the new location. Don't put it in a syncing folder (OneDrive) while running.

**Can I work on more than one client at a time?**
Yes — use **📁 Projects** in the editor to save and switch between projects.

**Do I need to re-scan SAP every time?**
BDCQ: once per project. Scope catalog: once every 6 months (or when the app warns it's outdated).

**Which browsers work?**
The web app works in any modern browser. The **SAP Deck Agent extension is Chrome-only**.

---

## 21. Glossary

| Term | Meaning |
|---|---|
| **BDCQ** | Business Driven Configuration Questionnaire — SAP's Explore-phase questionnaire that drives configuration decisions. |
| **KDD** | Key Design Decision — a documented design choice for a scope item, with options and rationale. |
| **Scope item** | A discrete SAP business process (e.g. *BD6 — Vendor Invoice Management*), identified by a short code. |
| **RICEF / WRICEF** | Reports, Interfaces, Conversions, Enhancements, Forms (W = Workflow) — classification of a gap/extension object. |
| **Cloud PE** | S/4HANA Cloud **Public Edition** — the standardised, Fiori/BTP-configured SAP deployment this tool targets. |
| **LOB** | Line of Business (Finance, Procurement, Sales, etc.) — how scope items are grouped. |
| **Fit-to-Standard** | The Explore-phase activity of mapping client requirements onto SAP standard processes. |
| **MCP** | Model Context Protocol — lets Claude read the tool's data directly. |

---

## 22. Getting Help

- Re-read the relevant section above, then check **[Troubleshooting](#19-troubleshooting)**.
- Note the exact error text and what you clicked just before it.
- Contact your **project lead / the tool owner** with that detail (and a screenshot if you can). Mention you're on **v1.1.0**.

---

*Explore Accelerator v1.1.0 · SAP S/4HANA Cloud Public Edition · Built with Claude Code · For internal Accenture use only.*
