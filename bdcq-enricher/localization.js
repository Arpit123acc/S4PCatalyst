"use strict";

// ── Country localisation rules ─────────────────────────────────────────────────
// processAreaKeywords: matched case-insensitively against Process Area + SSCUI + Topic + Question

const COUNTRY_RULES = {
  IN: {
    name: "India",
    rules: [
      {
        processAreaKeywords: ["tax", "finance", "payable", "receivable", "payment", "accounting", "invoice", "ledger"],
        requirement: "GST — Goods and Services Tax (tax procedure TAXIN)",
        basis: "GST Act 2017",
        mandatory: true,
        cloudPEApproach: "Standard GST config via Manage Tax Codes (Fiori). Tax procedure TAXIN. No custom development required.",
        confirmPrompt: "Which entities are GST-registered? Confirm GSTINs per state of operation."
      },
      {
        processAreaKeywords: ["invoice", "einvoice", "e-invoice", "irp", "billing", "sales", "customer invoice"],
        requirement: "E-invoicing via IRP (Invoice Registration Portal) — mandatory for B2B above INR 5Cr annual turnover",
        basis: "CBIC e-invoicing mandate (phased by turnover threshold)",
        mandatory: true,
        cloudPEApproach: "SAP Document and Reporting Compliance (DRC) with IRP integration via SAP Integration Suite (BTP). Standard in Cloud PE.",
        confirmPrompt: "Which entities exceed INR 5Cr annual turnover? Is SAP Integration Suite included in your BTP subscription?"
      },
      {
        processAreaKeywords: ["withhold", "tds", "tcs", "vendor", "payable", "procurement", "supplier"],
        requirement: "TDS — Section 194C (contractors), 194J (professional fees), 194H (commission), 194Q (purchase of goods)",
        basis: "Income Tax Act 1961",
        mandatory: true,
        cloudPEApproach: "Standard withholding tax config via Manage Withholding Tax (Fiori). TAN registration required per entity.",
        confirmPrompt: "Which vendor categories are subject to TDS? What rates apply per section? Is TAN registered for each entity?"
      },
      {
        processAreaKeywords: ["payment", "bank", "treasury", "payable", "disbursement"],
        requirement: "NEFT/RTGS — mandatory electronic payment formats for vendor disbursements",
        basis: "RBI payment system guidelines",
        mandatory: true,
        cloudPEApproach: "SAP standard NEFT/RTGS payment medium formats. Bank connectivity via direct file transfer or SAP Integration Suite.",
        confirmPrompt: "Which bank is used for vendor payments? Direct file upload or host-to-host integration?"
      },
      {
        processAreaKeywords: ["delivery", "goods movement", "logistics", "shipping", "transport", "warehouse"],
        requirement: "e-Way Bill — mandatory for inter-state goods movement above INR 50,000",
        basis: "GST e-Way Bill rules (Rule 138)",
        mandatory: true,
        cloudPEApproach: "e-Way Bill integration via SAP Integration Suite (BTP). Triggered on goods issue or delivery.",
        confirmPrompt: "What is the volume of inter-state goods movements per month? Auto-generation required or manual portal upload?"
      }
    ]
  },

  DE: {
    name: "Germany",
    rules: [
      {
        processAreaKeywords: ["finance", "accounting", "document", "archive", "ledger", "journal"],
        requirement: "GoBD — audit-proof document archiving, 10-year retention for accounting records",
        basis: "GoBD guidelines (BMF letter 2019)",
        mandatory: true,
        cloudPEApproach: "SAP Document Management with retention policies via SAP BTP Document Management Service. No custom archiving needed.",
        confirmPrompt: "Which document types require GoBD-compliant archiving? Is an existing archive system (ILM) in scope?"
      },
      {
        processAreaKeywords: ["payment", "bank", "sepa", "treasury", "payable"],
        requirement: "SEPA payment formats — SCT (Credit Transfer) and SDD (Direct Debit)",
        basis: "EU SEPA Regulation 260/2012",
        mandatory: true,
        cloudPEApproach: "SAP standard SEPA payment medium. EBICS bank connectivity via SAP Integration Suite or direct file transfer.",
        confirmPrompt: "Which banks are used? Is EBICS T or EBICS TS required for secure bank connectivity?"
      },
      {
        processAreaKeywords: ["tax", "vat", "mwst", "finance", "receivable", "billing"],
        requirement: "Umsatzsteuer (VAT) — 19% standard, 7% reduced, OSS for EU cross-border B2C sales",
        basis: "Umsatzsteuergesetz (UStG)",
        mandatory: true,
        cloudPEApproach: "Standard tax configuration via Manage Tax Codes. OSS scheme handled via standard EU VAT config.",
        confirmPrompt: "Are there EU cross-border B2C sales requiring OSS registration? Confirm USt-IdNr (VAT ID) per entity."
      },
      {
        processAreaKeywords: ["invoice", "billing", "output", "customer invoice", "sales"],
        requirement: "XRechnung / ZUGFeRD — e-invoicing mandatory for invoices to German public sector",
        basis: "E-Rechnungsverordnung (ERechV)",
        mandatory: false,
        cloudPEApproach: "SAP Document and Reporting Compliance supports XRechnung and ZUGFeRD. Standard config activation.",
        confirmPrompt: "Does the client have German public sector (Bundesbehörden / Länder / Kommunen) customers?"
      }
    ]
  },

  US: {
    name: "USA",
    rules: [
      {
        processAreaKeywords: ["tax", "sales tax", "use tax", "sales", "billing", "receivable", "order"],
        requirement: "US Sales and use tax — multi-state nexus, post-South Dakota v. Wayfair economic nexus rules",
        basis: "State tax laws (50 states + DC — rates and rules differ by state)",
        mandatory: true,
        cloudPEApproach: "SAP Tax Compliance Integration with Vertex or Avalara via SAP Integration Suite. Standard SAP tax config alone is insufficient for US multi-state tax.",
        confirmPrompt: "In which states does the client have physical or economic nexus? Is Vertex or Avalara already licensed?"
      },
      {
        processAreaKeywords: ["payment", "bank", "ach", "nacha", "treasury", "payable"],
        requirement: "ACH payment format (NACHA) — standard for US electronic vendor and payroll payments",
        basis: "NACHA Operating Rules",
        mandatory: true,
        cloudPEApproach: "SAP standard ACH payment medium (CCD, CTX, PPD formats). Bank connectivity via direct file or SAP Integration Suite.",
        confirmPrompt: "Which bank is used? Is ACH CCD, CTX, or PPD format required per payment type?"
      },
      {
        processAreaKeywords: ["tax", "1099", "withhold", "vendor", "payable", "procurement"],
        requirement: "1099 reporting — Form 1099-MISC, 1099-NEC for qualifying vendor payments",
        basis: "IRS reporting requirements (threshold: $600+)",
        mandatory: true,
        cloudPEApproach: "Standard withholding tax and 1099 reporting via SAP Document and Reporting Compliance.",
        confirmPrompt: "Which vendor categories are subject to 1099 (attorneys, independent contractors, rent)? Volume of 1099-eligible vendors?"
      },
      {
        processAreaKeywords: ["authoriz", "access", "role", "security", "audit", "control", "segregation"],
        requirement: "SOX compliance — SoD controls, audit trail, internal controls over financial reporting",
        basis: "Sarbanes-Oxley Act Sections 302 and 404",
        mandatory: false,
        cloudPEApproach: "Standard SAP role-based access + audit log (SAP Audit Management). SoD analysis via standard SAP GRC or third-party tool.",
        confirmPrompt: "Is the client publicly listed on a US stock exchange (SEC registrant requiring SOX 404)?"
      }
    ]
  },

  GB: {
    name: "UK",
    rules: [
      {
        processAreaKeywords: ["tax", "vat", "finance", "accounting", "receivable", "billing"],
        requirement: "Making Tax Digital (MTD) — VAT return submission via HMRC API mandatory for VAT-registered businesses",
        basis: "HMRC MTD for VAT regulations (mandatory from April 2022 for all VAT-registered)",
        mandatory: true,
        cloudPEApproach: "SAP Document and Reporting Compliance with HMRC MTD API integration. Standard in Cloud PE.",
        confirmPrompt: "What is the VAT registration number? What is the filing frequency (monthly or quarterly)?"
      },
      {
        processAreaKeywords: ["payment", "bank", "bacs", "faster payment", "chaps", "treasury", "payable"],
        requirement: "BACS / Faster Payments — standard UK electronic payment formats",
        basis: "Pay.UK scheme rules",
        mandatory: true,
        cloudPEApproach: "SAP standard BACS payment medium. Bank connectivity via direct file or SAP Integration Suite.",
        confirmPrompt: "Which UK clearing method is required: BACS Direct Credit, Faster Payments, or CHAPS?"
      },
      {
        processAreaKeywords: ["contractor", "payroll", "hr", "worker", "resource"],
        requirement: "IR35 — off-payroll working rules for contractors working through intermediaries",
        basis: "Finance Act 2021 (Chapter 10, ITEPA 2003)",
        mandatory: false,
        cloudPEApproach: "IR35 determination is a process/tax decision. SAP HCM payroll handles deductions once classification is confirmed.",
        confirmPrompt: "Does the client engage contractors? How many are in scope for IR35 status determination?"
      }
    ]
  },

  BR: {
    name: "Brazil",
    rules: [
      {
        processAreaKeywords: ["invoice", "billing", "sales", "receivable", "tax", "nfe", "nota fiscal"],
        requirement: "Nota Fiscal eletrônica (NFe) — mandatory for all goods sales; NFS-e for services",
        basis: "Convênio ICMS 110/2008 and state SEFAZ regulations",
        mandatory: true,
        cloudPEApproach: "SAP Document and Reporting Compliance with SEFAZ integration via SAP Integration Suite. State-specific rules apply — this is high complexity.",
        confirmPrompt: "In which Brazilian states does the client operate? Daily NFe volume? Brazil-certified SAP partner engaged?"
      },
      {
        processAreaKeywords: ["tax", "icms", "pis", "cofins", "ipi", "finance", "accounting"],
        requirement: "Brazilian indirect taxes — ICMS, PIS/COFINS, IPI with complex state and federal rules",
        basis: "Brazilian Constitution + complementary tax laws",
        mandatory: true,
        cloudPEApproach: "SAP Brazil localisation package. Requires Brazil-specific SAP expertise — confirm implementation partner has Brazil tax capability.",
        confirmPrompt: "What tax regime applies: Lucro Real, Lucro Presumido, or Simples Nacional? Brazil-certified SAP implementor engaged?"
      },
      {
        processAreaKeywords: ["company code", "legal entity", "org structure", "cnpj", "entity"],
        requirement: "CNPJ per legal entity — one company code per CNPJ mandatory",
        basis: "Brazilian company registration law (Receita Federal)",
        mandatory: true,
        cloudPEApproach: "One SAP company code per CNPJ. This is a hard org structure constraint — must be resolved before company code design.",
        confirmPrompt: "How many Brazilian legal entities (CNPJs) are in scope? Is the legal entity / holding structure confirmed?"
      }
    ]
  },

  AE: {
    name: "UAE",
    rules: [
      {
        processAreaKeywords: ["tax", "vat", "finance", "accounting", "invoice", "billing"],
        requirement: "UAE VAT 5% — mandatory for taxable supplies since January 2018",
        basis: "UAE VAT Law (Federal Decree-Law No. 8 of 2017)",
        mandatory: true,
        cloudPEApproach: "Standard tax configuration via Manage Tax Codes. Zero-rated (exports, certain services) and exempt supplies need specific config.",
        confirmPrompt: "What is the TRN (Tax Registration Number)? Which supplies are zero-rated or exempt? Is there a tax group registration?"
      }
    ]
  },

  SA: {
    name: "Saudi Arabia",
    rules: [
      {
        processAreaKeywords: ["tax", "vat", "finance", "accounting", "invoice", "billing"],
        requirement: "KSA VAT 15% — mandatory since July 2020",
        basis: "KSA VAT Implementing Regulations (ZATCA)",
        mandatory: true,
        cloudPEApproach: "Standard tax configuration. Confirm VAT group structure and intra-GCC transaction treatment.",
        confirmPrompt: "What is the VAT registration number (TIN)? Are there intra-GCC supplies needing special treatment?"
      },
      {
        processAreaKeywords: ["invoice", "billing", "einvoice", "e-invoice", "fattura", "zatca", "fatoora"],
        requirement: "ZATCA Phase 2 e-invoicing (Fatoora) — mandatory B2B and B2G integration with ZATCA platform",
        basis: "ZATCA E-Invoicing Regulation — wave-based rollout by annual revenue threshold",
        mandatory: true,
        cloudPEApproach: "SAP Document and Reporting Compliance with ZATCA Fatoora integration via SAP Integration Suite. Confirm ZATCA compliance wave.",
        confirmPrompt: "Which ZATCA compliance wave applies to this entity (check annual revenue threshold)? What is the confirmed compliance go-live date?"
      },
      {
        processAreaKeywords: ["output", "form", "invoice", "document", "language", "arabic"],
        requirement: "Arabic language — all legal commercial documents must include Arabic text",
        basis: "Saudi Arabian language and commercial regulations",
        mandatory: true,
        cloudPEApproach: "SAP standard Arabic language pack. SAP Document and Reporting Compliance supports bilingual (Arabic + English) output forms.",
        confirmPrompt: "Are output documents required in Arabic only or bilingual (Arabic + English)? Confirm for each document type (invoice, PO, delivery note)."
      }
    ]
  },

  FR: {
    name: "France",
    rules: [
      {
        processAreaKeywords: ["finance", "accounting", "audit", "tax", "ledger", "journal"],
        requirement: "FEC (Fichier des Écritures Comptables) — mandatory digital audit file for tax authority inspections",
        basis: "Livre des procédures fiscales — Article L47 A-I",
        mandatory: true,
        cloudPEApproach: "SAP Financial Audit File export (FEC format) is standard in Cloud PE via financial reporting tools.",
        confirmPrompt: "Is the fiscal year calendar-year or non-standard? The FEC format has specific chart of accounts structure requirements."
      },
      {
        processAreaKeywords: ["invoice", "billing", "sales", "receivable", "customer invoice"],
        requirement: "Chorus Pro e-invoicing — mandatory for all invoices to French public sector (B2G)",
        basis: "Ordonnance n° 2014-697 (Chorus Pro portal)",
        mandatory: false,
        cloudPEApproach: "SAP Document and Reporting Compliance supports Chorus Pro format. Standard activation.",
        confirmPrompt: "Does the client invoice French public sector bodies (État, collectivités locales, établissements publics)?"
      }
    ]
  },

  AU: {
    name: "Australia",
    rules: [
      {
        processAreaKeywords: ["tax", "gst", "finance", "accounting", "bas", "activity statement"],
        requirement: "Australian GST 10% — quarterly Business Activity Statement (BAS) filing with ATO",
        basis: "A New Tax System (Goods and Services Tax) Act 1999",
        mandatory: true,
        cloudPEApproach: "Standard tax configuration via Manage Tax Codes. BAS report via SAP Document and Reporting Compliance.",
        confirmPrompt: "What is the ABN (Australian Business Number)? BAS filing frequency (monthly/quarterly/annual)? Which supplies are GST-free (health, education, fresh food, exports)?"
      },
      {
        processAreaKeywords: ["hr", "payroll", "employee", "pay", "stp", "single touch"],
        requirement: "Single Touch Payroll Phase 2 (STP2) — mandatory ATO payroll reporting each pay event",
        basis: "Tax Administration Act 1953 (STP Phase 2 mandatory from Jan 2022)",
        mandatory: true,
        cloudPEApproach: "SAP HCM payroll with STP2 integration via SAP Integration Suite. Confirm payroll is running in SAP.",
        confirmPrompt: "Is payroll processed in SAP or an external system? How many employees per entity? STP2 go-live date confirmed with ATO?"
      }
    ]
  },

  SG: {
    name: "Singapore",
    rules: [
      {
        processAreaKeywords: ["tax", "gst", "finance", "accounting", "invoice", "billing"],
        requirement: "Singapore GST 9% (from Jan 2024) — mandatory for GST-registered businesses above SGD 1M turnover",
        basis: "Goods and Services Tax Act (Cap. 117A)",
        mandatory: true,
        cloudPEApproach: "Standard tax configuration via Manage Tax Codes. GST F5/F7 return via SAP Document and Reporting Compliance.",
        confirmPrompt: "What is the GST registration number? Any exempt supplies (financial services, residential property) to configure?"
      },
      {
        processAreaKeywords: ["invoice", "einvoice", "e-invoice", "peppol", "invoicenow"],
        requirement: "InvoiceNow (Peppol e-invoicing) — mandatory for GST-registered businesses from 2025",
        basis: "IMDA InvoiceNow mandate (phased rollout 2025–2026)",
        mandatory: true,
        cloudPEApproach: "SAP Document and Reporting Compliance with Peppol/InvoiceNow integration. Confirm rollout wave.",
        confirmPrompt: "Is the client GST-registered? Which InvoiceNow mandate wave applies? Peppol access point provider identified?"
      }
    ]
  }
};

