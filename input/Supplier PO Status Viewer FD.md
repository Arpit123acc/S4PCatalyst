# Functional Design: Supplier Open PO Status Viewer

**Application:** Supplier Open PO Status Viewer
**SAAS Solution:** S/4HANA Cloud, Public Edition
**Solution Mode:** Side-by-side (BTP) — CAP Node.js + SAP UI5
**RICEFW Type:** R — Report
**Author:** Delivery Team
**Date:** 2026-07-31
**Status:** Draft

---

## 03. HIGH-LEVEL FUNCTIONAL SPECIFICATION

### 03.01 High-Level Business Requirements

Procurement and accounts-payable staff currently check the status of open purchase orders
by navigating to the standard **Manage Purchase Orders** Fiori app in S/4HANA Cloud.
There is no lightweight, embeddable, or externally shareable view that lets a supplier-facing
team quickly look up all open POs for a given supplier without full S/4HANA Cloud access.

The requirement is a **read-only BTP web application** that:

- Accepts a supplier (Business Partner) number or name fragment as search input.
- Retrieves all open Purchase Orders for that supplier from S/4HANA Cloud Public Edition
  via the **released OData V2 API** `API_PURCHASEORDER_PROCESS_SRV`.
- Displays the results in a sortable, filterable table showing PO number, PO date,
  purchasing organisation, total net amount, currency, and document status.
- Requires no custom fields, no ABAP development, and no changes to the S/4HANA Cloud tenant.
- Runs entirely on SAP BTP Cloud Foundry (no on-premise connectivity).

### 03.02 Related Scope Items

- MM — Procurement (standard scope item 11J: Purchase Order Processing)
- No custom scope items or add-ons involved.

---

## 04. DETAILED REQUIREMENTS

### 04.01 Functional Requirements

| ID   | Requirement | Priority |
|------|-------------|----------|
| FR-1 | The app shall display an input field for supplier number (exact match) and a supplier name search field (contains, case-insensitive). | Must |
| FR-2 | On search, the app shall call `API_PURCHASEORDER_PROCESS_SRV` / `A_PurchaseOrder` filtered by `Supplier eq '<value>'` and `PurchaseOrderStatus eq 'B'` (open). | Must |
| FR-3 | The results table shall show: PO Number, Supplier, Purchasing Org, PO Date, Net Amount, Currency, PO Status. | Must |
| FR-4 | The table shall support client-side sort on all columns and a live search filter. | Should |
| FR-5 | If no results are found, a friendly empty-state message shall be displayed. | Must |
| FR-6 | The app shall display a timestamp of when the data was last refreshed and a **Refresh** button. | Should |
| FR-7 | The app requires no authentication beyond the BTP app's own login (authenticated via BTP Identity Service). | Must |
| FR-8 | No data shall be written to S/4HANA Cloud — the app is strictly read-only. | Must |

### 04.02 Non-Functional Requirements

| ID    | Requirement |
|-------|-------------|
| NFR-1 | Response time for a supplier PO query returning up to 100 rows: < 3 seconds on EU10 BTP. |
| NFR-2 | The app must run on SAP BTP Cloud Foundry with the standard `nodejs_buildpack`. |
| NFR-3 | No hard-coded credentials — S/4HANA Cloud connectivity via a BTP Destination (OAuth2SAMLBearerAssertion or BasicAuthentication communication user). |
| NFR-4 | The app must not log any user input or PII to application logs. |
| NFR-5 | The CAP service layer must restrict all operations to READ; no Create/Update/Delete exposed. |

### 04.03 Out of Scope

- Creating or amending Purchase Orders.
- Custom fields on Purchase Orders in S/4HANA Cloud.
- Mobile / offline capability.
- Integration with any non-SAP system.
- Any ABAP development or RAP objects in S/4HANA Cloud.
- Email or notification features.
- Historical / archived POs beyond what the standard API returns.

---

## 05. INTEGRATION & API

### 05.01 S/4HANA Cloud Released API Used

