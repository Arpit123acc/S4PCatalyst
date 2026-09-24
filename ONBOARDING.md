# Fulcrum — SAP S/4HANA Cloud PE · Explore Accelerator

## What This Is

An AI-powered accelerator for the SAP Activate **Explore phase** on S/4HANA Cloud Public Edition projects. It enriches SAP's standard Business Driven Configuration Questionnaires (BDCQ) with country localisation, industry-specific context, and Cloud PE constraints — so consultants arrive at workshops with intelligence, not just a question list.

## What It Does

**Without this tool:**
SAP provides standard BDCQ Excel files via the Roadmap Viewer. A consultant opens them, reads the questions, and asks the client. The client gets generic questions they could find on SAP Help Portal.

**With this tool:**
The BDCQ Enricher reads those same files and adds three layers:

1. **Country localisation** — flags mandatory statutory requirements (GST/TDS for India, GoBD for Germany, MTD for UK, ZATCA for Saudi Arabia, etc.) and tells you the standard Cloud PE answer so the question becomes a confirmation of scope, not an open discovery
2. **Industry context** — injects vertical-specific themes and sub-questions SAP's standard BDCQ doesn't include (BOM depth for Manufacturing, billing model for Professional Services, batch management for Pharma, etc.)
3. **Cloud PE constraints** — catches where client expectations will hit a Public Cloud wall (custom ABAP, SM30 table maintenance, ABAP workflow, direct DB access) and states the standard SAP Cloud PE path and extension option

## Prerequisites

Before running the enricher, you need BDCQ files downloaded from SAP.

**One-time Chrome extension setup:**
1. Open Chrome → `chrome://extensions` → Enable Developer mode → Load unpacked
2. Pick the `SAPDeckAgent_v27/` folder from this project
3. Go to `me.sap.com/roadmapviewer` → open your project → Explore phase → Accelerators tab
4. Click the SAP Deck Agent extension icon → Scan → Save BDCQ Files → pick **this folder**
5. The extension creates `bdcq/bdcq-questions.json` automatically

> If `bdcq/bdcq-questions.json` already exists in this folder, skip to Running.

## Running the BDCQ Enricher

Open this folder in Claude Code, then type:

```
/s4pc-bdcq-enricher
```

The agent will:
- Confirm the BDCQ domains found (typically 10–15 domains)
- Ask for client name, countries in scope, and industry
- Recommend which domains to enrich for that industry
- Produce enriched questions domain by domain — mandatory items flagged, country notes included, Cloud PE constraints called out, workshop prompts rewritten

**To go straight in:**
```
/s4pc-bdcq-enricher — Client: Acme Corp, Countries: India Germany, Industry: Manufacturing
```

## Skills Available

| Skill | Invoke | Purpose |
|---|---|---|
| BDCQ Enricher | `/s4pc-bdcq-enricher` | Enrich BDCQ questions with localisation + industry + Cloud PE context |
| KDD Generator | `/s4pc-kdd-generator` | Generate KDD log from scope catalog — exports 5-sheet Excel |

## Countries Covered

India · Germany · USA · UK · Brazil · France · Australia · UAE · Saudi Arabia · Singapore

## Industries Covered

Manufacturing · Retail · Professional Services · Pharma / Life Sciences · Utilities · Financial Services · Oil & Gas · High Tech

## Rules — Cloud PE Only

This accelerator strictly enforces SAP S/4HANA Cloud Public Edition standards.
Never references: ABAP, ECC, R/3, SE16, SM30, user exits, BAdIs, SAP GUI, on-premise, private cloud.
Configuration only via SAP Fiori apps or SAP BTP.
