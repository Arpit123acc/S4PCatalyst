# RICEFW-F — Form / Output (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Form
**Realization:** Build output with the Maintain Form Templates app (Adobe Forms via the cloud forms service) and Output Parameter Determination — Smart Forms, SAPscript, and custom print programs are not available.

## Pipeline
1. Discovery → document type (PO, order confirmation, invoice…), channels (print/email/EDI), languages, branding.
2. Start from the SAP-delivered master/content template for that output scenario (Maintain Form Templates → download).
3. Data check: form data provider is fixed per output scenario — verify required fields exist in the provider; missing fields → custom fields (key user) surfaced into the form data provider where supported.
4. **GATE:** required data available in the standard form data provider (or via released extension point). Never invent data-provider fields.
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): XDP template in Adobe LiveCycle Designer, upload, assign in output determination.
6. Code review + human **Code approval (Checkpoint 2)** on the built template + determination rules.
7. Tests: per language/channel, edge cases (long texts, many line items, page breaks), golden-file PDF comparison.
8. Technical Design (documentation, authored after tests): template layout spec, fragment/master template usage, output determination rules (Output Parameter Determination app — BRF+ style rules), email templates if needed.
9. Package incl. transport/export of templates + determination rules.

## Anti-patterns to reject
- "Copy the print program and adjust" — no print programs here.
- Hardcoded literals instead of translatable texts.
- Custom logo/layout per company code duplicated instead of master template fragments.