// ── Industry rules ────────────────────────────────────────────────────────────

const INDUSTRY_RULES = {
  manufacturing: {
    name: "Manufacturing",
    processAreaThemes: {
      default: "Manufacturing configuration requires BOM, routing, and production order decisions that differ significantly from service industries.",
      production: "Production strategy (make-to-stock, make-to-order, engineer-to-order) must be defined per product group before configuring production orders and MRP.",
      material: "Material master configuration for manufactured items requires additional views (MRP, Work Scheduling) with manufacturing-specific parameters.",
      mrp: "MRP planning parameters — lot sizing, safety stock, planning horizon, and lead times — directly impact procurement volumes and stock levels.",
      quality: "Quality inspection plans and usage decisions must align with manufacturing process steps and any regulatory requirements (ISO, GMP).",
      costing: "Standard cost vs. moving average price for manufactured materials determines how production variances are settled and impacts P&L reporting.",
      bom: "BOM complexity (multi-level, phantoms, co-products, by-products) must be assessed before configuring production planning."
    },
    mustAsk: [
      "What is the maximum BOM depth and are phantom assemblies or co-products used?",
      "Which production types apply: discrete, process, repetitive, or project-based manufacturing?",
      "How are production cost variances settled — standard cost rollup or actual costing (COGS)?",
      "Is subcontracting (external manufacturing / contract manufacturing) in scope?"
    ],
    excludeKeywords: ["pos integration", "season", "markdown", "patient record", "billing cycle", "meter reading", "retail store"]
  },

  retail: {
    name: "Retail",
    processAreaThemes: {
      default: "Retail configuration requires decisions around high transaction volumes, season planning, and channel fulfilment unique to the vertical. When describing project teams or roles, use Retail-specific titles such as Store Rollout Lead, POS Integration Specialist, Omnichannel Architect, Merchandising Consultant, or Seasonal Workforce Planner — not generic 'Senior Consultant / Junior Developer' titles.",
      project: "Retail implementation projects are structured around store rollout phases, POS go-live events, and seasonal trade periods. Project milestones should reference store waves, cut-over weekends, and peak-trade blackout windows (e.g. avoid go-live during peak season). Key roles: Retail Workstream Lead, Store Enablement Manager, POS Integration Lead.",
      resource: "Retail project resources differ from generic consulting engagements. Typical roles include: Store Rollout Lead (owns site-by-site go-live), Omnichannel Architect (POS/SAP integration design), Merchandising Consultant (article master, pricing, markdown config), Seasonal Planning Lead (demand planning and safety stock for peak periods), and Change Lead (store manager adoption programme).",
      sales: "Retail sales configuration must account for high daily transaction volumes, multi-channel returns handling, and POS integration — not typical in other industries.",
      pricing: "Retail pricing includes markdowns, promotions, and season-specific pricing requiring specific SAP condition type configuration.",
      inventory: "Retail inventory management involves replenishment automation, seasonal safety stock, and potential VMI (vendor-managed inventory) scenarios.",
      article: "Article master in retail has specific attributes (season, collection, colour/size variants) that drive configuration complexity.",
      organisation: "Retail org structure typically maps SAP company code to retail banner/legal entity, plants to distribution centres, and storage locations to store back-rooms or shop-floor stock. Org design decisions made here directly affect store-level stock visibility."
    },
    mustAsk: [
      "How many stores and channels (online, wholesale, marketplace) are in scope?",
      "Which POS system is used and how is end-of-day posting handled — real-time or daily batch?",
      "How are promotions, markdowns, and loyalty managed — within SAP or an external system?",
      "What is peak daily transaction volume and how is batch posting performance managed?",
      "Is the implementation structured as a template rollout (pilot store → wave rollouts) and what is the go-live blackout window around peak trading?"
    ],
    excludeKeywords: ["production order", "bom", "routing", "mrp", "plant maintenance", "batch manufacturing", "work center"]
  },

  "professional services": {
    name: "Professional Services",
    processAreaThemes: {
      default: "Professional services configuration centres on project billing, time capture, and revenue recognition — fundamentally different from product businesses.",
      project: "Project billing model (T&M, fixed price, milestone, retainer) must be defined per engagement type before configuring billing rules and WBS structures.",
      time: "Time recording configuration determines how consultant hours flow from timesheets to project cost and client billing.",
      revenue: "Revenue recognition under IFRS 15 / ASC 606 — percentage of completion vs. completed contract — must be designed before configuring contract accounts.",
      resource: "Resource management configuration depends on whether internal, external, and partner resources are tracked and billed separately.",
      intercompany: "Intercompany project billing configuration is required if the delivering entity differs from the contracting entity."
    },
    mustAsk: [
      "What billing model applies per engagement type: T&M, fixed price, milestone, or retainer?",
      "Is revenue recognised at project milestone or percentage of completion (IFRS 15 / ASC 606)?",
      "Does the business bill intercompany between the delivering and contracting legal entities?",
      "Is time recording in SAP standard or integrated from a third-party timesheet tool (e.g. Replicon, Harvest)?"
    ],
    excludeKeywords: ["production order", "bom", "mrp", "plant maintenance", "pos", "season", "markdown", "meter reading"]
  },

  pharma: {
    name: "Pharma / Life Sciences",
    processAreaThemes: {
      default: "Pharma configuration requires batch management, GxP compliance, and serialisation decisions not applicable to other industries.",
      batch: "Batch management is mandatory for regulated pharmaceutical products — batch classification, shelf-life, and recall handling must be configured from day one.",
      quality: "Quality inspection plans must meet GMP requirements. Usage decisions in pharma carry regulatory consequences — skipping an inspection is not possible.",
      serial: "Serialisation requirements (DSCSA for US, FMD for EU, others) must be confirmed early — they significantly impact material master, delivery, and integration scope.",
      gxp: "Computer System Validation in Cloud PE: SAP runs on a Pre-Validated Cloud Service model. The client validates only Key User extensions and integrations — not the standard platform. This must be confirmed with regulatory affairs.",
      recall: "Recall handling must be designed end-to-end: batch traceability from supplier to patient, customer notification process, and reversal postings."
    },
    mustAsk: [
      "Which products require batch management and at what level (raw material, semi-finished, finished goods)?",
      "What serialisation standard applies — DSCSA (US), FMD (EU) — and is aggregation required?",
      "What regulatory framework applies: FDA 21 CFR Part 11, EU GMP Annex 11, ICH?",
      "Has your regulatory affairs team confirmed they accept SAP's Pre-Validated Cloud Service model?"
    ],
    excludeKeywords: ["pos", "season", "markdown", "retail store", "meter reading", "billing cycle"]
  },

  utilities: {
    name: "Utilities",
    processAreaThemes: {
      default: "Utilities configuration requires contract account management, billing cycle design, and device management decisions specific to the sector.",
      billing: "Billing cycle configuration (monthly, bimonthly, on-demand) must align with meter reading frequency and any regulatory requirements on billing periods.",
      credit: "Credit management in utilities involves high-volume small-balance collections and disconnection / reconnection processes not typical in other industries.",
      contract: "Contract account configuration determines the structure of customer accounts, meter points, and the posting of billing documents.",
      device: "Device management configuration covers meter master data, reading order generation, and integration with AMI or smart meter platforms."
    },
    mustAsk: [
      "Is the energy market regulated or deregulated in your geography — this impacts pricing and supply configuration?",
      "How are meter readings collected: manual, AMI, or smart meter — and what is the read frequency?",
      "What is the billing cycle and what triggers billing runs — date-based or reading-based?",
      "What is the disconnection / reconnection process and is it managed in SAP or a field service system?"
    ],
    excludeKeywords: ["production order", "bom", "routing", "mrp", "pos", "season", "markdown", "patient record"]
  },

  "financial services": {
    name: "Financial Services",
    processAreaThemes: {
      default: "Financial services configuration requires regulatory reporting, strict SoD controls, and data residency decisions not typical in other industries.",
      reporting: "Regulatory reporting (Basel III/IV, IFRS 17 for insurance, Solvency II) must be confirmed before designing the chart of accounts and profit centre structure.",
      authoriz: "Segregation of duties requirements for financial regulators are more stringent than standard SAP defaults — role design must be reviewed with the compliance team before Realize phase.",
      data: "Data residency: confirm SAP BTP data centre region (EU / US / APAC) meets your regulator's data localisation requirements before signing the Cloud contract."
    },
    mustAsk: [
      "Which regulatory reporting frameworks apply: Basel III/IV, IFRS 17, Solvency II, local banking regulations?",
      "What are your data residency requirements and does the SAP BTP region cover them?",
      "What SoD controls does your regulator (FCA, RBI, MAS, FINMA) mandate and how are they audited?"
    ],
    excludeKeywords: ["production order", "bom", "mrp", "pos", "season", "batch manufacturing", "meter reading"]
  },

  "oil & gas": {
    name: "Oil & Gas",
    processAreaThemes: {
      default: "Oil & Gas configuration requires joint venture accounting, hydrocarbon volume management, and asset-intensive maintenance decisions.",
      asset: "Functional location hierarchy and equipment master must reflect the O&G asset structure (field, well, platform, pipeline) before configuring maintenance plans.",
      procurement: "Long-lead item procurement and CAPEX project purchasing in O&G require specific procurement configuration not typical in manufacturing.",
      finance: "Joint venture accounting configuration determines how costs and revenues are allocated between JV partners — this is a high-complexity, project-critical design decision.",
      revenue: "Revenue recognition for hydrocarbon sales involves production sharing agreements, royalty calculations, and variable pricing not found in standard product businesses."
    },
    mustAsk: [
      "Is joint venture accounting in scope and how many JV partners are there?",
      "How are hydrocarbon volumes tracked and in which unit of measure (bbl, mcf, MMBtu)?",
      "Are production sharing agreements in scope and how is royalty calculated?",
      "What is the functional location hierarchy for your asset base (field → platform → well → equipment)?"
    ],
    excludeKeywords: ["pos", "season", "markdown", "retail", "patient record", "billing cycle", "meter reading"]
  },

  "high tech": {
    name: "High Tech",
    processAreaThemes: {
      default: "High Tech configuration involves complex product structures, subscription revenue recognition, and global outsourced supply chains.",
      sales: "Software and subscription revenue recognition under IFRS 15 / ASC 606 — multi-element arrangements and variable consideration — must be designed carefully before configuring contract accounts.",
      production: "Configure-to-order and engineer-to-order scenarios common in High Tech require variant configuration decisions before production setup.",
      supply: "High Tech typically has a global outsourced (contract) manufacturing network requiring integration with CMO systems and complex goods receipt flows."
    },
    mustAsk: [
      "Are software licences or subscriptions in scope — if so, how is revenue recognised under IFRS 15 / ASC 606?",
      "Is configure-to-order or engineer-to-order manufacturing in scope?",
      "Is there a global contract manufacturing network that needs to be integrated with SAP?"
    ],
    excludeKeywords: ["meter reading", "billing cycle", "patient record", "clinical", "deregulated market"]
  }
};

