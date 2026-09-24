// pre-generate.js — Build kdd-cache.json for all 657 scope items
// No API key needed — uses smart templates + process step extraction
// Run: node kdd-generator/pre-generate.js
"use strict";

const fs      = require("fs");
const path    = require("path");
const helpers = require("./helpers");

const ROOT    = path.join(__dirname, "..");
const cat     = JSON.parse(fs.readFileSync(path.join(ROOT, "scope-catalog.json"), "utf8"));

// ── Module mapping ────────────────────────────────────────────────────────────
const LOB_MODULE = {
  "Finance": "FIN", "Sales": "SD", "Procurement": "MM",
  "Manufacturing": "PP", "Human Resources": "HCM",
  "Supply Chain": "SCM", "Asset Management": "AM",
  "Service": "CS", "Quality Management": "QM"
};

const LOB_DOCS = {
  FIN: "journal entries, payment documents, dunning notices",
  SD:  "sales orders, delivery notes, billing documents",
  MM:  "purchase orders, goods receipt documents, supplier invoices",
  PP:  "production orders, process orders, operation confirmations",
  HCM: "payroll records, time sheets, employee master records",
  SCM: "stock transfer orders, outbound deliveries, warehouse tasks",
  AM:  "asset master records, depreciation runs, maintenance orders",
  CS:  "service orders, service contracts, service confirmations",
  QM:  "inspection lots, quality notifications, usage decisions",
  Cross: "business documents, correspondence, output messages"
};

const LOB_FIORI = {
  FIN: "Display Financial Statements, Manage Journal Entries, Manage Incoming Payments",
  SD:  "Manage Sales Orders, Create Billing Documents, Monitor Sales",
  MM:  "Create Purchase Requisition, Manage Purchase Orders, Manage Supplier Invoices",
  PP:  "Manage Production Orders, Monitor Production, Confirm Production Steps",
  HCM: "Manage Employee, My Paystub, Maintain Business Roles",
  SCM: "Monitor Stock, Manage Outbound Deliveries, Transfer Stock",
  AM:  "Manage Fixed Assets, Post Asset Depreciation, Asset Balances",
  Cross: "Manage Business Partner, Display Audit Log"
};

const LOB_ORGUNITS = {
  FIN: "company code, controlling area, chart of accounts, fiscal year variant",
  SD:  "sales organisation, distribution channel, division, sales area",
  MM:  "plant, purchasing organisation, purchasing group, storage location",
  PP:  "plant, work centre, production scheduler, MRP area",
  HCM: "personnel area, employee group, employee subgroup, payroll area",
  SCM: "warehouse number, storage type, storage section",
  AM:  "asset class, depreciation area, company code",
  Cross: "company code, plant, business area"
};

const LOB_MASTERDATA = {
  FIN: "GL accounts, cost centres, profit centres, customer/vendor master",
  SD:  "customer master, sales conditions, material master, pricing records",
  MM:  "material master, supplier master, info records, source lists",
  PP:  "material master, bills of material, work centres, routings",
  HCM: "employee master, organisational units, positions, jobs",
  SCM: "material master, batch master, storage location data",
  Cross: "business partner, master data records"
};

// ── Extract Key Process Flow steps from description ───────────────────────────
function extractSteps(description) {
  const lines = description.split("\n").map(l => l.trim()).filter(Boolean);
  const steps = [];
  let inFlow = false;
  for (const line of lines) {
    if (line === "Key Process Flow") { inFlow = true; continue; }
    if (inFlow && ["Business Benefits","Solution Capabilities","Overview","Industry Relevance"].includes(line)) break;
    if (inFlow && line.length > 5 && !["Info Label","Leading","Name","Version","Compare"].includes(line)) {
      steps.push(line);
    }
  }
  return steps.slice(0, 6);
}

function extractOverview(description) {
  const lines = description.split("\n").map(l => l.trim()).filter(Boolean);
  const out = [];
  let inOv = false;
  for (const line of lines) {
    if (line === "Overview") { inOv = true; continue; }
    if (inOv && ["Key Process Flow","Business Benefits","Solution Capabilities"].includes(line)) break;
    if (inOv && line.length > 5) out.push(line);
  }
  return out.join(" ").substring(0, 500);
}

