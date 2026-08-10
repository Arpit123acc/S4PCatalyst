# S4PC Catalyst — Team Access Guide

An agentic delivery accelerator for **SAP S/4HANA Cloud, Public Edition**: give it a Functional
Design, and a team of AI agents designs, builds, reviews and tests a clean-core RICEFW object —
with you approving at each checkpoint. There are two ways to access it; use whichever your lead set up.

---

## Option A — Run your own local copy (most common)

**You need:** **Python 3.9+** installed, and **Claude Code installed and logged in** (your
organisation's Claude Code / Claude for Work seat — this is the AI runtime; no API keys required).

1. Unzip **`S4PC-Catalyst-v1.0-team.zip`** anywhere.
2. Start it:
   - **Windows:** double-click **`START.cmd`**
   - **macOS / Linux:** run **`./start.sh`**
3. Your browser opens at **http://127.0.0.1:8321**.
4. **FD Intake** → upload your Functional Design (`.docx` / `.pdf` / `.txt`) → click **▶ Run pipeline**.
5. Follow it live in **Workflow Explorer**; approve / adjust / reject at each ✋ checkpoint.
6. Stop it with **`SHUTDOWN.cmd`** (or close the window).

> If **Run pipeline** reports the CLI isn't found, confirm `claude` (Claude Code) is installed and
> you're logged in. Everything runs on your machine; nothing is stored externally.

---

## Option B — Use the shared instance (nothing to install)

Your lead runs one instance on a shared machine; you just open it in a browser.

1. Open **http://<HOST-IP>:8321** — your lead will give you the exact address.
2. Log in: user **`team`**, password **(provided by your lead)**.
3. Use **FD Intake → ▶ Run pipeline**, and watch **Workflow Explorer**.

> Keep it on the corporate network / VPN only.

---

## What you'll get from a run
The pipeline produces, per object: discovery, a clean-core solution proposal (key user / developer
/ side-by-side — or a mix), release verdicts, technical design, code, a lint report, unit tests, a
peer/challenger review, and a package summary with a **tenant-verification checklist** — visible in
the Workflow Explorer and saved under `output/<RUN-ID>/`.

## Good to know
- **Clean core is enforced** — only SAP-released APIs, CDS views and BAdIs; the pipeline won't use
  BAPIs or classical ABAP.
- **You stay in control** — it pauses for your decision at 3 checkpoints and never ships on its own.
- **Confidential / internal use only.** See `ACCESS-AND-IP.md` (IP & security) and `HOSTING.md`
  (for whoever runs the shared instance).