// ── Cloud PE constraint reference ─────────────────────────────────────────────

const CLOUDPE_CONSTRAINTS = [
  {
    keywords: ["custom report", "bespoke report", "abap report", "custom layout", "crystal report", "business object report"],
    type: "Gap",
    expectation: "Custom ABAP reports or Crystal Reports",
    reality: "SAP Analytics Cloud (SAC) or Fiori embedded analytics only. No custom ABAP reports in Cloud PE.",
    extensionPath: "SAP Analytics Cloud story built on CDS views. CDS view extensions via Key User In-App."
  },
  {
    keywords: ["workflow", "approval workflow", "custom workflow", "escalation", "multi-step approval"],
    type: "Partial",
    expectation: "Custom ABAP workflow with complex routing and multi-level escalation",
    reality: "SAP standard workflow via Manage Workflows Fiori app covers most approval scenarios. Complex multi-condition routing may need BTP Process Automation.",
    extensionPath: "Key User In-App for standard approval rules. SAP Build Process Automation (BTP) for complex multi-step workflows."
  },
  {
    keywords: ["form", "output form", "sapscript", "smartform", "print layout", "pdf template", "form template"],
    type: "Partial",
    expectation: "Custom SAPscript or SmartForms layouts",
    reality: "Adobe Forms (PDF) output in Cloud PE. Templates can be adapted via Key User In-App Extension.",
    extensionPath: "Key User In-App for form template changes. SAP BTP Document Service for fully custom branded documents."
  },
  {
    keywords: ["interface", "integration", "rfc call", "bapi", "idoc", "middleware", "point-to-point", "direct connect"],
    type: "Partial",
    expectation: "Point-to-point BAPI/RFC or IDoc integration with external systems",
    reality: "All integrations in Cloud PE go through SAP Integration Suite (BTP). No direct RFC from external systems.",
    extensionPath: "SAP Integration Suite — standard integration content available in Integration Content Advisor."
  },
  {
    keywords: ["table maintenance", "configuration table", "sm30", "sm31", "sm34", "direct table", "maintain entries"],
    type: "Gap",
    expectation: "Direct table maintenance via SM30/SM31 transactions",
    reality: "Configuration only via SAP Fiori apps or Manage Your Solution. No SM30/SM31 available in Cloud PE.",
    extensionPath: "Key User In-App Configuration Extension for custom configuration tables."
  },
  {
    keywords: ["enhancement", "user exit", "badi", "enhancement spot", "cmod", "smod", "modification", "implicit enhancement"],
    type: "Partial",
    expectation: "Classic BAdI or user exit implementation in standard SAP code",
    reality: "Adaptation Point framework (In-App Extension) replaces classic BAdIs in Cloud PE. Restricted ABAP Cloud available for controlled extensibility.",
    extensionPath: "Key User In-App Extension (no coding) or Developer ABAP Cloud Extension (restricted, controlled)."
  },
  {
    keywords: ["custom authorization", "custom authorisation", "custom auth object", "su21", "authorization object"],
    type: "Partial",
    expectation: "Custom authorisation objects defined via SU21",
    reality: "Role design based on standard SAP Business Role templates only. Custom authorisation objects not supported in Cloud PE.",
    extensionPath: "Key User In-App Role Extension — extend standard business roles with field-level access restrictions."
  },
  {
    keywords: ["background job", "batch job", "sm36", "custom job", "scheduled job", "job scheduling"],
    type: "Partial",
    expectation: "Custom ABAP batch jobs scheduled via SM36",
    reality: "Application Jobs via Schedule Application Jobs Fiori app. Custom background jobs via ABAP Cloud Developer Extension only.",
    extensionPath: "ABAP Cloud Developer Extension for custom application jobs (restricted, requires SAP approval)."
  },
  {
    keywords: ["variant configuration", "lo-vc", "variant config", "configurable material", "characteristic", "class type 300"],
    type: "Partial",
    expectation: "Full LO-VC (Logistics Variant Configuration) capability equivalent to ECC",
    reality: "Variant Configuration is supported in Cloud PE but with feature limitations vs. ECC LO-VC. Complex constraint nets and certain pricing procedures may have gaps.",
    extensionPath: "Validate specific VC requirements against Cloud PE feature scope list. Check SAP roadmap for parity items."
  },
  {
    keywords: ["direct database", "se16", "database query", "direct table read", "sql query", "raw table"],
    type: "Gap",
    expectation: "Direct database access via SE16 or custom SQL queries",
    reality: "No direct database access in Cloud PE. All data access via CDS views exposed as OData or SOAP APIs.",
    extensionPath: "None — use standard SAP APIs. Custom CDS views can be created via Developer ABAP Cloud Extension."
  }
];

