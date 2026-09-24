// agents/shared.js — constants and utilities shared across all agents
"use strict";

const path = require("path");

const ROOT = path.resolve(__dirname, "..");

const CLAUDE_MODEL      = process.env.CLAUDE_MODEL || "claude-haiku-4-5-20251001";
const CLAUDE_TIMEOUT_MS = 240_000;   // 240s — 4 min; complex EHS/MFG processes can take 100-150s

const KDD_REQUIRED_FIELDS = [
  "kddId", "designQuestion", "fitGap", "complexity",
  "decisionOwner", "sapActivatePhase", "rationale", "impactActions", "notes"
];

// Cloud PE forbidden terms — must never appear in generated KDD output
const FORBIDDEN_TERMS = [
  "ABAP", "ECC", "R/3", "SE16", "SM30", "SE38", "SE80",
  "user exit", "BAdI", "CMOD", "SMOD",
  "on-premise", "on premise",
  "private cloud",
  "SAP GUI", "SAPGUI",
  "SM31", "SM34", "OBYC",
  "backend custom", "LSMW", "BDC", "batch input"
];

// Allowed auto-replacements when forbidden term is found
// Cloud PE equivalents — used by validation-agent to auto-fix output
const AUTO_REPLACEMENTS = {
  // UI
  "SAP GUI":        "SAP Fiori",
  "SAPGUI":         "SAP Fiori",
  // Deployment model
  "on-premise":     "Cloud PE",
  "on premise":     "Cloud PE",
  "private cloud":  "Cloud PE",
  // Extensibility
  "user exit":      "Key User Extension",
  "BAdI":           "BTP Extension",
  // Legacy transactions → Cloud PE equivalents
  "SM30":           "SAP Fiori customizing app",
  "SM31":           "SAP Fiori customizing app",
  "SM34":           "SAP Fiori customizing app",
  "SE16":           "SAP Fiori Display app",
  "SE38":           "ABAP Cloud development",
  "SE80":           "ABAP Cloud development",
  // Data migration
  "LSMW":           "SAP S/4HANA Migration Cockpit",
  "BDC":            "SAP S/4HANA Migration Cockpit",
  "batch input":    "SAP S/4HANA Migration Cockpit",
  // Programming
  "ABAP":           "ABAP Cloud",
  "backend custom": "Key User Extension or BTP Side-by-Side",
  // Legacy system references → neutral Cloud PE terminology
  "ECC":            "source system",
  "R/3":            "source system"
};

const LOB_MODULE = {
  "Finance":                              "FIN",
  "FAC Finance and Controlling":          "FIN",
  "Sales":                                "SD",
  "Sourcing and Procurement":             "MM",
  "Procurement":                          "MM",
  "Manufacturing":                        "PP",
  "Human Resources":                      "HCM",
  "Supply Chain":                         "SCM",
  "Asset Management":                     "PM",
  "Service":                              "CS",
  "IT Management":                        "IT",
  "Professional Services":                "PS",
  "R&D and Engineering":                  "RE",
  "Data Management":                      "DM",
  "Database and Data Management":         "DM",
  "Application Platform and Infrastructure": "IT",
  "Integration":                          "IT",
  "Solutions for Specific Industries":    "Cross",
  "VEV Vendor Evaluation":                "MM",
  "Other":                                "Cross"
};

module.exports = {
  ROOT,
  CLAUDE_MODEL,
  CLAUDE_TIMEOUT_MS,
  KDD_REQUIRED_FIELDS,
  FORBIDDEN_TERMS,
  AUTO_REPLACEMENTS,
  LOB_MODULE
};
