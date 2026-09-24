# Fulcrum — SAP S/4HANA Cloud Public Edition

## What This Is
A Claude Code cartridge for the SAP Activate **Explore phase**, covering BDCQ enrichment and KDD generation for S/4HANA Cloud Public Edition projects.

## Cartridge Structure
```
explore-accelerator/
├── .claude/agents/
│   ├── s4pc-bdcq-enricher.md   ← BDCQ Enricher skill (localization + industry)
│   └── s4pc-kdd-generator.md   ← KDD Generator skill
├── kdd-generator/
│   ├── helpers.js               ← RAG, phase, owner, effort, on-prem detection
│   ├── excel-builder.js         ← Builds the 5-sheet XLSX output
│   └── package.json             ← xlsx dependency
├── SAPDeckAgent_v27/            ← Chrome extension (install in Chrome)
├── bdcq/                        ← BDCQ Excel files + bdcq-questions.json (from extension)
├── output/                      ← Generated files land here
├── scope-catalog.json           ← Saved from extension (update every 6 months)
└── cartridge.json
```

## Skills Available
| Skill | Invoke | What it does |
|---|---|---|
| BDCQ Enricher | `/s4pc-bdcq-enricher` | Enriches SAP BDCQ questions with country localisation, industry context, and Cloud PE constraints — produces a workshop-ready brief |
| KDD Generator | `/s4pc-kdd-generator` | Generates KDD log from scope catalog for given scope items — exports 5-sheet Excel |

## Setup (One Time)
1. Install Chrome extension: Chrome → `chrome://extensions` → Load unpacked → pick `SAPDeckAgent_v27/`
2. Run `npm install` in the `kdd-generator/` folder
3. **For BDCQ enrichment**: Open SAP Roadmap Viewer → Explore phase → Accelerators tab → extension → Scan → Save BDCQ Files → pick this folder → creates `bdcq/bdcq-questions.json`
4. **For KDD generation**: Open SAP for Me Process Navigator → extension → Load Full Catalog → Save to Cartridge → creates `scope-catalog.json`

## Running the BDCQ Enricher
```
/s4pc-bdcq-enricher
```
The agent reads `bdcq/bdcq-questions.json`, asks for client country and industry, then produces enriched workshop questions with mandatory localisation flags and Cloud PE constraints.

## Running the KDD Generator
```
/s4pc-kdd-generator
```
Or with scope IDs and project details upfront:
```
/s4pc-kdd-generator BD6, J59, BEI — Client: Acme Corp, Project: S4H Rollout
```

## Data Files
| File | What | How often |
|---|---|---|
| `bdcq/bdcq-questions.json` | Parsed BDCQ Excel files — all domains, all questions | Per project (download from Roadmap Viewer) |
| `scope-catalog.json` | 657 SAP processes with descriptions, LOB, benefits | Every 6 months (new SAP release) |

## Output
Excel files are written to the `output/` folder:
```
output/Accenture_KDD_<Client>_<Project>_<Date>.xlsx
```
5 sheets: Cover · Instructions · Scope Overview · KDD Master Log · Summary

## SAP Release Check
The skill automatically checks if `scope-catalog.json` matches the expected current SAP release.
SAP releases every 6 months — February (XX02) and August (XX08).
A warning is shown if your catalog is outdated.

## Rules — Cloud PE Only
This cartridge strictly enforces SAP S/4HANA **Cloud Public Edition** standards.
Never reference: ABAP, ECC, R/3, SE16, SM30, SE38, SE80, user exit, BAdI, SAP GUI, SAPGUI,
CMOD, SMOD, on-premise, private cloud, table maintenance, SM31, SM34.
Configuration only via SAP Fiori apps or SAP BTP.