function extractBenefits(description) {
  const lines = description.split("\n").map(l => l.trim()).filter(Boolean);
  const out = [];
  let inBen = false;
  for (const line of lines) {
    if (line === "Business Benefits") { inBen = true; continue; }
    if (inBen && ["Key Process Flow","Solution Capabilities","Industry Relevance"].includes(line)) break;
    if (inBen && line.length > 5) out.push(line);
  }
  return out;
}

// ── Generate 15 KDDs per scope item ──────────────────────────────────────────
function generateKDDs(item) {
  const mod    = LOB_MODULE[item.lob] || "Cross";
  const steps  = extractSteps(item.description);
  const docs   = LOB_DOCS[mod]   || LOB_DOCS.Cross;
  const fiori  = LOB_FIORI[mod]  || LOB_FIORI.Cross;
  const orgs   = LOB_ORGUNITS[mod] || LOB_ORGUNITS.Cross;
  const mdata  = LOB_MASTERDATA[mod] || LOB_MASTERDATA.Cross;
  const name   = item.name;
  const id     = item.id;

  // Use actual process steps where available, fall back to generic
  const s = (i, fallback) => steps[i] ? `"${steps[i]}"` : fallback;

  const questions = [
    // ① Fiori Configuration — 2 questions
    {
      cat: "fiori", phase: "Realize", fitGap: "Fit", complexity: "Low",
      owner: "Business Process Owner",
      q: `Which SAP Fiori applications (e.g. ${fiori.split(",")[0].trim()}) will be configured for end users of ${name}, and what launchpad tile groups and spaces need to be set up?`,
      r: `Fiori tile and launchpad configuration determines the user experience and access entry points for ${name}.`,
      i: `Without defined Fiori app assignments, users cannot access ${name} functionality after go-live.`,
      n: `Configure via Manage Launchpad Settings (Fiori). Use SAP standard business roles for ${name} where available.`,
      l3: "Fiori Configuration", l4: "Launchpad & Tile Setup"
    },
    {
      cat: "fiori", phase: "Realize", fitGap: "Partial Fit", complexity: "Medium",
      owner: "Business Process Owner",
      q: steps[0]
        ? `The first step in ${name} is ${s(0, "process initiation")}. What Fiori app configuration (e.g. default field values, screen variants, table column layouts) is needed to support this step efficiently?`
        : `What Fiori app screen variants, default values, and personalisation settings should be configured to optimise the ${name} user experience?`,
      r: `App-level personalisation for ${name} reduces manual entry and guides users through the correct process steps.`,
      i: `Without Fiori personalisation, users face unnecessary data entry and a higher risk of processing errors in ${name}.`,
      n: `Use Manage Launchpad Settings or app-level personalisation options in S/4HANA Cloud Public Edition Fiori.`,
      l3: "Fiori Configuration", l4: "Screen Variants & Personalisation"
    },
    // ② Master Data — 2 questions
    {
      cat: "masterdata", phase: "Explore", fitGap: "Fit", complexity: "Medium",
      owner: "Data Migration Lead",
      q: `What master data objects (${mdata}) must be created, extended, or validated before ${name} can be activated?`,
      r: `Complete and accurate master data is a hard prerequisite for ${name} transactions to execute without errors.`,
      i: `Missing or incomplete master data will block ${name} transactions at go-live, requiring emergency data loads.`,
      n: `Manage master data via Manage Business Partner (Fiori) and relevant master data apps. No backend config tools in Cloud PE.`,
      l3: "Master Data", l4: "Master Data Requirements"
    },
    {
      cat: "masterdata", phase: "Explore", fitGap: "Fit", complexity: "Low",
      owner: "Business Process Owner",
      q: steps[1]
        ? `The ${name} step ${s(1, "second process step")} relies on specific master data attributes. Who owns the data governance process for creating and maintaining these records, and what is the change control procedure?`
        : `Who is responsible for maintaining master data used in ${name} on an ongoing basis, and what Fiori-based governance process will be followed?`,
      r: `Clear data ownership prevents duplicate or stale master data from disrupting ${name} processing.`,
      i: `Without a defined governance process, inconsistent master data will cause errors and delays across all ${name} transactions.`,
      n: `Assign data stewards per entity. Use standard Fiori master data apps for ongoing maintenance — no SM30 or table maintenance in Cloud PE.`,
      l3: "Master Data", l4: "Data Governance & Ownership"
    },
    // ③ Approval / Workflow — 2 questions
    {
      cat: "workflow", phase: "Realize", fitGap: "Partial Fit", complexity: "Medium",
      owner: "Business Process Owner",
      q: steps[2]
        ? `The ${name} step ${s(2, "approval step")} may require a multi-level approval. What are the approval levels, monetary or volume thresholds, and escalation rules?`
        : `What approval workflow will be configured for ${name}, including number of approval levels, value thresholds, and escalation paths if an approver is unavailable?`,
      r: `Approval workflows for ${name} enforce financial controls and ensure segregation of duties compliance.`,
      i: `Without defined approval rules, ${name} transactions may bypass required authorisations, creating audit and compliance risks.`,
      n: `Configure via SAP Build Process Automation (BTP) or the standard flexible workflow in S/4HANA Cloud. No workflow config via backend tools.`,
      l3: "Approval & Workflow", l4: "Approval Levels & Thresholds"
    },
    {
      cat: "workflow", phase: "Realize", fitGap: "Fit", complexity: "Low",
      owner: "Business Process Owner",
      q: `What email and in-app notification rules will be configured for ${name} workflow events (e.g. task submitted, approved, rejected, overdue escalation)?`,
      r: `Timely notifications keep ${name} process participants informed and prevent tasks from stalling in an approver's queue.`,
      i: `Without notification configuration, approvers may not act promptly, extending ${name} cycle time and breaching SLAs.`,
      n: `Configure notifications via SAP Build Process Automation on BTP or the My Inbox Fiori app notification settings.`,
      l3: "Approval & Workflow", l4: "Notifications & Escalation"
    },
    // ④ Outputs & Forms — 1 question
    {
      cat: "outputs", phase: "Realize", fitGap: "Partial Fit", complexity: "Medium",
      owner: "Business Process Owner",
      q: `What output documents does ${name} produce (e.g. ${docs.split(",")[0].trim()})? What form template design, language versions, and output channels (email, print, EDI, portal) will be configured?`,
      r: `Output configuration determines the format and delivery method for all documents generated by ${name}, impacting external parties.`,
      i: `Without output configuration, ${name} cannot deliver required documents to customers, suppliers, or internal teams at go-live.`,
      n: `Configure outputs via SAP Forms service by Adobe or BTP Document Management. Use Manage Output Parameters (Fiori) — no NACE in Cloud PE.`,
      l3: "Outputs & Forms", l4: "Form Design & Output Channels"
    },
    // ⑤ Integration — 2 questions
    {
      cat: "integration", phase: "Realize", fitGap: "Fit", complexity: "Low",
      owner: "Integration Lead",
      q: steps[3]
        ? `The ${name} step ${s(3, "posting step")} creates data consumed by downstream SAP modules. What are the integration touchpoints and how will data consistency be validated across modules?`
        : `What are the integration touchpoints between ${name} and other SAP modules (e.g. FIN, ${mod === "MM" ? "PP, WM" : mod === "SD" ? "MM, WM" : "SD, MM"})? How will standard cross-module postings be configured and validated?`,
      r: `Correct integration design for ${name} ensures financial postings and data flows are consistent across the system landscape.`,
      i: `Unresolved integration gaps will cause data inconsistencies and manual reconciliation effort between ${name} and connected processes.`,
      n: `Standard S/4HANA Cloud integration is pre-delivered. Document custom scenarios and implement via SAP Integration Suite on BTP if required.`,
      l3: "Integration", l4: "Cross-Module Integration"
    },
    {
      cat: "integration", phase: "Realize", fitGap: "Gap", complexity: "High",
      owner: "Integration Lead",
      q: `Does ${name} require integration with third-party systems (e.g. banks, legacy ERP, external portals)? If so, what SAP Integration Suite iFlows or APIs will be built, and who owns the interface design?`,
      r: `Third-party integrations for ${name} must be identified early to assess build effort and fit within Cloud PE extensibility boundaries.`,
      i: `Late identification of external integrations for ${name} will delay go-live and increase implementation risk.`,
      n: `All external integrations must use SAP Integration Suite (BTP) APIs or pre-delivered connectors. No direct RFC or function module calls in Cloud PE.`,
      l3: "Integration", l4: "External System Integration"
    },
    // ⑥ Reporting & Analytics — 1 question
    {
      cat: "reporting", phase: "Realize", fitGap: "Fit", complexity: "Low",
      owner: "Business Process Owner",
      q: `What KPIs, operational reports, and management dashboards are required for ${name}? Will SAP Analytics Cloud (SAC), embedded Fiori analytical apps, or SAP Datasphere be used?`,
      r: `Reporting requirements for ${name} must be confirmed to configure the correct analytics tools and KPI definitions.`,
      i: `Without defined reporting, ${name} performance cannot be monitored and management decisions will lack timely data.`,
      n: `Use Fiori embedded analytics for operational reports. SAP Analytics Cloud for management dashboards. Configure KPIs via Manage KPIs (Fiori).`,
      l3: "Reporting & Analytics", l4: "KPI & Report Design"
    },
    // ⑦ Authorisation — 1 question
    {
      cat: "auth", phase: "Deploy", fitGap: "Fit", complexity: "Medium",
      owner: "IT Security Lead",
      q: `What SAP business roles and Fiori catalogue/space assignments will be defined for ${name} to enforce segregation of duties (e.g. creator vs. approver, initiator vs. poster)?`,
      r: `Role design for ${name} must comply with the client's SoD policy and external audit requirements.`,
      i: `Without proper role design, users of ${name} may gain excessive access, creating audit findings and compliance risks at go-live.`,
      n: `Assign and maintain roles via Maintain Business Roles (Fiori). All authorisations are managed in Cloud PE — no backend role maintenance tools.`,
      l3: "Authorisation", l4: "Role Design & SoD"
    },
    // ⑧ Data Migration — 2 questions
    {
      cat: "migration", phase: "Deploy", fitGap: "Partial Fit", complexity: "High",
      owner: "Data Migration Lead",
      q: steps[4]
        ? `${name} includes the step ${s(4, "data-related step")} which depends on migrated legacy data. What historical records, open items, and balances need to be loaded via SAP Data Migration Cockpit?`
        : `What legacy data objects (${docs.split(",").slice(0,2).join(",")}) need to be migrated for ${name} using SAP Data Migration Cockpit, and what is the estimated volume?`,
      r: `Defining data migration scope for ${name} early allows time for extraction mapping, cleansing, and iterative mock runs.`,
      i: `Incomplete migration planning for ${name} results in missing data at go-live and manual reconciliation effort.`,
      n: `Use SAP Data Migration Cockpit (available in Cloud PE) with standard migration objects. No LSMW or legacy migration tools.`,
      l3: "Data Migration", l4: "Migration Scope & Volume"
    },
    {
      cat: "migration", phase: "Deploy", fitGap: "Fit", complexity: "Medium",
      owner: "Data Migration Lead",
      q: `What are the data quality acceptance criteria, reconciliation tolerances, and cutover window for ${name} migration? How many mock migration runs will be planned?`,
      r: `Clear acceptance criteria and a structured cutover plan ensure ${name} migration is verified before go-live.`,
      i: `Without defined cutover criteria and mock runs, ${name} migration sign-off will be delayed, risking the go-live date.`,
      n: `Plan minimum 2 mock migration runs during Realize phase. Final cutover in Deploy. Use Data Migration Cockpit error logs for reconciliation.`,
      l3: "Data Migration", l4: "Cutover Strategy & Acceptance Criteria"
    },
    // ⑨ Org Structure — 1 question
    {
      cat: "orgstructure", phase: "Explore", fitGap: "Fit", complexity: "Medium",
      owner: "Business Process Owner",
      q: `What organisational structure assignments (${orgs}) are required to activate ${name}, and have all org unit decisions been signed off by the business?`,
      r: `Org structure decisions for ${name} are foundational — all downstream configuration, testing, and data migration depend on them.`,
      i: `Incorrect or late org structure decisions will require rework across all ${name} configuration, test scripts, and data loads.`,
      n: `Define and configure org structure via SAP S/4HANA Cloud Fiori configuration apps. Org structure changes after go-live are very costly.`,
      l3: "Org Structure", l4: "Org Unit Assignments"
    },
    // ⑩ Number Ranges — 1 question
    {
      cat: "numberranges", phase: "Realize", fitGap: "Fit", complexity: "Low",
      owner: "Business Process Owner",
      q: `What number range intervals will be configured for ${name} documents (e.g. ${docs.split(",")[0].trim()}), and will internal or external number assignment be used for each document type?`,
      r: `Number range design for ${name} must accommodate projected transaction volumes and any legal or regulatory numbering requirements.`,
      i: `Without number range configuration, ${name} document creation will fail immediately at go-live.`,
      n: `Configure number ranges via Manage Number Range Intervals (Fiori) in Cloud PE. No SNRO or backend number range tools.`,
      l3: "Number Ranges & Outputs", l4: "Number Range Configuration"
    }
  ];

  return questions.map((q, i) => ({
    processId:        `${id}-KDD-${String(i+1).padStart(3,"0")}`,
    kddId:            `${id}-KDD-${String(i+1).padStart(3,"0")}`,
    scopeItemId:      id,
    scopeItemName:    name,
    designQuestion:   q.q,
    fitGap:           q.fitGap,
    complexity:       q.complexity,
    status:           "Open",
    source:           "SAP for Me - Process Navigator",
    dataSource:       "SAP for Me",
    module:           mod,
    l1:               item.lob,
    l2:               name,
    l3:               q.l3,
    l4:               q.l4,
    decisionMade:     "",
    rationale:        q.r,
    impactActions:    q.i,
    decisionOwner:    q.owner,
    sapConsultant:    "",
    notes:            q.n,
    ragStatus:        helpers.getRAGStatus({ fitGap: q.fitGap, complexity: q.complexity }),
    sapActivatePhase: q.phase,
    configEffort:     helpers.getConfigEffort({ fitGap: q.fitGap, complexity: q.complexity }),
    integrationFlags: helpers.getIntegrationFlags({ designQuestion: q.q, impactActions: q.i, rationale: q.r })
  }));
}