| API | Entity Set | Filter Used | Release Status |
|-----|-----------|-------------|---------------|
| `API_PURCHASEORDER_PROCESS_SRV` | `A_PurchaseOrder` | `Supplier`, `PurchaseOrderStatus` | Released — SAP Business Accelerator Hub |
| `API_PURCHASEORDER_PROCESS_SRV` | `A_PurchaseOrderItem` | `PurchaseOrder` (navigation) | Released — SAP Business Accelerator Hub |

> **No custom CDS views, BAdIs, or custom fields are required.**
> The API is fully released and available on api.sap.com.

### 05.02 BTP Services Required

| Service | Purpose | Pricing Metric |
|---------|---------|---------------|
| SAP BTP Cloud Foundry Runtime | Host the CAP Node.js app | Memory / GB-hour (Discovery Center) |
| SAP BTP Destination Service | Store S/4HANA Cloud connection (no credentials in code) | Free tier / calls |
| SAP BTP Connectivity Service | Outbound HTTP to S/4HANA Cloud API | Included with CF Runtime |
| SAP Authorization and Trust Management (XSUAA) | App-level login (BTP Identity) | Included |

> Pricing reference: [SAP Discovery Center — BTP Cloud Foundry Runtime](https://discovery-center.cloud.sap/serviceCatalog/cloud-foundry-runtime)

---

## 06. UI DESIGN (WIREFRAME)

```
┌────────────────────────────────────────────────────────┐
│  Supplier Open PO Status Viewer                        │
│                                                        │
│  Supplier No: [__________]  Name contains: [________]  │
│                                              [Search]  │
│                                                        │
│  Showing 12 results  (refreshed 14:32:01)  [Refresh]  │
│                                                        │
│  PO Number  │ Supplier │ Purch Org │ Date     │ Amount │
│  ───────────┼──────────┼───────────┼──────────┼─────── │
│  4500001234 │ 100023   │ 1000      │ 2026-07-01│ 4,500  │
│  4500001235 │ 100023   │ 1000      │ 2026-07-15│ 12,200 │
│  ...                                                   │
└────────────────────────────────────────────────────────┘
```

---

## 07. ACCEPTANCE CRITERIA

| ID   | Given | When | Then |
|------|-------|------|------|
| AC-1 | A valid supplier number is entered | The user clicks Search | A table of open POs for that supplier is displayed within 3 seconds |
| AC-2 | A partial supplier name is entered (e.g. "Acme") | The user clicks Search | All open POs for suppliers whose name contains "Acme" are shown |
| AC-3 | No open POs exist for the entered supplier | The user clicks Search | An empty-state message "No open purchase orders found" is displayed |
| AC-4 | The Refresh button is clicked | Any time | The table re-queries the API and the timestamp updates |
| AC-5 | A non-existent supplier number is entered | The user clicks Search | A clear error message is shown; no stack trace is visible |
| AC-6 | The app is deployed to BTP CF | A user logs in | The BTP Identity login screen is shown before any data is visible |

---

## 08. TENANT VERIFICATION CHECKLIST

Before go-live, the following must be confirmed in the target S/4HANA Cloud tenant:

- [ ] Communication Scenario `SAP_COM_0009` (Purchase Order Integration) is available.
- [ ] A Communication Arrangement for `API_PURCHASEORDER_PROCESS_SRV` is created with a
      dedicated Communication User (technical user, no dialog logon).
- [ ] The BTP Destination pointing to the S/4HANA Cloud OData host is configured and tested.
- [ ] The API returns data when called from BTP with the communication user credentials
      (test via API test tool or Postman before pipeline deploy).

---

## 09. CONSTRAINTS & ASSUMPTIONS

- The S/4HANA Cloud tenant exposes `API_PURCHASEORDER_PROCESS_SRV` (standard; no activation needed).
- The BTP subaccount is already provisioned with CF Runtime quota (at least 256 MB).
- The delivery team has `SpaceDeveloper` role in the target BTP CF space.
- Volume: up to 500 open POs per supplier search is acceptable (no pagination UI needed for MVP).
- The app is internal-facing; no Internet-exposed endpoint is required for this phase.
