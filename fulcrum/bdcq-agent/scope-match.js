"use strict";
// Does a BDCQ row mention any of the requested scope items?
//
// ONE implementation, called from bdcq-builder.js and routes.js. It was
// inlined in both and they were identical, so the export and the preview
// would have drifted apart the moment either was touched.
//
// THE BUG THIS FIXES
//   A Scope Ref cell holds a LIST, not one id:
//       "BD9, 2EQ, I9I, BKA, BDD, 1EZ, 1F1, 1MC, 1J7, J14"
//   The old test compared the WHOLE cell against one id, so every
//   multi-item row was silently skipped. Measured over the 16 published
//   workbooks: 389 of 1,337 rows carry such a list, and filtering for BD9
//   returned 20 rows when 160 mention it.
//
//   Comma is the only separator SAP uses in these files -- checked across
//   all 389 cells. Splitting on more punctuation would risk breaking free
//   text apart for no measured gain.
//
//   A cell with no comma splits to itself, so single-id cells behave
//   exactly as before and free text still cannot match a 3-character id.

function cellIds(v) {
  return String(v == null ? "" : v)
    .toUpperCase()
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);
}

function rowMatchesScope(row, scopeSet) {
  if (!scopeSet || scopeSet.size === 0) return true;   // no filter = keep all
  for (const v of Object.values(row || {})) {
    for (const id of cellIds(v)) {
      if (scopeSet.has(id)) return true;
    }
  }
  return false;
}

module.exports = { rowMatchesScope, cellIds };
