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
├── SAPDeckAgent_v27/            ← Chrome extension (fallback only — see Data Files)
├── bdcq/                        ← bdcq-questions.json (generated from the S4PC brain)
├── output/                      ← Generated files land here
├── scope-catalog.json           ← Generated from the S4PC brain (see Data Files)
└── cartridge.json
```

## Skills Available
| Skill | Invoke | What it does |
|---|---|---|
| BDCQ Enricher | `/s4pc-bdcq-enricher` | Enriches SAP BDCQ questions with country localisation, industry context, and Cloud PE constraints — produces a workshop-ready brief |
| KDD Generator | `/s4pc-kdd-generator` | Generates KDD log from scope catalog for given scope items — exports 5-sheet Excel |

## Setup (One Time)
1. Run `npm install` in the repo root and in the `kdd-generator/` folder
2. Generate both data files from the S4PC brain:
   ```
   python3.11 scripts/export_scope_catalog.py  --out <this-folder>/scope-catalog.json
   python3.11 scripts/export_bdcq_questions.py --out <this-folder>/bdcq/bdcq-questions.json
   ```
   Both run unattended on the brain host. No browser, no SAP session, no
   Chrome extension.

### Where the data comes from, and why it changed
Both files used to be produced by the `SAPDeckAgent_v27` Chrome extension,
which needed a logged-in SAP session and a human at a browser. The S4PC brain
already holds the same content from the same SAP sources — the 657 solution
processes from the EAXService OData API, and all 16 Business Driven
Configuration Questionnaire workbooks from the SAP Activate accelerator
catalogue — so the exporters produce identical files without any of that.

The shape is unchanged, so nothing in this cartridge needed modifying. What
is gained is release provenance: each file records which S/4HANA release its
content describes, which the scraped version could not say.

The extension still works and remains in the repo as a fallback for anyone
without brain access.

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
| `bdcq/bdcq-questions.json` | 16 BDCQ workbooks, 1,337 questions, all domains | `export_bdcq_questions.py` after a brain refresh |
| `scope-catalog.json` | 657 SAP processes with descriptions, LOB, benefits | `export_scope_catalog.py` after a brain refresh |

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

Files generated from the brain also carry a `_source` block naming the exact
`target_release` and `internal_version` they were built from, so the release
can be read off the file rather than inferred from its age.

## Rules — Cloud PE Only
This cartridge strictly enforces SAP S/4HANA **Cloud Public Edition** standards.
Never reference: ABAP, ECC, R/3, SE16, SM30, SE38, SE80, user exit, BAdI, SAP GUI, SAPGUI,
CMOD, SMOD, on-premise, private cloud, table maintenance, SM31, SM34.
Configuration only via SAP Fiori apps or SAP BTP.