function buildOverview(item) {
  const mod = LOB_MODULE[item.lob] || "Cross";
  return {
    id:               item.id,
    name:             item.name,
    module:           mod,
    lob:              item.lob,
    version:          cat.version,
    overview:         extractOverview(item.description),
    keyProcessFlow:   extractSteps(item.description).map((s, i) => `${i+1}. ${s}`).join("\n"),
    businessBenefits: extractBenefits(item.description).map((s, i) => `${i+1}. ${s}`).join("\n"),
    source:           "SAP for Me"
  };
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log(`\n🔄 Pre-generating KDD cache for ${cat.processes.length} scope items...\n`);
const cache = {};
let done = 0;

for (const item of cat.processes) {
  cache[item.id] = {
    rows:     generateKDDs(item),
    overview: buildOverview(item)
  };
  done++;
  if (done % 100 === 0 || done === cat.processes.length) {
    process.stdout.write(`   ${done} / ${cat.processes.length} items done\n`);
  }
}

const outPath = path.join(ROOT, "kdd-cache.json");
fs.writeFileSync(outPath, JSON.stringify({ version: cat.version, generatedAt: new Date().toISOString(), items: cache }), "utf8");

const totalKDDs = Object.values(cache).reduce((s, v) => s + v.rows.length, 0);
console.log(`\n✅  kdd-cache.json written`);
console.log(`   Items cached : ${Object.keys(cache).length}`);
console.log(`   Total KDDs   : ${totalKDDs.toLocaleString()}`);
console.log(`   File size    : ${(fs.statSync(outPath).size / 1024 / 1024).toFixed(1)} MB\n`);