// ── Normalisation helpers ──────────────────────────────────────────────────────

const COUNTRY_NAME_MAP = {
  "india": "IN", "in": "IN",
  "germany": "DE", "deutschland": "DE", "de": "DE",
  "usa": "US", "united states": "US", "us": "US", "america": "US", "u.s.": "US",
  "uk": "GB", "united kingdom": "GB", "britain": "GB", "england": "GB", "gb": "GB", "u.k.": "GB",
  "brazil": "BR", "brasil": "BR", "br": "BR",
  "uae": "AE", "united arab emirates": "AE", "dubai": "AE", "ae": "AE", "abu dhabi": "AE",
  "saudi": "SA", "saudi arabia": "SA", "ksa": "SA", "sa": "SA",
  "france": "FR", "fr": "FR",
  "australia": "AU", "au": "AU",
  "singapore": "SG", "sg": "SG"
};

const INDUSTRY_NAME_MAP = {
  "manufacturing": "manufacturing",
  "retail": "retail",
  "professional services": "professional services",
  "prof services": "professional services",
  "pso": "professional services",
  "ps": "professional services",
  "pharma": "pharma",
  "pharmaceutical": "pharma",
  "life sciences": "pharma",
  "lifesciences": "pharma",
  "utilities": "utilities",
  "utility": "utilities",
  "financial services": "financial services",
  "banking": "financial services",
  "insurance": "financial services",
  "fsi": "financial services",
  "oil & gas": "oil & gas",
  "oil and gas": "oil & gas",
  "o&g": "oil & gas",
  "energy": "oil & gas",
  "high tech": "high tech",
  "hightech": "high tech",
  "technology": "high tech",
  "tech": "high tech"
};

function normalizeCountries(input) {
  if (!input) return [];
  return input.split(/[,;]+/).map(c => {
    const key = c.trim().toLowerCase();
    return COUNTRY_NAME_MAP[key] || null;
  }).filter(Boolean);
}

function normalizeIndustry(input) {
  if (!input) return null;
  const key = input.trim().toLowerCase();
  return INDUSTRY_NAME_MAP[key] || key;
}

module.exports = {
  COUNTRY_RULES,
  INDUSTRY_RULES,
  CLOUDPE_CONSTRAINTS,
  normalizeCountries,
  normalizeIndustry
};
