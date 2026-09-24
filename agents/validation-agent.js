// agents/validation-agent.js — Stage 5 of the KDD pipeline  ← NEW agent
// Enforces SAP S/4HANA Cloud PE compliance on every generated KDD row.
//
// Three checks:
//   1. Schema    — 15 rows, all required fields present, valid enums
//   2. Forbidden — no Cloud PE-banned terms (ABAP, SAP GUI, on-premise, etc.)
//   3. Auto-fix  — replaces known forbidden terms with Cloud PE equivalents where possible
//
// If auto-fix resolves all forbidden terms → validationPassed = true, validationAutoFixed = true
// If critical errors remain               → validationPassed = false (orchestrator retries generation)
"use strict";

const { KDD_REQUIRED_FIELDS, FORBIDDEN_TERMS, AUTO_REPLACEMENTS } = require("./shared");

// Text fields to scan and auto-fix
const SCANNABLE_FIELDS = [
  "designQuestion", "rationale", "impactActions", "notes", "l1", "l2",
  "ricefTitle", "extensibilityType"
];

const VALID_FIT_GAP   = new Set(["Fit", "Partial Fit", "Gap"]);
const VALID_COMPLEXITY = new Set(["Low", "Medium", "High"]);
const VALID_PHASE      = new Set(["Explore", "Realize", "Deploy"]);

// ── Allowed Cloud PE terms that contain forbidden substrings ─────────────────
// "ABAP Cloud" is a valid Cloud PE extensibility option — must not be flagged
// even though "ABAP" alone is forbidden.
const ALLOWED_PATTERNS = [
  "abap cloud",          // Cloud PE extensibility layer — valid
  "developer abap cloud" // extensibilityType value — valid
];

function isForbiddenInContext(val, term) {
  const lower = val.toLowerCase();
  if (!lower.includes(term.toLowerCase())) return false;
  // Check if every occurrence of the forbidden term is part of an allowed pattern
  const termLower = term.toLowerCase();
  let idx = 0;
  while ((idx = lower.indexOf(termLower, idx)) !== -1) {
    const context = lower.slice(Math.max(0, idx - 5), idx + termLower.length + 10);
    const inAllowed = ALLOWED_PATTERNS.some(p => context.includes(p));
    if (!inAllowed) return true;  // found occurrence not covered by allowed pattern
    idx += termLower.length;
  }
  return false;  // all occurrences were in allowed context
}

// ── Scan rows for forbidden terms ─────────────────────────────────────────────

function scanForbidden(rows) {
  const violations = [];
  rows.forEach((row, i) => {
    SCANNABLE_FIELDS.forEach(field => {
      const val = row[field] || "";
      FORBIDDEN_TERMS.forEach(term => {
        if (isForbiddenInContext(val, term)) {
          violations.push({
            row:    i + 1,
            kddId:  row.kddId || `row-${i + 1}`,
            field,
            term,
            sample: val.slice(0, 80)
          });
        }
      });
    });
  });
  return violations;
}

// ── Auto-fix: replace forbidden terms with Cloud PE equivalents ───────────────

function autoFix(rows) {
  return rows.map(row => {
    const fixed = { ...row };
    SCANNABLE_FIELDS.forEach(field => {
      if (!fixed[field]) return;
      let val = fixed[field];
      for (const [term, replacement] of Object.entries(AUTO_REPLACEMENTS)) {
        // Case-insensitive, whole-word-ish replacement
        const re = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
        val = val.replace(re, replacement);
      }
      fixed[field] = val;
    });
    return fixed;
  });
}

// ── Schema validation ─────────────────────────────────────────────────────────

function schemaErrors(rows) {
  const errors = [];

  if (!Array.isArray(rows) || rows.length !== 15) {
    errors.push(`Expected 15 rows, got ${Array.isArray(rows) ? rows.length : typeof rows}`);
    return errors;   // no point checking individual rows if count is wrong
  }

  rows.forEach((row, i) => {
    const label = row.kddId || `row ${i + 1}`;
    const missing = KDD_REQUIRED_FIELDS.filter(f => !row[f]);
    if (missing.length) errors.push(`${label} missing fields: ${missing.join(", ")}`);

    if (row.fitGap    && !VALID_FIT_GAP.has(row.fitGap))
      errors.push(`${label} invalid fitGap: "${row.fitGap}"`);
    if (row.complexity && !VALID_COMPLEXITY.has(row.complexity))
      errors.push(`${label} invalid complexity: "${row.complexity}"`);
    if (row.sapActivatePhase && !VALID_PHASE.has(row.sapActivatePhase))
      errors.push(`${label} invalid phase: "${row.sapActivatePhase}"`);
  });

  return errors;
}

// ── Agent entry point ─────────────────────────────────────────────────────────

/**
 * @param {Object} state
 * @param {Array}  state.rows   - KDD rows from generation-agent
 * @param {Object} state.item   - catalog item (for logging)
 * @returns {Object} updated state with:
 *   validationPassed    {boolean}
 *   validationErrors    {string[]}
 *   validationAutoFixed {boolean}  — true if forbidden terms were fixed automatically
 *   forbiddenFound      {number}   — count before auto-fix
 *   rows                {Array}    — possibly auto-fixed rows
 */
async function run(state) {
  const { rows, item } = state;
  const id = item ? item.id : "?";

  if (!rows || !rows.length) {
    return {
      ...state,
      validationPassed:    false,
      validationErrors:    ["No rows to validate"],
      validationAutoFixed: false,
      forbiddenFound:      0
    };
  }

  // 1. Schema check
  const schemaErrs = schemaErrors(rows);

  // 2. Forbidden terms — scan before fix
  const before = scanForbidden(rows);

  // 3. Auto-fix where possible
  const fixedRows = before.length > 0 ? autoFix(rows) : rows;

  // 4. Re-scan after fix to find what couldn't be fixed
  const after = before.length > 0 ? scanForbidden(fixedRows) : [];

  // Build final error list
  const allErrors = [
    ...schemaErrs,
    ...after.map(v => `${v.kddId}.${v.field} still contains "${v.term}" — manual review needed`)
  ];

  const autoFixed = before.length > 0 && after.length === 0;

  if (before.length > 0) {
    const fixedCount = before.length - after.length;
    console.log(`[validation-agent] ${id} — ${before.length} forbidden terms found, ${fixedCount} auto-fixed, ${after.length} remaining`);
  }
  if (schemaErrs.length) {
    console.warn(`[validation-agent] ${id} — ${schemaErrs.length} schema error(s): ${schemaErrs[0]}`);
  }
  if (allErrors.length === 0) {
    console.log(`[validation-agent] ${id} — ✅ passed (${fixedRows.length} rows)`);
  }

  return {
    ...state,
    rows:                fixedRows,
    validationPassed:    allErrors.length === 0,
    validationErrors:    allErrors,
    validationAutoFixed: autoFixed,
    forbiddenFound:      before.length
  };
}

module.exports = { run };
