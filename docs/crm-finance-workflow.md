# CRM finance workflow: lead to reconciled cash (living spec, v0.7)

**Status:** Phases 1 to 5 built (owner approved start 2026-10-12; management sign-off and
open questions pending). Every phase ships behind its own switch in TBO Smart Settings, off by
default, so a deploy changes nothing until the owner turns it on. This file is the living
spec: every change updates it in the same pull request, and it describes what is built, not
what was first planned. §14 lists what each phase actually built, with its decisions and
deviations from the plan.

- **Owner:** Head of IT (TBO). **Approvers:** Management, Finance Manager.
- **Date:** 2026-10-12.
- **Scope:** brief deliverables 1–4 and 7: assessment, workflow, technical plan, change list,
  notification spec, dashboard spec and timeline.
- **Systems:** tboindia.tbocloud.in (Frappe v15, Frappe CRM fork `tbocloud/crm`, ERPNext,
  HRMS, `tbo_smart`). New backend code goes in `tbo_smart`. CRM screens go in the
  `tbocloud/crm` fork, kept as small extension points so upstream merges stay easy.

**How facts were checked.** Code was read at these versions: ERPNext 15.121.4, Frappe CRM
1.86.0 (fork), HRMS 15.64.2 and Frappe 15.121.1. The local `tbo.local` site turned out to
have only `frappe` and `helpdesk` installed, so **no local data about companies, currencies,
accounts or custom fields was available**. Every statement about tboindia data is marked
**[verify on site]** or **[assumption]**.

---

## 0. Owner decisions this plan builds on

| # | Decision | Effect on the plan |
| --- | --- | --- |
| D1 | ERPNext does all accounting: quotations, invoices, payments, receivables ageing, bank import and reconciliation. CRM is the working view. Build new only where ERPNext has nothing, and keep no duplicate ledgers. | There are no TBO invoice, payment or ledger tables. TBO adds three doctypes (allocation, transfer, exception) that ERPNext has no equivalent for, plus custom fields and CRM screens. |
| D2 | Team Back Office KSA invoices clients in SAR and transfers 50% to Team Back Office India. | The plan models them as two ERPNext Companies on one site: KSA in SAR, India in INR **[assumption, verify on site]**. §12 covers what happens if KSA is not on tboindia. |
| D3 | India's share is 50% of each net receipt (amount received excluding VAT/taxes). It arises per payment received, not per invoice. Transfers are tracked against the payable and differences are reported. | One **TBO Fund Allocation** per receipt (§3, §4), settled by **TBO Fund Transfer** records (§3, §5). |

---

## 1. Assessment of the existing CRM and ERPNext

### 1.1 How a CRM Deal links to ERPNext today (checked in code)

Source: `crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py` and `crm/hooks.py`
in the `tbocloud/crm` fork.

| Link | How it is made | Where |
| --- | --- | --- |
| Settings | Single **ERPNext CRM Settings**: `enabled`, `is_erpnext_in_different_site`, `erpnext_company` (**one** company, Data field), `create_customer_on_status_change` + `deal_status`, `sync_products`. | `erpnext_crm_settings.json` |
| Deal → Customer | When the deal reaches `deal_status`, `create_customer_from_deal` calls ERPNext `erpnext.crm.frappe_crm_api.create_customer`. It sets `Customer.crm_deal` and `CRM Deal.erpnext_customer` (Data). It also runs on Sales Order `before_validate` if the customer is still missing. | `create_customer_in_erpnext`, `create_customer_on_sales_order` |
| Deal → Quotation | The **Create Quotation** action (CRM Form Script "Create Quotation from CRM Deal") opens **`/app/quotation/new`** (a desk form) with `quotation_to` = Customer or CRM Deal, `party_name`, `crm_deal`, `company = erpnext_company`, contact and address. Items are prefilled from deal products by `prefill_quotation_items`. | `get_quotation_url`, `public/js/erpnext_quotation_prefill.js` |
| Quotation → Deal | Custom field `Quotation.crm_deal` (Data, read-only). The fork adds a **Quotations tab** on the Deal page (`get_deal_quotations`, gated by `can_view_quotations`) with live refresh through the `crm_quotation_update` realtime event. | `Deal.vue` tabs, `notify_deal_quotation_change` |
| Quotation → Sales Order → Sales Invoice → Payment | **No CRM link.** The deal is only reachable through `Sales Order Item.prevdoc_docname` → `Quotation.crm_deal`. Sales Invoice and Payment Entry carry no deal. | gap G1 |
| Organization → Customer | None. The customer hangs off the deal, not the CRM Organization. | `docs/tbo-smart-api-ess-crm.md` §6 |
| Products | CRM Product ↔ ERPNext Item two-way sync (`crm/integrations/erpnext/item.py`). | |
| Permissions | User Permission and DocShare are mirrored between CRM and ERPNext (`mirror_sync.py`). | |

### 1.2 Coverage by requirement

Legend: **Covered** = use as is. **Partly** = reuse plus a small TBO addition. **Gap** = TBO
builds it.

| Brief item | What already exists (doctype / report / file) | Coverage | TBO addition |
| --- | --- | --- | --- |
| 4.1 BDE ownership | `CRM Lead.lead_owner`, carried to `CRM Deal.deal_owner` (`LEAD_DEAL_FIELD_MAP`). Assignment via ToDo. Sales hierarchy (`docs/user-hierarchy.md`). | Covered | – |
| 4.1 Follow-up history, next date | CRM Task (`due_date`, `status`, `assigned_to`), FCRM Note, CRM Call Log, comments. Mobile `log_activity` / `add_follow_up` in `tbo_smart/api/mobile/crm.py`. | Covered | "Next follow-up" = earliest open task, shown in lists (Phase 1). |
| 4.1 Conversion status | `CRM Lead.converted`, `convert_to_deal`, `CRM Status Change Log`. | Covered | – |
| 4.1 Onboarding / quotation link | Quotation tab on the Deal. No onboarding record. | Partly | Post-sale stage field on the deal (§2), onboarding checklist. |
| 4.1 Conversion reports | CRM dashboard `get_funnel_conversion`, `get_deals_by_salesperson` (`crm/api/dashboard.py`); `tbo_smart` `get_sales_insights`. | Partly | BDE conversion report with entity filter (Phase 5). |
| Quotation approval | ERPNext Quotation. Discount/commission approval by designation is being built as a **separate feature**: "Quotation discount/commission approval" (`tbo_smart`, branch `feat/quotation-discount-control`, spec `docs/quotation-discount-control.md`). | Partly (separate feature) | This plan does not design approval. It shows the approval state on the Finance tab and blocks invoicing while approval is pending (§2, stage 6). |
| 4.2 Advance / partial / full payments | Payment Entry against Sales Order (advance) or Sales Invoice (partial/full); `Sales Invoice Advance`; `Payment Schedule` per payment term; `Payment Ledger Entry`. | Covered | – |
| 4.2 Outstanding per invoice, due dates | `Sales Invoice.outstanding_amount`, `Payment Schedule.outstanding/due_date`. | Covered | Shown on the Deal Finance tab. |
| 4.2 Client payment history | Customer Ledger Summary, General Ledger, Payment Ledger reports. | Covered | Deal/client timeline in CRM. |
| 4.2 Discrepancy validation | ERPNext blocks over-allocation (`validate_allocated_amount`). Nothing flags overpayment, payment to the wrong entity, or a receipt booked as a Journal Entry. | Partly | Checks raise **TBO Finance Exception** records (§3.5). |
| 4.3 Outstanding per client / invoice, ageing | **Accounts Receivable** and **Accounts Receivable Summary** (filters: company, party, `sales_person`, `range`, `based_on_payment_terms`, `in_party_currency`). | Covered | Reused in CRM through the report's own query class. |
| 4.3 Automated reminders | **Dunning** / **Dunning Type** (manual, created from an invoice by `create_dunning`). **Notification** with "Days Before / Days After" on a parent date field. **Process Statement Of Accounts** (scheduled SOA emails). | Partly | Daily job per payment-term row (Notification cannot read child-row due dates). |
| 4.3 Overdue tracking, tasks with remarks and next action, escalation | CRM Task. No escalation. | Gap | Collection tasks + escalation ladder (§6). |
| 4.3 BDE-wise and Accounts-wise collection performance | AR report has `sales_person`. There is no "accounts owner". | Partly | `tbo_accounts_owner` on the deal; performance report. |
| 4.3 Stays active until paid or exception | No concept of this. | Gap | Reminders continue until outstanding = 0 or an approved exception exists. |
| 4.4 50% allocation per receipt | None. Inter Company Journal Entry and inter-company Sales/Purchase Invoice exist for the accounting. | Gap | TBO Fund Allocation (§3.3, §4). |
| 4.4 Transfer tracking vs payable | Payment Entry (Pay / Receive), Bank Transaction. Nothing ties the KSA leg, the India leg and the allocations together. | Gap | TBO Fund Transfer (§3.4). |
| 4.5 Bank statement import | **Bank Statement Import** (CSV/XLSX/Google Sheets) → **Bank Transaction**. | Covered | – |
| 4.5 Matching | **Bank Reconciliation Tool** ranks vouchers by reference no, amount and party (`bank_reconciliation_tool.py` `get_*_matching_query`), with auto-reconcile. | Covered | Transfer legs are matched the same way. |
| 4.5 Unmatched | Bank Transaction `status` Unreconciled / `unallocated_amount`. | Covered | Ageing of unmatched lines in the queue. |
| 4.5 Duplicate detection | `validate_duplicate_references` only stops the same voucher twice **inside one** Bank Transaction. Import does not detect re-imported lines. | Gap | Dedupe key + flag (§5). |
| 4.5 Amount discrepancies, pending transfers, approval | None. | Gap | TBO Finance Exception with maker-checker workflow. |
| 4.5 Nothing reconciled until verified | ERPNext sets **Reconciled** as soon as a line is fully allocated. | Gap | Verification step and lock (§5.3). |
| Audit trail | `track_changes` = 1 on Payment Entry, Sales Invoice, Journal Entry, Bank Transaction, Dunning, CRM Deal and CRM Lead → **Version**. Workflow Action records approvals. **CRM Task has no track_changes.** | Partly | Turn on Version for CRM Task (property setter); every new doctype tracks changes. |
| Dashboard | CRM dashboard (`crm/api/dashboard.py`, layout in **CRM Dashboard**). It only resolves `crm.api.dashboard.get_<name>` and is gated by `@sales_user_only`. | Gap | Provider hook in the fork; finance cards from `tbo_smart` (§8). |
| Notifications | Frappe Notification Log → Expo mobile push (`tbo_smart/push.py`, off by default). Email. **No Microsoft Teams integration exists in `tbo_smart`.** | Partly | Email + in-app/push; Teams later (§6.4). |

### 1.3 Gaps (numbered for the change list)

| # | Gap |
| --- | --- |
| G1 | Sales Order, Sales Invoice and Payment Entry do not record the CRM Deal. |
| G2 | ERPNext CRM Settings has a single `erpnext_company`. If both KSA and India ever quote clients, the company must be chosen per deal. |
| G3 | "Create Quotation" opens a desk form (`/app/quotation/new`), which conflicts with "no desk forms for daily users". |
| G4 | No quotation approval. Covered by the separate feature "Quotation discount/commission approval" (`docs/quotation-discount-control.md`); this plan only consumes its approval state. |
| G5 | CRM has no receivables, allocation or exception screens, and Accounts roles cannot use the CRM UI: CRM admits a user only when they can access the FCRM module (`crm.api.check_app_permission`), and dashboard methods are `@sales_user_only`. |
| G6 | No reminder schedule per payment-term due date. Dunning is manual. |
| G7 | No escalation ladder. |
| G8 | No 50% allocation. |
| G9 | No transfer register linking KSA payment, India receipt, bank lines and allocations. |
| G10 | No duplicate detection on imported bank lines. |
| G11 | No "verified" state before a bank line counts as reconciled. |
| G12 | No discrepancy queue with approval. |
| G13 | No approved payment exception (waive, extend, pause, dispute). |
| G14 | Follow-up tasks have no invoice link, promise-to-pay or escalation level, and no Version history. |
| G15 | CRM's automation engine (`crm/automation/events.py`) needs Frappe v16; tboindia runs v15 **[verify on site]**, so the plan does not depend on it. |

### 1.4 Risks

| # | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R1 | KSA is not a Company on tboindia (for example, KSA books are kept in another system). | Allocation cannot fire on receipt automatically. | Verify first (Phase 0). Fallback: import KSA receipts as a periodic file into a staging list, which is slower and manual (§12). |
| R2 | KSA tax invoices must meet ZATCA e-invoicing rules. ERPNext 15 ships no Saudi regional module (`erpnext/regional` has none). | KSA invoices from ERPNext may not be compliant. | Verify which ZATCA app, if any, is installed on site. Finance confirms the e-invoicing phase TBO KSA is in. |
| R3 | Tax treatment of the India share: KSA withholding tax on payments to a non-resident related party, Indian GST on export of services, transfer pricing. | Wrong postings, short transfers. | Finance and a tax advisor choose the accounting option in §4.6 before Phase 3. WHT shortfalls are modelled as a known difference type. |
| R4 | Old invoices and payments have no deal link. | History is incomplete in CRM. | One-off backfill through quotation → sales order chains; the rest are listed for Accounts to link by hand. Allocation starts from an agreed `allocation_start_date`. |
| R5 | Exchange-rate source and timing disputes (SAR→INR). | Disagreement over transfer differences. | Use the rate actually achieved on the India receipt. ERPNext books FX gain/loss (§4.4). |
| R6 | Upstream Frappe CRM changes collide with fork edits. | Merge pain. | Fork edits are limited to extension points (tab slot, routes, dashboard provider hook, app-permission hook); all logic lives in `tbo_smart`. |
| R7 | Sales users see amounts they should not (allocation, India share). | Confidentiality. | Allocation and transfer data is visible to Accounts, Finance and Management roles only (§9). |
| R8 | Another agent is re-theming the CRM frontend now. | Conflicts in `frontend/`. | Phase 1 UI starts after the theme PR merges, and uses its tokens. |

---

## 2. Workflow

The CRM Deal carries a **post-sale stage** (`tbo_stage`, §3.1) next to CRM's own pipeline
status. The stage is **computed** from the linked ERPNext documents, so nobody types it.
Version (track_changes on CRM Deal) keeps its history. Only "Onboarding complete" and
"Closed" are set by a person, and both are logged.

| # | Stage | Status values | System record | Owner role | Carried forward automatically | Exit criteria |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead | CRM Lead Status: the 13 pipeline statuses, New Lead to Closed – Won / Closed – Lost (`lead-pipeline.md` §2) | CRM Lead | Sales Team (captures), BDE | Source, contact, organization, products (web form, mobile `create_lead`, import) | Owner assigned |
| 2 | BDE assignment | `lead_owner` set | CRM Lead + ToDo | BDM / Head – Sales & Marketing | Owner gets share + assignment (`share_with_agent`, `assign_agent`) | `lead_owner` not empty |
| 3 | Follow-up | Lead status; open CRM Task due date = next follow-up | CRM Task, FCRM Note, CRM Call Log | BDE | Notes, tasks and calls stay on the lead | Qualified, or Lost with reason |
| 4 | Conversion | `converted = 1`; deal created automatically at Closed – Won while the lead pipeline is on (`lead-pipeline.md` §6) | CRM Deal (`lead` link) | BDE | `convert_to_deal` copies owner, contact, organization and products | Deal exists |
| 5 | Onboarding | `tbo_stage = Onboarding` | CRM Deal + onboarding checklist (CRM Task template) | BDE + KSA Accounts | Deal → ERPNext Customer (`create_customer_from_deal`) with contacts and address | Customer exists with VAT/CR no. and billing address; checklist done; "Onboarding complete" ticked |
| 6 | Quotation approval | Quotation → adjustment → role check → approval (if needed) → final quotation, run by **Quotation discount/commission approval (separate feature, docs/quotation-discount-control.md)**; client acceptance = Sales Order | Quotation (`crm_deal`) + that feature's adjustment and approval records, Sales Order | BDE drafts and adjusts within their limit; BDM, Head – Sales & Marketing, Director and CEO approve above it (limits per designation live in that feature's settings) | Items from deal products (`prefill_quotation_items`); customer, company and contact from the deal | Final quotation submitted with **no approval pending**, and Sales Order submitted (client accepted). **A quotation whose approval is pending cannot become a Sales Order or Sales Invoice** (`validate` guard, §3.1). |
| 7 | Invoice | SI status (Unpaid, Partly Paid, Overdue, Paid) | Sales Invoice (`crm_deal`, payment schedule) | KSA Accounts | SO → SI mapper (items, taxes, payment terms, project); `crm_deal` set by hook | Invoice submitted |
| 8 | Advance collection | SO `advance_paid`; PE against SO | Payment Entry (Receive) | KSA Accounts; BDE chases | `get_payment_entry` from SO/SI; `crm_deal` from references | Advance term received, or exception |
| 9 | Receivables follow-up | `tbo_stage = Collecting`; overdue terms; escalation level | Payment Schedule, CRM Task (collection), Dunning | Accounts owner; BDE escalated | Reminder job reads due terms (§6) | Outstanding = 0, or approved exception |
| 10 | Balance collection | SI Paid | Payment Entry | KSA Accounts | SO advances pulled into SI (`Sales Invoice Advance`) | All invoices of the deal paid |
| 11 | KSA–India allocation | Allocation: Open / Partly Settled / Settled / Reversed | TBO Fund Allocation (one per receipt) | System; KSA Accounts reviews | Created on PE submit (§4) | Every receipt has an allocation |
| 12 | Transfer tracking | Transfer: Draft / Sent / Received / Matched / Difference / Closed | TBO Fund Transfer + KSA PE (Pay) + India PE (Receive) | KSA Accounts sends; India Accounts confirms | Open allocations suggested FIFO | Allocations fully settled |
| 13 | Bank reconciliation | Bank Transaction: Unreconciled / Reconciled + `tbo_verification` Unverified / Verified | Bank Transaction, Bank Reconciliation Tool | KSA / India Accounts | Imported lines matched by ref no, amount and party | Each receipt and transfer leg matched and verified |
| 14 | Financial approval | Exceptions: Open / Pending Approval / Approved / Rejected | TBO Finance Exception | Finance Manager | Auto-raised by checks (§5.4) | No open exceptions on the deal |
| 15 | Closure | `tbo_stage = Closed` | CRM Deal | Finance Manager | – | All of: outstanding 0 or approved exception; allocations settled; transfers matched and verified; no open exceptions |

`tbo_stage` values: Onboarding → Quotation → Invoiced → Collecting → Collected → Settling →
Reconciled → Closed, plus On Exception. The stage is recomputed on submit or cancel of
Quotation, SO, SI, PE, allocation, transfer and exception, and nightly as a safety net.

---

## 3. Data model

All new code lives in `tbo_smart` (module "Tbo Smart"). Custom fields are created in code
(`create_custom_fields` in an `after_migrate` hook), not as hand-made Custom Field records, so
every site gets the same fields. Every new doctype has `track_changes = 1`.

### 3.1 Custom fields

| Doctype | Field | Type | Purpose / rule |
| --- | --- | --- | --- |
| CRM Deal | `tbo_company` | Link Company | Invoicing entity. Defaults to `ERPNext CRM Settings.erpnext_company`. Used for quotations (G2). |
| CRM Deal | `tbo_stage` | Select (§2) | Post-sale stage, read-only, computed. |
| CRM Deal | `tbo_accounts_owner` | Link User | Accounts person who owns collection; used for Accounts-wise performance. |
| CRM Deal | `tbo_onboarding_complete` | Check, read-only | Set by BDE/Accounts through the Finance tab (API); logged. |
| CRM Deal | `tbo_closed` | Check, read-only | **Built in Phase 1 (not in the v0.1 plan).** "Closed" is a person's decision, so it needs its own flag; the stage is Closed while it is set. |
| CRM Deal | `tbo_project` | Link Project | Optional; filled from SO `project` when one exists. **Not built yet** (V13 unverified). |
| Sales Order | `crm_deal` | Link CRM Deal, read-only | Set in `validate` from the item's quotation (G1). |
| Sales Invoice | `crm_deal` | Link CRM Deal | Set in `validate` from SO or quotation. Required for `tbo_company` invoices from `allocation_start_date`, unless `tbo_no_deal_reason` is filled (**Phase 3**; Phase 1 only fills it). |
| Sales Invoice | `tbo_no_deal_reason` | Small Text | Why an invoice has no deal (for example, a legacy contract). |
| Sales Order, Sales Invoice | `validate` guard (no field) | – | Refuses to save when a source Quotation still has a pending discount/commission approval, read from the separate feature's state. If that feature already enforces this, the plan reuses its check instead of adding a second one. |
| Payment Entry | `crm_deal` | Link CRM Deal, read-only | Set from references. If the references belong to different deals it is left blank and the allocation lines carry the deal per row. |
| Payment Schedule (child) | `tbo_last_reminder` | Select (none, D-3, D0, D+7, D+15, D+30, Weekly) | Makes the reminder job idempotent. |
| Payment Schedule (child) | `tbo_last_reminder_on` | Date | Same. |
| CRM Task | `tbo_task_type` | Select (Sales follow-up, Collection, Onboarding) | Splits the collections list from sales tasks. Smart leads (`docs/smart-leads.md`) create their lead follow-ups as "Sales follow-up". |
| CRM Task | `tbo_sales_invoice` | Link Sales Invoice | The invoice being chased. |
| CRM Task | `tbo_promised_date`, `tbo_promised_amount` | Date, Currency | Promise to pay, from the follow-up remark. |
| CRM Task | `tbo_escalation_level` | Int (0–4) | Set by the reminder job. |
| Bank Transaction | `tbo_dedupe_key` | Data, indexed, read-only | Hash of bank account, date, amount, direction and normalised reference/description (§5.2). |
| Bank Transaction | `tbo_possible_duplicate_of` | Link Bank Transaction | Set when the key already exists. |
| Bank Transaction | `tbo_verification` | Select (Unverified, Verified) | §5.3. |
| Bank Transaction | `tbo_verified_by`, `tbo_verified_on` | Link User, Datetime | Maker-checker. |

`Quotation.crm_deal` and `Customer.crm_deal` already exist (Data, created by CRM) and are
reused as they are.

**Phase 1 created** the CRM Deal fields `tbo_company`, `tbo_stage`, `tbo_onboarding_complete`
and `tbo_closed`, and `crm_deal` on Sales Order, Sales Invoice and Payment Entry
(`tbo_smart/finance/setup.py`, run on install and by `after_migrate`). The other rows arrive
with the phase that uses them.

Property setter: **CRM Task → Track Changes = 1**, so follow-up edits get Version history (G14).

### 3.2 Settings: extend `TBO Smart Settings` (no new settings doctype)

New "Finance workflow" section. **Built in Phase 1:**

| Field | Type | Default |
| --- | --- | --- |
| `enable_finance_workflow` ("Finance workflow (Phase 1)") | Check | 0. Switches on the deal-link hooks, the stage, the pending-approval guard, the Finance tab, the quotation dialog, the BDE report and hides the desk "Create Quotation" action. |
| `finance_bde_can_see_amounts` ("BDE can see invoice/payment amounts") | Check | 1 (Q17). |
| `finance_crm_access` ("Let finance roles open the CRM") | Check | 0 (G5). |
| `finance_crm_roles` ("Finance roles in the CRM") | Small Text, one role per line | `Accounts User`, `Accounts Manager`. Validated: every line must be a Role. |

**Built in Phase 2** (section "Receivables and reminders (Phase 2)"):

| Field | Type | Default |
| --- | --- | --- |
| `enable_finance_receivables` ("Receivables and reminders (Phase 2)") | Check | 0. Needs Phase 1 on too. Switches on the reminder job, the digest, the Receivables and Finance exceptions pages and the receivable actions on the Finance tab. |
| `send_client_reminders` ("Send reminder emails to clients") | Check | **0** (Q14: wording, language and mailbox not agreed). Internal tasks, notifications and draft dunnings run either way. |
| `weekly_client_reminders` ("Weekly client reminder after D+30") | Check | 0 |
| `reminder_sender` | Link Email Account | Blank: the site's default outgoing account. |
| `invoicing_company` | Link Company | Blank: reminders for every company. Set it to TBO KSA (Q2; Phase 3 also reads it). |
| `accounts_lead` ("KSA Accounts lead") | Link User | Owns collection on deals with no accounts owner (Q18). |
| `escalation_sales_head`, `escalation_finance_manager` | Link User | Notified at D+15 and D+30; the Finance Manager also gets the digest in the app. |
| `management_digest_recipients` | Small Text (emails) | Blank: no digest email. |

**Built in Phase 3** (section "Allocation and transfers (Phase 3)"):

| Field | Type | Default |
| --- | --- | --- |
| `enable_fund_allocation` ("Allocation and transfers (Phase 3)") | Check | 0. Needs Phase 1 on, and refuses to switch on without both companies. |
| `invoicing_company` | Link Company | From Phase 2: TBO KSA (Q2). |
| `partner_company` | Link Company | TBO India. Must differ from the invoicing company. |
| `allocation_percent` | Percent | 50 |
| `allocation_start_date` | Date | Set to the date of the first migrate with Phase 3 (the deploy date, Q16) when blank. |
| `allocation_accounting_option` | Select: Memo only, Monthly inter-company invoice, Journal Entry per receipt | Memo only (Q1). Only Memo only is built; choosing another shows a warning and still posts nothing. |
| `allocation_base_after_bank_charges` | Check | 0 (Q6: base = amount credited to the client). |
| `default_vat_rate` | Percent | 15. Used only when money has no document to read a VAT ratio from. |
| `amount_tolerance` | Currency | 1.00. Transfer differences at or below this are not exceptions. |
| `transfer_pending_days` | Int | 5. A transfer not confirmed by India after this many days raises an exception. |

**Built in Phase 4** (section "Reconciliation and approvals (Phase 4)"):

| Field | Type | Default |
| --- | --- | --- |
| `enable_reconciliation` ("Reconciliation and approvals (Phase 4)") | Check | 0. Needs Phase 1 on. |
| `unmatched_alert_days` | Int | 3 |
| `second_approval_amount` ("Second approval above") | Currency | Blank: one approver is enough (Q5). |

### 3.3 New doctype: TBO Fund Allocation (submittable)

**Why new:** ERPNext has no record of a revenue-share obligation that arises per receipt and is
settled by later transfers. A GL account alone cannot show which receipt, deal and invoice
each SAR came from, or how much of it is settled. The allocation holds no money of its own: its
amounts are derived from the Payment Entry and are never edited by hand.

| Field | Type | Rule |
| --- | --- | --- |
| `naming` | `TBO-ALC-.YYYY.-.#####` | |
| `allocation_type` | Select: Receipt, Reversal | |
| `payment_entry` | Link Payment Entry | Required. **Unique** among submitted Receipt allocations: one receipt, one allocation. |
| `reversal_of` | Link TBO Fund Allocation | Required for Reversal. |
| `posting_date` | Date | = PE posting date |
| `company` | Link Company | = `invoicing_company` |
| `customer`, `crm_deal`, `project` | Link | From the PE; `crm_deal` blank if the lines differ. |
| `currency` | Link Currency | Company currency (SAR) |
| `gross_amount` | Currency | PE base allocated + unallocated amount (SAR) |
| `tax_amount` | Currency | Σ line tax |
| `net_amount` | Currency | Σ line net |
| `allocation_percent` | Percent | Copied from settings when created (snapshot) |
| `share_amount` | Currency | Payable to India (negative for Reversal) |
| `settled_amount` | Currency | Σ submitted transfer lines, read-only |
| `outstanding_amount` | Currency | share − settled |
| `status` | Select: Open, Partly Settled, Settled, Reversed, On Exception | Computed |
| `lines` | Table **TBO Fund Allocation Line** | One per PE reference plus one for any unallocated amount |

**TBO Fund Allocation Line:** `reference_doctype` (Sales Invoice / Sales Order / Unallocated),
`reference_name`, `crm_deal`, `gross_amount`, `net_ratio` (document net ÷ grand total, 9 dp),
`tax_amount`, `net_amount`, `share_amount`, `is_closing_receipt` (Check).

**Lifecycle (hooks on Payment Entry):**

| PE event | Allocation action |
| --- | --- |
| `on_submit`, type Receive, party Customer, company = invoicing company, posting date ≥ start date | Create and submit an allocation (system user, so Accounts cannot edit it). |
| `on_cancel` and allocation unsettled | Cancel the allocation. |
| `on_cancel` and allocation partly/fully settled | Submit a **Reversal** allocation (negative) and raise an exception "Receipt cancelled after transfer". |
| `on_submit`, type Pay to Customer (refund) | Submit a negative **Reversal**-type allocation for the refund's net (§4.3). |
| Receipt booked as a Journal Entry against a customer | No allocation; raise exception "Receipt via Journal Entry: book it as a Payment Entry". |

**Validations:** the lines total the PE; the share follows the formula in §4; no manual edits
after submit (only a reversal or an approved exception can change the payable).

### 3.4 New doctype: TBO Fund Transfer (submittable)

**Why new:** ERPNext records each leg of the transfer as its own Payment Entry in its own
company, plus a Bank Transaction on each side. Nothing ties the two legs together or says
which allocations a transfer settles. The transfer record stores only links and which
allocations it settles; its amounts are fetched read-only from the two Payment Entries.

| Field | Type | Rule |
| --- | --- | --- |
| naming | `TBO-TRF-.YYYY.-.#####` | |
| `from_company`, `to_company` | Link Company | Settings defaults |
| `transfer_date` | Date | |
| `reference_no` | Data | SWIFT / UTR; used for bank matching |
| `ksa_payment_entry` | Link Payment Entry (Pay, from_company) | Required to submit |
| `amount_sent` | Currency (SAR), fetched | = KSA PE paid amount |
| `sender_charges` | Currency (SAR), fetched | KSA PE deductions |
| `withholding_tax` | Currency (SAR) | If KSA withholds (Q8) |
| `india_payment_entry` | Link Payment Entry (Receive, to_company) | Set when India confirms |
| `amount_received_inr` | Currency (INR), fetched | |
| `receiver_charges_inr` | Currency (INR), fetched | |
| `effective_rate` | Float, computed | INR received ÷ SAR sent |
| `ksa_bank_transaction`, `india_bank_transaction` | Link Bank Transaction | Filled by reconciliation (§5) |
| `allocations` | Table **TBO Fund Transfer Allocation** (`fund_allocation`, `allocated_amount`) | Open allocations, oldest first by default; editable before submit |
| `allocated_total` | Currency (SAR) | Σ rows |
| `difference` | Currency (SAR) | (amount_sent + withholding_tax) − allocated_total |
| `status` | Select: Draft, Sent, Received, Matched, Difference, Closed | Computed |

**Validations:** a row cannot exceed its allocation's outstanding amount; Σ rows ≤ amount sent
+ withholding; the same allocation cannot be over-settled across transfers (row-locked
check); `|difference| > amount_tolerance` raises an exception; both PEs must be submitted
and belong to the right companies.

**Status:** Sent (KSA PE submitted) → Received (India PE linked) → Matched (both bank lines
reconciled and verified) → Closed (no open exceptions). Difference is set when an exception is
open.

### 3.5 New doctype: TBO Finance Exception (one doctype for discrepancies and payment exceptions)

**Why new and why one:** ERPNext has no queue of "things that need a decision", and no
maker-checker approval for writing off, accepting a difference or pausing collection. Bank
discrepancies and client payment exceptions have the same shape (a reference, expected vs
actual, a proposed resolution and an approval), so one doctype with a `category` serves both.
That avoids two near-duplicate doctypes.

| Field | Type | Rule |
| --- | --- | --- |
| naming | `TBO-EXC-.YYYY.-.#####` | |
| `category` | Select: Receivable, Allocation, Transfer, Bank Reconciliation | |
| `exception_type` | Select (table below) | |
| `source` | Select: System, User | |
| `reference_doctype`, `reference_name` | Dynamic Link | The document at issue |
| `company`, `customer`, `crm_deal` | Link | For filters and permissions |
| `expected_amount`, `actual_amount`, `difference`, `currency` | Currency / Link | |
| `requested_action` | Select: Extend due date, Pause reminders, Waive balance, Accept difference, Correct entry, Mark duplicate, Not an issue | |
| `new_due_date`, `pause_until` | Date | For extend or pause |
| `reason` | Text | Required |
| `resolution_reference_doctype`, `resolution_reference_name` | Dynamic Link | The correcting document (credit note, JE, cancelled duplicate, …) |
| `workflow_state` | Workflow: Open → Pending Approval → Approved / Rejected (→ Open) | Fixture Workflow; **`allow_self_approval = 0`** on the approve transition |
| `approved_by`, `approved_on` | Link User, Datetime | Set by the workflow transition |

Exception types:

| Category | Type | Raised by |
| --- | --- | --- |
| Receivable | Overpayment / payment exceeds open invoices | PE submit check |
| Receivable | Payment received by the wrong entity (India) | PE submit check: customer of an invoicing-company deal paid to the partner company |
| Receivable | Client dispute; extend; pause; waive (write-off) | User |
| Allocation | Receipt via Journal Entry | JE submit check |
| Allocation | Receipt cancelled after transfer | PE cancel |
| Allocation | VAT ratio changed after reconciliation | Nightly integrity check |
| Transfer | Short / excess transfer beyond tolerance | Transfer submit |
| Transfer | Transfer pending more than N days | Nightly |
| Bank Reconciliation | Possible duplicate bank line | Bank Transaction insert |
| Bank Reconciliation | Unmatched more than `unmatched_alert_days` | Nightly |
| Bank Reconciliation | Amount mismatch between bank line and voucher | Reconciliation hook |

**Effects of approval:** "Extend due date" updates the Payment Schedule row's due date through
ERPNext's own path; "Pause reminders" stops the job until `pause_until`; "Waive balance"
requires a linked credit note or write-off JE before approval. An approved exception counts as
"authorised exception" for closure (§2, stage 15).

### 3.6 Reused, not rebuilt

| Need | Reused |
| --- | --- |
| Follow-up with remarks and next action | CRM Task (+ fields in §3.1) and FCRM Note |
| Formal reminder letter | ERPNext Dunning + Dunning Type (Reminder 1, Reminder 2, Final Notice) |
| Statement of account | Process Statement Of Accounts |
| Outstanding, ageing | Payment Ledger Entry through the Accounts Receivable report class |
| FX gain/loss | ERPNext exchange gain/loss on Payment Entry |
| Approval history | Workflow Action, Version, Comment |
| Bank import and matching | Bank Statement Import, Bank Reconciliation Tool |

---

## 4. 50% allocation rules

### 4.1 Formula

For each submitted receipt `PE` (company = KSA, in SAR):

```
for each reference row r (Sales Invoice or Sales Order) and the unallocated remainder:
    gross_r   = r.allocated_amount converted to company currency (SAR)
    net_ratio = doc.base_net_total / doc.base_grand_total   (r's invoice or order)
              = deal's latest submitted SO/Quotation ratio  (unallocated, deal known)
              = 1 / (1 + default_vat_rate)                  (unallocated, no document)
    net_r     = round(gross_r × net_ratio, 2)
    share_r   = round(net_r × allocation_percent / 100, 2)
share = Σ share_r
```

- **VAT exclusion:** the ratio removes VAT in proportion. An invoice with no tax (exempt or
  zero-rated) has ratio 1.
- **Closing receipt, no rounding drift:** when a receipt brings an invoice's outstanding to 0,
  its line takes the remainder:
  `net_r = invoice.base_net_total − Σ earlier net on that invoice` and
  `share_r = round(invoice.base_net_total × pct, 2) − Σ earlier share on that invoice`.
  The shares on an invoice therefore always add up to exactly 50% of its net total.
- **Rounding:** 2 decimals, ROUND_HALF_UP, in SAR.
- **Advance re-linked later:** when an advance against an SO (or an unallocated receipt) is
  later reconciled to the invoice, no second allocation is made. If the invoice's ratio
  differs from the one used, the nightly check raises "VAT ratio changed" for Accounts to
  approve an adjustment (a Reversal plus a new Receipt allocation).
- **Bank charges deducted by the client's bank:** by default the base is the amount credited
  to the client (allocated amount). If `allocation_base_after_bank_charges` = 1, PE deductions
  reduce the base pro rata (Q6).
- **Write-offs and discounts:** no money moves, so no allocation.

### 4.2 Currency

| Item | Rule |
| --- | --- |
| Allocation currency | KSA company currency (SAR). A receipt in another currency uses the PE's `base_received_amount` (SAR at the PE's rate). |
| Payable | In SAR, until settled. |
| Transfer | KSA pays SAR (amount_sent). India receives INR. |
| FX | India's INR receipt against a SAR-denominated receivable gives an exchange gain or loss, which ERPNext books on the India Payment Entry (`exchange_gain_loss`). The allocation is settled in SAR, so FX never changes what India is owed. |
| Rate source | The bank's achieved rate (INR received ÷ SAR sent) **[assumption, Q3]**. The Currency Exchange rate is used only for provisional INR figures on the dashboard. |

### 4.3 Credit notes, refunds and reversals

| Event | Effect |
| --- | --- |
| Credit note (SI `is_return`) with no refund | None. No money moved. The credit reduces outstanding. |
| Refund paid to the client (PE Pay to Customer against a credit note) | Reversal allocation = −50% × net of the refund, using the credit note's ratio. The payable falls. |
| Payable becomes negative (refund after India was paid) | India owes KSA. It is netted against the next transfer and shown as "Due from India" **[Q4]**. |
| Receipt PE cancelled before any transfer | The allocation is cancelled. |
| Receipt PE cancelled after a transfer | Reversal allocation plus an exception for the Finance Manager. |
| Bounced cheque | Treated as a cancelled receipt. |

### 4.4 Worked example A: advance, partial and balance (15% VAT)

Sales Order and Sales Invoice: net SAR 100,000.00, VAT SAR 15,000.00, grand total
SAR 115,000.00, ratio 0.869565217.

| Receipt | Against | Gross (SAR) | Net (SAR) | India share 50% (SAR) | Running payable |
| --- | --- | --- | --- | --- | --- |
| PE-1, 05 Nov, advance 30% | Sales Order | 34,500.00 | 30,000.00 | 15,000.00 | 15,000.00 |
| PE-2, 20 Dec, partial | Sales Invoice | 40,250.00 | 35,000.00 | 17,500.00 | 32,500.00 |
| PE-3, 25 Jan, balance (closing) | Sales Invoice | 40,250.00 | 35,000.00 (remainder) | 17,500.00 (remainder) | 50,000.00 |
| **Total** | | **115,000.00** | **100,000.00** | **50,000.00** | |

### 4.5 Worked example B: rounding

Invoice: net 33,333.33, VAT 5,000.00, grand 38,333.33.

| Receipt | Gross | Net | Share |
| --- | --- | --- | --- |
| R1 | 10,000.00 | 10,000 × 33,333.33 ÷ 38,333.33 = 8,695.65 | 4,347.83 (4,347.825 rounded half up) |
| R2 (closing) | 28,333.33 | 33,333.33 − 8,695.65 = 24,637.68 | round(16,666.665) − 4,347.83 = 16,666.67 − 4,347.83 = **12,318.84** |
| Total | 38,333.33 | 33,333.33 | 16,666.67 |

### 4.6 Accounting entries: options for Finance to choose (Q1)

The allocation itself posts **nothing** to the GL. It is the sub-ledger that proves the
payable. One of these options turns it into books:

| Option | KSA books | India books | Pros | Cons |
| --- | --- | --- | --- | --- |
| **A. Monthly inter-company invoice (recommended)** | Purchase Invoice from "TBO India" (internal supplier), auto-made by ERPNext `make_inter_company_purchase_invoice`: Dr Cost of services – India / Cr Creditors – TBO India (SAR) | Sales Invoice to "TBO KSA" (internal customer, SAR): Dr Debtors – TBO KSA / Cr Service revenue (export) | Native ERPNext, one GL entry per month, tax and transfer-pricing friendly; FX gain/loss automatic on the India receipt. Transfers settle this invoice through ordinary Payment Entries. | The GL shows the payable at month end; between receipts and month end it lives only in the allocation sub-ledger. |
| B. Journal Entry per receipt | Inter Company Journal Entry per allocation: Dr Cost of services – India / Cr Payable – TBO India | Paired JE via `make_inter_company_journal_entry` | GL is always current. | Many GL lines; India books revenue per receipt; harder for tax documents. |
| C. Memo only | – | – | Simplest. | Not acceptable for audited accounts. Not recommended. |

With option A, the month's invoice amount = Σ allocation `share_amount` for receipts posted
in the month (Receipt − Reversal). An allocations-to-invoice statement is attached to it.

### 4.7 Worked example C: transfer and FX (option A, illustrative rates)

- November payable: 15,000.00 (PE-1). December payable: 17,500.00 (PE-2).
- Inter-company invoices: Nov SAR 15,000 at 23.30 = INR 349,500; Dec SAR 17,500 at 23.35 =
  INR 408,625.
- Transfer TRF-1 on 05 Jan: KSA sends SAR 32,500.00 with a KSA bank fee of SAR 35 (booked as
  a KSA expense, not deducted from India's share **[Q7]**).
- India receives INR 760,000 (bank conversion at 23.3846) after a receiving fee of INR 500.
  INR 760,500 is credited against the receivables.
- India PE: receivables cleared INR 758,125. Exchange gain INR 2,375 is booked by ERPNext.
  INR 500 is booked as bank charges.
- TRF-1 allocations: ALC(PE-1) 15,000 + ALC(PE-2) 17,500 = 32,500. Difference = 0 → status
  Received, then Matched once both bank lines are reconciled and verified.
- Pending transfer after TRF-1 = 50,000 − 32,500 = **17,500.00** (PE-3 allocation).

---

## 5. Reconciliation design

### 5.1 What is reused

| ERPNext feature | Use |
| --- | --- |
| Bank Account (company accounts) | KSA SAR account(s), India INR account(s) **[verify on site]** |
| Bank Statement Import | Accounts upload the bank statement (CSV/XLSX) per account; lines become Bank Transactions (submitted) |
| Bank Reconciliation Tool | Matching: it ranks Payment Entries and Journal Entries by reference no, amount and party, and auto-reconciles exact matches |
| Bank Transaction `payment_entries`, `status`, `unallocated_amount` | Matched / unmatched state |
| Payment Entry `clearance_date` | Set by ERPNext on reconcile |
| Unreconcile Payment | Undo a wrong match (gated, §5.3) |

Bank Statement Import and the Bank Reconciliation Tool are **ERPNext accounting screens used by
Accounts staff**. Rebuilding them in CRM would duplicate ERPNext (D1). CRM shows the outcome and
the queue (§7). See Q10.

### 5.2 What TBO adds

| Addition | How |
| --- | --- |
| Duplicate detection | `Bank Transaction.before_insert` computes `tbo_dedupe_key` = sha1(bank_account, date, deposit/withdrawal, amount, normalised reference_number or description). If a non-cancelled line has the same key, it sets `tbo_possible_duplicate_of` and raises a "Possible duplicate" exception. The line is **not blocked**, because two genuine same-day, same-amount payments happen. Accounts mark it a duplicate (cancel the line) or "Not an issue". |
| Transfer matching | The TBO Fund Transfer `reference_no` is copied to both legs' Payment Entry `reference_no`, so the tool's reference rank finds them. On reconcile, a hook fills `ksa_bank_transaction` / `india_bank_transaction` on the transfer. |
| Amount discrepancy | A hook on Bank Transaction `on_update_after_submit` compares the bank amount with the matched vouchers. A difference above tolerance raises an exception. |
| Unmatched ageing | Nightly: unreconciled lines older than `unmatched_alert_days` raise an exception, at most one per line. |
| Pending transfers | Allocations with `outstanding_amount > 0`, and transfers in Sent but not Received after N days. |

### 5.3 Verification ("nothing reconciled until verified")

| Step | Who | Rule |
| --- | --- | --- |
| Match | Accounts (KSA or India) in the Bank Reconciliation Tool | ERPNext sets status Reconciled. TBO keeps `tbo_verification = Unverified`. |
| Verify | A **different** user with Accounts Manager or TBO Finance Approver | From the CRM reconciliation queue: Verify sets Verified, `tbo_verified_by` and `tbo_verified_on`. The verifier cannot be the user who matched it (taken from Version). |
| Lock | System | After Verified, `before_update_after_submit` on Bank Transaction refuses changes to `payment_entries` unless an **approved** "Correct entry" exception references the line. Unreconcile Payment is refused on the same terms. |
| Count | Dashboard | "Reconciled" = Reconciled **and** Verified. "Matched, awaiting verification" is shown separately. |

### 5.4 Discrepancy queue and approval

The queue is the TBO Finance Exception list filtered to the Transfer and Bank Reconciliation
categories (and Receivable/Allocation for the Finance Manager).

- **Workflow:** Open → (Accounts propose a resolution, link the correcting document) Pending
  Approval → Approved / Rejected (back to Open). Self-approval is disabled.
- **Audit trail:**
  - Version on the exception, Bank Transaction, Payment Entry, allocation and transfer;
  - Workflow Action (who approved, when);
  - comments for the discussion.
  - Corrections are new documents (a cancel plus an amendment, a Journal Entry or a reversal allocation), never edits to submitted records.

---

## 6. Automation and notifications

### 6.1 Reminder schedule (daily job, `tbo_smart`, 09:00 Asia/Riyadh)

Runs when `enable_receivable_reminders` = 1. It reads **each Payment Schedule row** of
submitted invoices of the invoicing company with outstanding > 0. It skips a row when an
approved Pause/Extend exception covers it, or when the row's `tbo_last_reminder` already
records this step. Email templates are **Email Template** records, so Finance can edit the
wording without code.

| Step | When | Client email (To: invoice contact; CC: deal owner) | Internal | Escalation level |
| --- | --- | --- | --- | --- |
| D−3 | 3 days before due | "Payment due on {date}" + invoice PDF | – | 0 |
| D0 | On the due date | "Payment due today" | CRM Task "Collect {invoice}" for the accounts owner, due today | 1 |
| D+7 | 7 days overdue | Reminder 1 | Task reassigned with the BDE added; in-app notification and push to both | 2 |
| D+15 | 15 days overdue | Formal letter: **Dunning** (type Reminder 2) created as Draft for KSA Accounts to submit and send | Notify the BDM, the Head – Sales & Marketing and the KSA Accounts lead; the deal appears in "At risk" | 3 |
| D+30 | 30 days overdue | Final notice (Dunning type Final Notice) | Notify the Finance Manager; included in the Management daily digest | 4 |
| Weekly after D+30 | Every 7 days | Optional client reminder (setting) | Task re-dated; stays in the digest | 4 |

The reminders stop when the row's outstanding is 0 or an approved exception exists (brief 4.3:
"active until obligation met or authorised exception").

### 6.2 Other automatic events

| Trigger | Recipient | Channel |
| --- | --- | --- |
| Quotation discount/commission approval requested, approved, escalated or overridden | Defined by the separate feature (`docs/quotation-discount-control.md`) | As that feature defines |
| Receipt submitted (allocation created) | Deal owner, accounts owner | In-app ("{client} paid SAR x") |
| Payable to India over the threshold, or monthly on day 1 | KSA Accounts, India Accounts | Email: allocation statement |
| Transfer sent | India Accounts | In-app + email |
| Transfer not received after 5 days | KSA and India Accounts, Finance Manager | Email |
| New exception | Accounts of the company | In-app |
| Exception pending approval | Finance Manager (TBO Finance Approver) | In-app + push + email |
| Exception approved or rejected | Requester | In-app |
| Daily digest (08:00) | Management, Finance Manager | Email: new overdue, D+30 list, payable, pending transfers, open exceptions |

### 6.3 Mechanism

- In-app notifications are Frappe **Notification Log** records. `tbo_smart.push` already turns
  them into mobile push when **TBO Smart Settings → Mobile push** is on, so no new push code
  is needed.
- Emails use `frappe.sendmail` with Email Templates, sent from `reminder_sender`. Each one is
  logged as a Communication on the Sales Invoice and the CRM Deal timeline.
- Jobs go in `tbo_smart/hooks.py` `scheduler_events` (daily and cron). They are idempotent
  (§3.1 fields) and limited to the invoicing company.

### 6.4 Microsoft Teams

`tbo_smart` has no Teams integration today. The digest is designed as one function returning
structured data, so a Teams channel post can be added later, for example through the TBO
Copilot Teams bot (R&D PROJ-0006) or an incoming webhook, without changing the rules. Not in
the current estimate (Q11).

---

## 7. CRM UI changes

All of these are in the CRM frontend (`tbocloud/crm` fork, Vue 3 + frappe-ui) and use the TBO
theme being applied by the theme work: one brand accent, status colours only for meaning, and
Geist Mono with tabular numbers for amounts and IDs. Data comes from whitelisted methods in
`tbo_smart/api/finance/*.py`, which check permissions server side. **Daily users (BDE, Sales,
Management) never open `/app`.** Accounts staff keep the ERPNext screens for posting and bank
work (§5.1, Q10).

| Screen | Route | Users | Content | Actions |
| --- | --- | --- | --- | --- |
| Deal → **Finance** tab (replaces the fork's Quotations tab, which becomes its first section) | `/crm/deals/:id` | BDE (own deals), BDM (team), Head – Sales & Marketing, Accounts, Finance, Director / CEO | Stage strip (§2) · Quotations (status, approval state, total) · Sales Orders · Invoices (number, date, due, grand, outstanding, status) with payment-term rows · Payments received (date, mode, ref, amount) · Outstanding and overdue totals · Allocation (Accounts/Finance only): India share, settled, pending · Follow-ups (collection tasks, promise to pay) · Open exceptions | New quotation (dialog; replaces the desk link, G3) · Discount/commission adjustment and approval actions **embedded from the separate feature** (not rebuilt here) · Add follow-up · Request exception · Open PDF (print view) |
| **Receivables** | `/crm/receivables` | Accounts, Finance, Management; BDE sees own deals | Outstanding per client and per invoice, ageing buckets (0–30, 31–60, 61–90, 90+), escalation level, last reminder, promise date | Filters: date, client, BDE, accounts owner, entity. Add follow-up · Request exception · Export |
| **Collections** follow-up list | `/crm/tasks/view/list` with a saved **Collections** view (`tbo_task_type = Collection`) | Accounts owners, BDE | Task, client, invoice, outstanding, due, promised date and amount, escalation level, next action | Mark done with remark · Re-date · Log call. This reuses CRM's Tasks page, so no new page is needed. |
| **Allocations & Transfers** | `/crm/allocations` | KSA Accounts, India Accounts, Finance, Management (read) | Payable summary (share, settled, pending, due from India); allocation list (receipt, deal, gross, net, share, status); transfers (status, sent, received, rate, difference) | New transfer (picks open allocations FIFO; editable) · Confirm receipt (India) · Open the linked PE |
| **Exceptions & Reconciliation** | `/crm/finance-exceptions` | Accounts, Finance Manager | Queue by category with age; unverified matched bank lines | Propose resolution · Approve / Reject (Finance) · Verify bank line (second person) |
| **Dashboard** cards | `/crm/dashboard` | By role (§8) | Sales, Collections, Receivables, Allocation, Transfers, Reconciliation, Performance | Filters: date, client, employee, entity |

**Phase 1 built** the Finance tab, the New quotation dialog and the BDE conversion page (§14.1).
The fork's notes are in `docs/tbo-finance.md` in tbocloud/crm.

**Fork extension points**, kept small and offered upstream where general:

1. A tab-registration slot on the Deal page, so the Finance tab is a component registered from
   the fork's TBO folder.
2. Routes and sidebar entries shown by capability flags from `tbo_smart`.
3. A dashboard provider hook (`crm_dashboard_providers` in hooks). `get_dashboard`/`get_chart`
   look up `get_<name>` in registered modules as well as `crm.api.dashboard`.
4. An app-permission hook, so Accounts, Finance and Management roles can open CRM without
   becoming Sales Users (G5).

**States:** every screen has loading, empty ("No open receivables"), error and no-permission
states; tables scroll inside their container on phones; amounts are right-aligned with
tabular numbers.

---

## 8. Dashboard and report specification

**Filters on every metric:** date range (default: this month), client (Customer, or CRM
Organization via deal), employee (BDE = `deal_owner`/`lead_owner`; accounts owner =
`tbo_accounts_owner`), entity (Company). "n/a" means the filter does not apply to that metric.
Amounts are in the entity currency, or in SAR when all entities are selected (provisional
INR→SAR at the latest Currency Exchange rate, labelled).

| Group | Metric | Definition | Source | Filters (date / client / employee / entity) |
| --- | --- | --- | --- | --- |
| Sales | Leads | CRM Leads created in range | `CRM Lead.creation` | creation / n/a / lead_owner / n/a |
| Sales | Conversions | Leads converted (deal created) in range; rate = conversions ÷ leads | CRM Deal with `lead`, `creation` | creation / organization / deal_owner / `tbo_company` |
| Sales | Quotations | Count and net value submitted; approval pending count | Quotation (`crm_deal` set, docstatus 1) | transaction_date / party / deal_owner / company |
| Sales | Invoices | Count, net and grand total submitted (excluding returns) | Sales Invoice (`is_return` 0) | posting_date / customer / deal_owner / company |
| Collections | Advance collected | Receipts against Sales Orders plus unallocated receipts | Payment Entry Reference (SO) + `unallocated_amount` | posting_date / party / deal_owner / company |
| Collections | Collected | All customer receipts, net of refunds | Payment Entry (Receive − Pay to Customer), `base_received_amount` | posting_date / party / deal owner, accounts owner / company |
| Collections | Pending | Outstanding as on the range end | Accounts Receivable report class (Payment Ledger Entry) | as-on date / customer / sales person, deal owner / company |
| Receivables | Outstanding | Same as Pending, by client and invoice | AR report | as above |
| Receivables | Overdue | Outstanding on terms with due date < today | AR report `based_on_payment_terms = 1` | as above |
| Receivables | Ageing | Buckets 0–30, 31–60, 61–90, 90+ by due date | AR report `ageing_based_on = Due Date`, `range = 30, 60, 90` | as above |
| Allocation | KSA share | Net receipts − India share | TBO Fund Allocation (`net_amount − share_amount`) | posting_date / customer / deal_owner / company |
| Allocation | Payable to India | Σ share (Receipt − Reversal); pending = Σ outstanding | TBO Fund Allocation | posting_date / customer / deal_owner / company |
| Transfers | Done | Σ allocated on transfers with status ≥ Received | TBO Fund Transfer | transfer_date / n/a / n/a / company |
| Transfers | Pending | Payable not yet transferred, plus transfers Sent but not Received | Allocation outstanding; transfer status | as above |
| Transfers | Status | Count by status; differences total | TBO Fund Transfer | as above |
| Reconciliation | Matched | Bank lines Reconciled and Verified; separately, matched but unverified | Bank Transaction | date / party / n/a / company |
| Reconciliation | Unmatched | Lines Unreconciled/Pending, with age | Bank Transaction | as above |
| Reconciliation | Discrepancies | Open exceptions by category and age; amount at stake | TBO Finance Exception | creation / customer / n/a / company |
| Performance | BDE conversion | Per BDE: leads, converted, rate, won value | CRM Lead, CRM Deal | creation / n/a / owner / company |
| Performance | BDE collection | Per BDE: invoiced, collected, outstanding, % collected, average days to pay | SI + PE by `crm_deal.deal_owner` | posting_date / customer / deal_owner / company |
| Performance | Accounts collection | Same per `tbo_accounts_owner`; plus overdue cleared, follow-ups done | SI, PE, CRM Task | as above |

**Reports** (Script Reports in `tbo_smart`, also usable from the CRM pages through the API):
Deal Finance Summary, Receivables by BDE / Accounts Owner, Fund Allocation Register, Transfer
Register and Difference Report, Reconciliation Exceptions, BDE Performance. Dashboard numbers
and these reports share the **same query functions**, so a card and its report always agree
(tested in §10, T13).

**Visibility:** Sales roles see the Sales, Collections, Receivables and Performance groups for
their own scope (CRM hierarchy rules). Allocation, Transfers and Reconciliation are for
Accounts, Finance and Management only.

---

## 9. Roles and permission matrix

The designation roles **BDE, BDM, Head – Sales & Marketing, Director and CEO** come from the
separate feature "Quotation discount/commission approval" (`docs/quotation-discount-control.md`),
which defines them and their % limits. This plan uses the same names and does not create them
again. CRM access still comes from CRM's own roles (Sales User / Sales Manager), so each
designation role is held together with one of them.

| Brief role | Frappe roles | Company restriction |
| --- | --- | --- |
| BDE Team | **BDE** + Sales User | – (CRM hierarchy: own deals) |
| Sales Team | **BDM** + Sales User (sales hierarchy above their BDEs) | – |
| Sales head | **Head – Sales & Marketing** + Sales Manager | – |
| KSA Accounts | Accounts User (+ Accounts Manager for the lead) | User Permission: Company = KSA |
| India Accounts | Accounts User (+ Accounts Manager for the lead) | User Permission: Company = India |
| Finance Manager | Accounts Manager + **TBO Finance Approver** (new) | Both companies |
| Management | **Director** or **CEO** + **TBO Management** (new, read-only) | Both companies |
| IT Administrator | System Manager | – |

Two new roles in this plan, because existing roles do not separate "approves exceptions and
verifies reconciliation" (TBO Finance Approver) from "can post accounting", or give read-only
cross-entity visibility without posting rights (TBO Management).

R = read, C = create, W = write, S = submit, X = cancel, A = approve / verify. "own" = own or
team deals by CRM rules. Quotation % limits and escalation are set in the discount-control
feature's settings, not here.

| Doctype / action | BDE | BDM | Head – Sales & Marketing | KSA Accounts | India Accounts | Finance Manager | Director / CEO | IT Admin |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CRM Lead / Deal / Task / Note | R C W own | R C W team | R C W all | R (deals with finance), W `tbo_accounts_owner`, tasks | R | R | R | all |
| Quotation (create, adjust discount/commission) | R C W own; adjust within limit; request approval above it | R W team; review BDE quotes; adjust within limit; escalate above it | Approve within limit; forward above it to Director | R | – | R | Director: adjust and approve within authority, no further approval. CEO: approve exceptions and overrides | all |
| Sales Order / Sales Invoice (refused while a quotation approval is pending) | R own (Finance tab) | R team | R | R C W S X | R (own company) | R | R | all |
| Payment Entry | R own (Finance tab) | R team | R | R C W S X (KSA) | R C W S X (India) | R | R | all |
| Dunning | R own | R team | R | R C W S | – | R | R | all |
| TBO Fund Allocation | – | – | – | R (system creates) | R | R; reversal approvals | R | all |
| TBO Fund Transfer | – | – | – | R C W S (send) | R W (confirm receipt, link India PE) | R X | R | all |
| TBO Finance Exception | C (Receivable: request) | C, R team | C, R all | C R W (propose) | C R W (propose) | **A** (approve/reject) | R; A above the limit if Q5 sets one | all |
| Bank Statement Import / Bank Transaction | – | – | – | R C W S (KSA) | R C W S (India) | R; **A** verify | R | all |
| Verify bank line | – | – | – | A only for lines another user matched (if Accounts Manager) | same | A | – | – |
| TBO Smart Settings (Finance) | – | – | – | – | – | R | – | R W |

**Permission mechanics.**
- A `has_permission` / `permission_query_conditions` hook in `tbo_smart` limits the TBO
  doctypes by company. Transfers are visible to both companies' accounts users, so the hook
  ignores standard user-permission matching on `from_company`/`to_company` and applies its
  own rule.
- Finance tab methods return allocation fields only to Accounts, Finance and Management.
- Accounts roles get read on CRM Deal through Custom DocPerm, plus the app-permission hook (§7).

**Audit:**
- Version on every doctype above (CRM Task newly enabled).
- Workflow Action for approvals; approver ≠ requester is enforced.
- Corrections only through cancel/amend, reversal or Journal Entry, never edits to submitted
  documents.
- Settings changes are tracked (TBO Smart Settings already has `track_changes = 1`).
- An **Audit Log** report lists Version and Workflow Action rows for finance doctypes, filtered
  by user and date.

---

## 10. Test plan

Setup: companies **TBO KSA** (SAR, VAT 15%) and **TBO India** (INR). Customer "Acme Trading"
from deal **D1** (BDE: bde1, accounts owner: acc_ksa1). Allocation 50%. Tolerance SAR 1.00.
Automated backend tests live in `tbo_smart/tests/`, with helpers in `tbo_smart/test_utils.py`
(`make_finance_company`, `make_deal_with_quotation`, `make_receipt`, …). UAT uses the same
script on staging.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| T1 | Lead → conversion | Create a lead (owner bde1), add a task due tomorrow, log a call, convert | Deal D1 with deal_owner bde1; task and notes on the lead; Leads +1, Conversions +1 for bde1 |
| T2 | Onboarding | Move D1 to the customer-creation status; complete the checklist | Customer created with `crm_deal = D1`; `tbo_stage` Onboarding → Quotation |
| T3 | Quotation → approval → order | New quotation from the Finance tab: 1 item SAR 100,000 + VAT 15%; no adjustment above bde1's limit | Quotation 115,000.00 submitted as final; SO created with `crm_deal = D1`. Discount/commission approval paths are tested by the separate feature. |
| T3b | No invoicing while approval is pending | bde1 sets a discount above their limit (approval pending), then tries to create a SO and a SI from the quotation | Both refused with a clear message; allowed after the approval completes |
| T4 | Invoice | SI from the SO with payment terms 30/40/30 | SI 115,000.00; three terms 34,500 / 46,000 / 34,500; `crm_deal = D1`; stage Invoiced |
| T5 | Advance | PE-1 SAR 34,500 against the SO | Allocation net 30,000.00, share 15,000.00; payable 15,000.00; Advance collected 34,500 |
| T6 | Partial | PE-2 SAR 40,250 against the SI (after the advance is pulled in) | Outstanding 40,250.00; allocation share 17,500.00; payable 32,500.00 |
| T7 | Outstanding calculation | Run AR as on the day after T6 | D1 outstanding 40,250.00 matches the Finance tab, Receivables page and dashboard |
| T8 | Reminders | Set a term due date = today + 3; run the job on D−3, D0, D+7 | One email per step (no repeats on re-run); D0 task for acc_ksa1; D+7 notification to bde1 |
| T9 | Overdue escalation | Advance the clock to D+15 and D+30 | Draft Dunning (Reminder 2) at D+15 and BDM / Head – Sales & Marketing notified; Final notice at D+30, the Finance Manager notified and listed in the digest |
| T10 | 50% calculation | Balance PE-3 SAR 40,250 (closing) + example B | Share 17,500.00 (total 50,000.00); example B shares 4,347.83 + 12,318.84 = 16,666.67 |
| T11 | Transfer tracking | TRF-1 SAR 32,500 with PE-1 and PE-2 allocations; India PE INR 760,500 | Allocations Settled; pending 17,500.00; effective rate 23.40; difference 0. Variant: send 32,400 → difference −100.00 → exception Transfer short |
| T12 | Bank reconciliation | Import KSA and India statements with the receipts and transfer lines; reconcile in the tool | Lines Reconciled + Unverified; the same user cannot verify; a second user verifies → Matched; TRF-1 Matched |
| T13 | Mismatch and duplicate detection | Re-import the same statement; reconcile a line of 34,450 to PE-1 of 34,500 | Possible duplicate exceptions for each repeated line (not blocked); amount mismatch exception of 50.00 |
| T14 | Exception approval | acc_ksa1 proposes "Accept difference" (bank charge) and links the JE | acc_ksa1 cannot approve; the Finance Manager approves; Version and Workflow Action recorded |
| T15 | Refund / reversal | Credit note 11,500 + refund PE | Reversal allocation −5,000.00; payable falls by 5,000.00 |
| T16 | Cancel after transfer | Cancel PE-1 after TRF-1 | Reversal −15,000.00 + exception "Receipt cancelled after transfer"; due from India 15,000.00 shown if nothing else is pending |
| T17 | Dashboard accuracy | Compare each card with its report and with ERPNext AR / GL for the same filters | Equal to the cent, for each entity, BDE, client and date filter |
| T18 | Permissions | Log in as each role | BDE sees only own deals, no allocation fields; India Accounts cannot submit KSA PEs; Management can read but every write is refused; `/api/resource` calls follow the same rules |
| T19 | Audit | Edit a follow-up, verify a line, approve an exception, change a setting | Version rows for each, with user and time; the Audit Log report lists them |
| T20 | Closure | Pay in full, settle, verify, no open exceptions | `tbo_stage` Closed only when all four exit conditions hold |

---

## 11. Phased timeline

Estimates assume **one full-stack developer** plus a part-time tester and a finance user for
UAT. Dates are indicative from approval on **2026-10-23**. A second developer working
frontend in parallel shortens it by about 30%.

| Phase | Scope | Effort (dev days) | Depends on | Indicative dates |
| --- | --- | --- | --- | --- |
| 0. Confirm | Answers to §12; verify-on-site checklist; staging site with a production copy; theme PR merged | 3 | Management answers | 26 – 30 Oct 2026 |
| 1. Leads, BDE, follow-up, Deal finance view | Custom fields (§3.1 deal/SO/SI/PE), deal-link hooks + backfill, `tbo_stage`, CRM quotation dialog (G3), "approval pending" guard on SO/SI, Finance tab (read; embeds the discount-control approval actions), next-follow-up and BDE conversion report, app-permission hook | 11 | Phase 0; CRM theme; Quotation discount/commission approval feature merged | 2 – 17 Nov |
| 2. Receivables and reminders | Receivables page, Collections view, collection-task fields, reminder job + templates, escalation, Dunning types, TBO Finance Exception (Receivable category) + workflow, daily digest | 11 | Phase 1 | 18 Nov – 2 Dec |
| 3. Allocation and transfers | Fund Allocation (+ lines) with PE hooks, reversals, Fund Transfer, Allocations & Transfers page, chosen accounting option (§4.6), statements | 12 | Phase 2; Q1–Q8 answered | 3 – 19 Dec |
| 4. Reconciliation and approvals | Dedupe, mismatch and unmatched checks, verification + lock, exception queue page, Bank/Transfer categories | 10 | Phase 3; bank statement samples from both banks | 5 – 19 Jan 2027 |
| 5. Dashboard | Provider hook, finance cards, shared report queries, filters, Audit Log report | 7 | Phases 1–4 | 20 – 28 Jan |
| UAT, training, go-live | Test plan §10 on staging with Finance; training; production cut-over | 5 | Phase 5 | 29 Jan – 4 Feb |
| **Total** | | **≈ 59 dev days** | | **≈ 14 weeks** (late December is a buffer) |

Each phase ships on its own behind its own switch in TBO Smart Settings (Phase 1:
`enable_finance_workflow`), off by default, so Phase 1 can go live before Phase 3 exists.

### 11.1 Deployment plan

| Step | Detail |
| --- | --- |
| Code | PRs to `tbocloud/tbo_smart` (backend, fixtures, tests) and `tbocloud/crm` (fork UI + extension points). CI green and review before merge; finance PRs get the `no-auto-merge` label until Finance signs off UAT. |
| Order | Merge and deploy the CRM fork first (extension points are inert without `tbo_smart`), then `tbo_smart`. |
| Press | Update the tboindia bench group to the new commits of both apps, then deploy. `bench migrate` creates the custom fields (`after_migrate`), roles, workflows and Email Templates (fixtures). **[verify on site: Press bench group, branch pins, staging site availability]** |
| Safety | Press backup before each deploy; flags default off; Phase 3 starts allocations only from `allocation_start_date`. |
| Rollback | Turn the flag off (hooks and jobs check it). Custom fields and doctypes stay and hold no ledger data, so the books are unaffected. |
| Go-live per phase | Enable on production → smoke test (T1, T5, T7 on a test customer, then cancel) → announce to users. |

---

## 12. Open questions and verify-on-site items

### 12.1 Open questions for Management / Finance

| # | Question | Default used in this plan |
| --- | --- | --- |
| Q1 | Which accounting option for India's share: monthly inter-company invoice (A), JE per receipt (B), or memo only (C)? Confirm with the tax advisor (KSA WHT, Indian GST on export of services, transfer pricing). | **C, memo only** (default chosen, pending confirmation): allocations and transfers are a sub-ledger and post nothing to the GL. A and B are follow-ups. |
| Q2 | Are TBO KSA and TBO India both Companies on tboindia ERPNext? If KSA's books are elsewhere, how do we get its receipts? | Both on tboindia; Phase 3 reads the companies from settings, and if the invoicing company does not exist allocation is skipped with an Error Log warning. Phase 1: a deal's `tbo_company` defaults to ERPNext CRM Settings' company, so today's behaviour holds (default chosen, pending confirmation). |
| Q3 | Exchange-rate source for SAR→INR differences: the bank's achieved rate, or a reference rate (SAMA/RBI) on a date? | Bank's achieved rate: the transfer's `effective_rate` = received ÷ sent (default chosen, pending confirmation). |
| Q4 | Does a refund to a client reverse India's share? If India was already paid, is the excess netted against the next transfer or paid back? | Yes, a Reversal allocation; netted in the next transfer (default chosen, pending confirmation). |
| Q5 | Who approves exceptions (waivers, accepted differences, extensions)? Is there an amount limit above which Management must also approve? | Finance Manager (TBO Finance Approver); a second, different approver above `second_approval_amount`, blank = not required (default chosen, pending confirmation). |
| Q6 | Is the 50% base the amount credited to the client, or the cash after the client's bank charges? | Credited to the client (`allocation_base_after_bank_charges` = 0; default chosen, pending confirmation). |
| Q7 | Who bears transfer bank charges: KSA, India, or shared? | KSA bears the transfer charges: they never reduce what a transfer settles (default chosen, pending confirmation). Only invoices of the KSA company are allocated. |
| Q8 | Is withholding tax deducted on KSA→India transfers? If so, does it count as paid to India? | Not deducted automatically; a transfer can record `withholding_tax`, which counts as settled, and a difference is a known exception type (default chosen, pending confirmation). |
| Q9 | Does KSA issue a tax invoice for advances (VAT due on advance receipt), or receive advances against a Sales Order only? Are Sales Orders used at all? | SO + advance PE; advance tax invoice if the advisor requires it |
| Q10 | Accounts staff post invoices and payments and reconcile banks in ERPNext screens (`/app`); CRM shows and approves. Is that acceptable, or must Accounts also post from CRM? | Acceptable: matching stays in ERPNext's Bank Reconciliation Tool; the CRM verifies and decides (default chosen, pending confirmation). |
| Q11 | Is a Microsoft Teams digest required now? | Email + in-app/push now; Teams later (default chosen, pending confirmation; `build_digest` returns structured data for a later Teams post). |
| Q12 | Which deal status creates the ERPNext Customer, and what does onboarding require (CR no., VAT no., contract, PO)? | Phase 1 leaves it to ERPNext CRM Settings' status as configured; "Onboarding complete" is a single tick, no checklist yet (default chosen, pending confirmation). |
| Q13 | Quotation adjustment: does the % apply to the quotation value (discount), the commission, or both? (Owned by the discount-control feature; must be configurable.) | Configurable; no default chosen |
| Q13b | Exact maximum % per designation: BDE, BDM, Head – Sales & Marketing, Director (CEO is unlimited). | None set; settings left empty until decided |
| Q14 | Reminder wording, languages (English/Arabic) and sender mailbox; should clients get reminders at D−3? | English Email Templates, default outgoing account; client emails **off** (`send_client_reminders` = 0) until confirmed (default chosen, pending confirmation). |
| Q15 | Does India ever invoice clients directly (INR deals)? Are those outside the 50% rule? | Yes, outside the rule; per-deal company field |
| Q16 | Allocation start date, and whether past receipts should be back-calculated for reporting only. | Deploy date (set on migrate); "Before start date" preview on the Allocations page records and posts nothing (default chosen, pending confirmation). |
| Q17 | Can BDEs see invoice and payment amounts for their own deals? | Yes; not allocation. Setting `finance_bde_can_see_amounts`, default on (default chosen, pending confirmation). |
| Q18 | Who is the "accounts owner" for each client (KSA Accounts staff list)? | Per deal (`tbo_accounts_owner`); fallback the KSA Accounts lead in settings, then the deal owner (default chosen, pending confirmation). |

### 12.2 Verify on site (tboindia.tbocloud.in)

| # | Item | Why |
| --- | --- | --- |
| V1 | Installed apps and versions (`frappe.utils.change_log.get_versions`): Frappe v15? CRM fork commit? ZATCA/KSA app? | G15, R2, deploy plan |
| V2 | Companies: names, abbreviations, default currency, country, parent company, internal customer/supplier set up between them | D2, §4.6 |
| V3 | Enabled currencies; Currency Exchange records and Currency Exchange Settings (provider) | §4.2 |
| V4 | Chart of accounts per company: receivable (SAR/INR), VAT output accounts, exchange gain/loss and unrealised accounts, bank charges, inter-company accounts | §4.6 |
| V5 | Bank Accounts per company (is_company_account, GL account, currency) and the statement formats of each bank | §5 |
| V6 | ERPNext CRM Settings: enabled, same site, `erpnext_company`, customer-creation status, product sync | §1.1 |
| V7 | Existing Custom Fields and Property Setters on Quotation, Sales Order, Sales Invoice, Payment Entry, Customer, CRM Deal, CRM Lead, CRM Task, Bank Transaction | Avoid name clashes |
| V8 | Existing Workflows (Quotation, Sales Invoice, Payment Entry) and Notifications | G4, §6 |
| V9 | Sales Taxes and Charges Templates (KSA VAT 15%), Payment Terms Templates | §4.1, T4 |
| V10 | Dunning Types, Email Accounts (outgoing for the KSA accounts mailbox), Email Templates | §6 |
| V11 | Roles held by sales and accounts users; User Permissions on Company; FCRM sales hierarchy on/off | §9 |
| V12 | Count of submitted SIs and PEs without a deal link; quotations with `crm_deal` | R4 backfill size |
| V13 | Whether ERPNext Project is used and linked to Sales Orders | `tbo_project` |
| V14 | Accounts Settings: "Book advance payments in separate party account", "Unlink payment on cancellation", accounting period locks | §4.3 |
| V15 | Press: bench group, app branches pinned for `crm` and `tbo_smart`, staging site, backup schedule | §11.1 |
| V16 | Scheduler enabled and the workers (short, default, long) running | §6 |

---

## 13. User documentation and training

| Audience | Material | Format | Length |
| --- | --- | --- | --- |
| BDE / BDM | "From lead to paid": follow-ups, quotations (discount/commission limits from the separate feature's guide), the Finance tab, collection tasks | 1-page quick guide + 10-min screen recording | 45 min session |
| Head – Sales & Marketing | Team receivables, BDE performance (quotation approvals: see the discount-control feature's guide) | Quick guide | 30 min |
| KSA Accounts | Invoices and receipts with deal links, reminders and Dunning, allocations, sending transfers, bank import and reconciliation, proposing exceptions | Step-by-step guide with screenshots | 2 × 60 min |
| India Accounts | Confirming receipts, FX and bank charges, India-side reconciliation, verification | Step-by-step guide | 60 min |
| Finance Manager | Exception approval, verification, period statements, audit log | Guide + checklist (daily, month-end) | 45 min |
| Director / CEO | Dashboard filters and what each number means (§8 definitions) | 1-page metric glossary | 20 min |
| IT Administrator | Settings, flags, jobs, roles, deploy and rollback, troubleshooting (Error Log) | Admin notes, in this repo | Handover |

Documentation lives in `tbo_smart/docs/` (this spec plus user guides). The metric glossary is
generated from §8 so the dashboard tooltips and the guide use the same wording.

---

## 14. Implementation status

### 14.1 Phase 1: deal links, stage, Finance tab, quotation dialog, guard, BDE report (built)

Switch: **TBO Smart Settings → Finance workflow (Phase 1)** (`enable_finance_workflow`), off by
default. While it is off every hook, job and API below does nothing (the APIs refuse with a
ValidationError and the CRM shows the stock Quotations tab). Only the custom fields and the
one-off backfill run regardless.

**Deal links (G1)** - `tbo_smart/finance/deal_links.py`, `tbo_smart/finance/documents.py`.

- ERPNext's own mappers already copy `crm_deal` from a Quotation to the Sales Order and from
  the Sales Order to the Sales Invoice (same fieldname, not `no_copy`). The `validate` hooks
  fill it when it is still blank: Sales Order from its items' quotations
  (`prevdoc_docname` → `Quotation.crm_deal`, or the party when *Quotation To* is CRM Deal);
  Sales Invoice from its items' sales orders, else those orders' quotations.
- Payment Entry: `crm_deal` always follows the references. Every Sales Invoice / Sales Order
  reference must carry the same deal; otherwise it is blank. A receipt with no invoice or
  order reference (an advance booked on account) keeps a deal typed on it.
- **Decision:** no guessing from the customer. A Sales Invoice made straight from a
  Quotation gets the deal from the mapper; one typed by hand with no source stays blank
  unless someone fills the (editable) field. Customer-based guessing would attach a repeat
  client's new invoice to an old deal.
- **Backfill** - `tbo_smart/finance/backfill.py`, patch
  `tbo_smart.patches.v1.backfill_finance_deal_links`. Runs once on migrate, whatever the
  switch. Set-based queries per doctype; fills only blank values, so it is idempotent.
  Documents it cannot resolve are skipped, counted on the console and listed by name in the
  `tbo_smart.finance` site log for Accounts to link by hand.

**Company per deal (G2).** `CRM Deal.tbo_company` (Link Company). A new deal gets ERPNext CRM
Settings' `erpnext_company` in `before_insert` (when that names a Company). Existing deals
fall back to it at read time. The quotation dialog lets the user pick another company, so a
deal can be quoted by KSA or India; Q2 is still open.

**Post-sale stage** - `tbo_smart/finance/stage.py`, data from
`tbo_smart/finance/deal_finance.py` (shared with the Finance tab, so the tab and the stage
always agree).

| Stage | When (first match wins) |
| --- | --- |
| Closed | `tbo_closed` is set (a person closed it) |
| Collected | Submitted, non-return invoices exist and the deal's total outstanding is 0 |
| Collecting | Invoices exist and something was received (any receipt against the deal's orders or invoices, or an unallocated receipt carrying the deal), or a term is overdue |
| Invoiced | Invoices exist, nothing received, nothing overdue |
| Quotation | Onboarding is complete, or the deal has a quotation (draft or submitted) or a sales order |
| Onboarding | The deal has an ERPNext customer |
| (blank) | Nothing post-sale yet |

- Recomputed by `doc_events` on Quotation save/submit/cancel/delete and on submit/cancel of
  Sales Order, Sales Invoice and Payment Entry (a payment recomputes every deal its references
  touch), plus a daily job (`recompute_all`) over every deal with a stage, a customer, an
  onboarding tick or a linked document. A failing deal is logged and skipped.
- **Decision:** the stage is written with `frappe.db.set_value` plus a **Version** row, not a
  full deal save, so an invoice submit never re-runs Frappe CRM's deal `on_update` (customer
  creation, automations). CRM Deal already has `track_changes` on, so the history is in the
  usual Version list. A `tbo_deal_finance_update` realtime event tells open deal pages.
- "Onboarding complete" (`set_onboarding_complete`) is for anyone who may edit the deal, or
  a finance user. "Close finance" (`set_finance_closed`) is for Accounts Manager or System
  Manager and only when the stage is Collected; Phase 4 adds the other closure conditions
  (allocations settled, transfers verified, no open exceptions) and the TBO Finance Approver
  role. Both write a Version and a timeline comment.
- Settling, Reconciled and On Exception are in the field's options but not computed yet.

**Quotation from the CRM (G3)** - `tbo_smart/api/finance/quotation.py`.

| Method | HTTP | Does |
| --- | --- | --- |
| `get_quotation_form(deal, quotation=None)` | GET | Dialog values: companies, the company's tax templates, and either a new quotation prefilled from the deal (items from `prefill_quotation_items`, party = the deal's Customer or the deal itself, company default tax template, valid till +1 month) or an existing quotation of this deal. |
| `get_item_price(item_code, company=None)` | GET | Name, UOM and the Selling Settings price list rate of a sales item. |
| `save_quotation(deal, values, quotation=None)` | POST | Creates a draft Quotation (crm_deal, party, company, primary contact, organization address) or updates a draft of this deal: items (qty, list rate, discount %), additional discount %, commission % (`tbo_commission_rate`), valid till, taxes template and terms. Saved as the session user, so ERPNext's Quotation permissions and the discount/commission control apply as on the desk form. Returns the quotation and `needs_approval` (kinds above the user's limit). |
| `submit_quotation(deal, quotation)` | POST | Submits a draft of the deal; the discount/commission control refuses it while an approval is missing. |
| `can_create_desk_quotations()` | GET | Overrides Frappe CRM's `can_create_quotations` (`override_whitelisted_methods`): False while the workflow is on, so the deal's desk "Create Quotation" action disappears; otherwise CRM's own answer. |

A quotation belongs to the deal when its `crm_deal` is the deal or it is made out to the deal
(*Quotation To* = CRM Deal), the same rule the Finance tab lists by. Editing in the CRM is
limited to drafts and to the fields above; "Open in ERPNext" stays for
anything else. **Deviation:** the plan only asked for drafts; Submit was added so a BDE never
needs `/app` for a quotation within their limit.

**Pending-approval guard** - `FinanceDocument.block_pending_approval`, using
`tbo_smart.quotation_control.get_unapproved` (added to the discount-control module, not
duplicated). A Sales Order or Sales Invoice (not a return) is refused on `validate` when any
source quotation, for a limited kind with a non-zero percentage, has an open (not voided)
request, or has no granted, not-voided adjustment covering it **and** is either still a
draft or had an approval voided. A quotation submitted while enforcement was off, with no
adjustment rows, passes, so switching enforcement on later does not block old orders.
Source quotations: Sales Order items' `prevdoc_docname`; Sales Invoice through its orders. A
Sales Invoice made straight from a Quotation has no item link back; ERPNext only allows that
from a submitted quotation, and submitting already needs the approval.

**Finance tab data** - `tbo_smart/api/finance/deal.py`.

| Method | HTTP | Returns / does |
| --- | --- | --- |
| `get_finance_access()` | GET | `{enabled, is_finance_user, can_close, can_see_amounts}`; the CRM uses it to choose the tab and the sidebar entry. |
| `get_deal_finance(deal)` | GET | Stage and stage list, company, customer, onboarding/closed flags, what the user may do, quotations (status, docstatus, dates, total), sales orders (status, dates, total, advance), invoices (status, dates, grand/rounded total, outstanding, overdue, payment-term rows), payments (one row per Payment Entry: date, mode, reference, signed amount, currency), totals per currency (invoiced, received, outstanding, overdue) and open follow-up tasks. |
| `set_onboarding_complete(deal, complete)` | POST | See the stage above. |
| `set_finance_closed(deal, closed)` | POST | See the stage above. |

Permissions (`tbo_smart/finance/access.py`): the deal must be readable under Frappe CRM's own
rules (owner, assignee, hierarchy, Sales Manager), or the user holds a role from
`finance_crm_roles` while `finance_crm_access` is on. ERPNext document permissions are not
needed on purpose (plan §9, "R own (Finance tab)"): the documents are read after the deal
check. With `finance_bde_can_see_amounts` off, users without a manager, approver or accounts
role get the documents with every invoice, order, term and payment amount set to null and no
totals; quotation totals stay visible because the deal owner already sees quotations.

**BDE conversion report** - `tbo_smart/api/finance/report.py`, `get_bde_conversion(from_date,
to_date, organization, company)`. Per salesperson: leads created (by `lead_owner`), deals
created from a lead (by `deal_owner`), conversion rate, submitted quotations (by quotation
date, owner of the deal), won deals (Won-type status, `closed_date` in range) and won value
(`deal_value × exchange_rate`, CRM's base currency, as CRM's dashboard counts it), plus a
total row. Default range: this month; at most two years. Client = CRM Organization; company
= the deal's `tbo_company` (CRM default when blank) or the quotation's company; leads have no
company, so that filter leaves them alone. Scope follows the mobile API's CRM scope rule:
own row, team (sales hierarchy) or everyone (Sales Manager outside the hierarchy, System
Manager, finance roles). **Decision:** a TBO page (`/crm/bde-conversion`), not a CRM
dashboard card: CRM's dashboard methods take only a date range and a user, with no client or
entity filter, so the provider hook waits for Phase 5.

**App access (G5).** The fork gains two hooks (tbocloud/crm `docs/tbo-finance.md`):
`crm_app_permission` (admit more users to `/crm`) and `crm_unrestricted_access` (skip the
sales hierarchy for a user, doctype and permission type). tbo_smart registers
`has_crm_app_access` and `has_unrestricted_crm_access`: both are True only for the
`finance_crm_roles` while the workflow and `finance_crm_access` are on, and the second only
for CRM Deal reads. Saving TBO Smart Settings with access on adds a read Custom DocPerm on CRM
Deal and CRM Organization for each of those roles (which also opens the FCRM module to them)
and remembers what it did (hidden field `finance_crm_granted`): rows it added, and existing
rows it widened (a row with no read, or read only if owner, gets full read; its original
values are kept). Turning access off, or taking a role off the list, deletes the added rows
and puts widened rows back. A pre-existing row that already gives full read is never touched. Deploy the CRM
fork first: without its hooks, Accounts still read the Finance tab data through the API, but
cannot open `/crm`.

**Tests** - `tbo_smart/tests/test_finance_workflow.py` (helpers in `tbo_smart/test_utils.py`):
deal links through quotation → order → invoice → payment and a mixed-deal payment; hooks
inactive while off; the backfill (linked, unresolved listed, idempotent); stage progression
with its Version history, overdue → Collecting, onboarding and closing permissions and logs,
the nightly job, the default company; the quotation API (prefill, save, edit, submit,
refusals); the guard (pending request, draft above limit, quotation from before enforcement,
switched off); Finance tab permissions (own vs other BDE, Accounts with and without CRM
access, hidden amounts, switched off, Custom DocPerm and role validation); and the report
numbers, filters and scope. The fork has tests for both hooks.

**Not in Phase 1 (plan §11 lists them under Phase 1):** "next follow-up" in the lead and deal
lists (the Finance tab shows the deal's next follow-up), the onboarding checklist (a single
tick for now, Q12), `tbo_project` and `tbo_accounts_owner` (Phase 2).

### 14.2 What the owner switches on after deploying Phase 1

1. Deploy tbocloud/crm first, then tbo_smart; `bench migrate` creates the fields and runs the
   backfill (check the migrate output and the `tbo_smart.finance` log for unresolved
   documents).
2. TBO Smart Settings → Finance workflow: tick **Finance workflow (Phase 1)**.
3. Optional: untick **BDE can see invoice/payment amounts** if management answers Q17 "no".
4. Optional: tick **Let finance roles open the CRM** and check **Finance roles in the CRM**
   (Accounts User and Accounts Manager by default), so Accounts can use the Finance tab.
5. For the guard to bite, the discount-control feature's **Enforce adjustment limits** must
   also be on (TBO Quotation Approval Settings).

### 14.3 Phase 2: receivables, reminders, escalation, exceptions and digest (built)

Switch: **TBO Smart Settings → Receivables and reminders (Phase 2)**
(`enable_finance_receivables`), off by default; it also needs Phase 1 on. While it is off the
jobs do nothing, the APIs refuse with a ValidationError and the CRM hides the pages and
actions. Fields, roles, Email Templates and the CRM Task property setter are created on
migrate either way (`tbo_smart.finance.setup.setup_finance`), and change nothing by
themselves.

**Created on migrate:** CRM Deal `tbo_accounts_owner`; Payment Schedule `tbo_last_reminder`
and `tbo_last_reminder_on` (allow on submit, read-only); CRM Task `tbo_task_type`,
`tbo_sales_invoice`, `tbo_promised_date`, `tbo_promised_amount` and `tbo_escalation_level`;
property setter **CRM Task → track_changes = 1** (G14); roles **TBO Finance Approver** and
**TBO Management**; English Email Templates "TBO Payment Due Soon", "TBO Payment Due Today"
and "TBO Payment Reminder" (Finance edits the wording there). Dunning Types "Reminder 2" and
"Final Notice" are created per company, no fee and no interest, the first time the job needs
one. Saving the settings with both switches on adds a public **Collections** view
(collection tasks, due date first) to the CRM Tasks page.

**Reminder job** - `tbo_smart/finance/reminders.py`, daily at 09:00 site time (cron
`0 9 * * *`; the site's time zone stands in for Asia/Riyadh). It reads each open Payment
Schedule row of submitted, non-return invoices (of `invoicing_company` when set) and runs
the latest step that is due, once per row:

| Step | Client email (only with `send_client_reminders`) | Internal | Level |
| --- | --- | --- | --- |
| D-3 | "TBO Payment Due Soon" + invoice PDF | - | 0 |
| D0 | "TBO Payment Due Today" | Collection CRM Task "Collect {invoice}" for the accounts owner, due today, linked to the deal | 1 |
| D+7 | "TBO Payment Reminder" | Task escalated (High); accounts owner and deal owner notified | 2 |
| D+15 | - | **Draft** Dunning "Reminder 2" for Accounts to submit and send; sales head, accounts lead and the deal owner's manager (CRM sales hierarchy) notified | 3 |
| D+30 | - | **Draft** Dunning "Final Notice"; Finance Manager notified; in the digest | 4 |
| Weekly after D+30 | "TBO Payment Reminder" only with `weekly_client_reminders` | Task re-dated to today; owners notified | 4 |

- A missed day never sends a backlog: only the latest due step runs, and a row never goes
  back to a step it passed. The row's `tbo_last_reminder`/`_on` make re-runs harmless.
- Each row runs inside a savepoint: task, dunning and marker are written first and undone
  together if any fails. The next run then retries the whole step cleanly, with no partial
  task or Dunning left from the failed attempt. Notifications and the client email go out
  only after the step is recorded, so a failed attempt never sends anything.
- Accounts owner = the deal's `tbo_accounts_owner`, else the KSA Accounts lead, else the deal
  owner (Q18). Notifications are Notification Logs (bell, email per user settings, and mobile
  push through `tbo_smart.push`).
- Client emails go through Frappe's Communication (`make`), so each is on the invoice's
  timeline and linked to the deal's; CC the deal owner; sender = `reminder_sender` or the
  default account. A mail failure is logged and never stops the internal steps.
- **Deviation:** the plan said D+7 "reassigns the task with the BDE added". CRM Task has a
  single assignee, so the task stays with the accounts owner and the BDE is notified instead.

**Exceptions** - doctype **TBO Finance Exception** (§3.5, all fields; Phase 2 uses the
Receivable category) and `tbo_smart/api/finance/exceptions.py`.

| Request (Receivables page or Finance tab) | Requested action | Effect once approved |
| --- | --- | --- |
| Extension request | Extend due date (`new_due_date`, whole invoice or one term) | The job counts days from the new date and restarts the steps for that row. **The invoice is not changed**: ERPNext has no supported way to edit a submitted invoice's payment terms, so ageing in ERPNext's own reports keeps the booked date. |
| Pause request / Client dispute | Pause reminders (`pause_until`) | The job skips the row (or invoice) until that date. |
| Write-off request | Waive balance | Approval needs a **submitted credit note against the invoice** or a **submitted journal entry for the customer** linked (`resolution_reference_*`); the job then stops for that invoice. |

- Status flow: Open → Pending Approval → Approved / Rejected, Rejected → Open, only through
  `decide_exception` (`request_approval`, `approve`, `reject`, `reopen`); editing the status
  directly is refused. A request from the CRM goes straight to Pending Approval.
- Maker-checker: only TBO Finance Approver (or System Manager) decides, and never the person
  who raised it or sent it for approval (`requested_by`). A rejection needs a reason. Once an
  exception leaves Open, everything the approver decides on (type, action, document, term,
  amounts, dates, reason, resolution document) and the audit fields (requested by, decided by,
  decision remarks) are frozen for any save; to change the request, reject and reopen.
  Sending for approval and reopening are for the person who raised it or a finance role that
  can edit exceptions; read access alone (e.g. TBO Management) cannot reopen.
- **Deviation:** no Frappe Workflow fixture. The rules live in the controller and the API,
  which keeps them testable and avoids a desk workflow nobody uses; Version
  (`track_changes`), `requested_by`, `approved_by`/`approved_on`, decision remarks and a
  timeline comment per step are the audit trail.
- Who sees them (`permission_query_conditions` / `has_permission`): accounts, finance,
  management, Sales Manager and System Manager see all; a salesperson sees the ones they
  raised and those on their own (or team's) deals, and can only edit their own. While no
  phase that raises exceptions is on, a salesperson sees none, even through the desk.
- Amounts: with "BDE can see invoice/payment amounts" off, the exception amounts (expected,
  actual, difference) and a follow-up's promised amount come back empty, like invoice amounts.

**Receivables page and follow-ups** - `tbo_smart/api/finance/receivables.py`.

| Method | HTTP | Returns / does |
| --- | --- | --- |
| `get_receivables(company, customer, deal_owner, accounts_owner)` | GET | Open invoices with ageing by each term's due date (not due, 1-30, 31-60, 61-90, 90+), overdue, days overdue, deal and accounts owner, escalation level, last reminder, promise to pay, open exceptions and terms; totals per client and per currency. Up to 2,000 invoices. |
| `add_collection_follow_up(invoice, due_date, remark, promised_date, promised_amount)` | POST | Updates the invoice's open collection task (or creates one assigned to me): next date, promise to pay, and the remark appended with time and user. |
| `set_accounts_owner(deal, user)` | POST | Accounts, finance and managers only. |

- Scope: accounts, finance, management, Sales Manager, System Manager and the Phase 1
  finance roles see every invoice; a salesperson sees invoices of their own (or team's)
  deals; invoices with no deal only appear to the first group. With "BDE can see
  invoice/payment amounts" off, salespeople get a 403 (the page is about amounts).
- **Deviation:** outstanding and ageing come from the invoices and their Payment Schedule
  rows (ERPNext keeps both in step with the payment ledger), not from the Accounts
  Receivable report class. Unallocated advances are therefore not in the page; ERPNext's
  AR report remains the accounting view.

**Daily digest** - `tbo_smart/finance/digest.py`, 08:00 site time. It honours approved
exceptions exactly as the job does (waived and paused terms left out, extensions move the due
date) and counts only Receivable exceptions. `build_digest` returns the totals per currency, terms that fell overdue since yesterday, terms 30+ days overdue and
how many exceptions are open or waiting; `send_digest` emails it to the digest recipients and
notifies the Finance Manager in the app. Payable and pending transfers join with Phase 3.

**Finance tab additions:** accounts owner (editable by accounts and managers), *Add
follow-up* and *Request exception* on each open invoice, collection details (level, promise)
on follow-ups, and an Exceptions section.

**Tests** - `tbo_smart/tests/test_finance_receivables.py`: the step rule (missed days,
weekly cadence), each step once with task, notifications and draft dunnings, paid invoices
and the switch, client emails only when on (with the deal timeline link), extension /
pause / waiver approvals with maker-checker and their effect on the job, exception
visibility, receivables ageing, scope, follow-ups and accounts owner, hidden amounts, the
digest and the setup records.

### 14.4 What the owner switches on after deploying Phase 2

1. TBO Smart Settings → **Receivables and reminders (Phase 2)** (Phase 1 must be on).
2. Set **Invoicing company** (TBO KSA), **KSA Accounts lead**, **Head - Sales & Marketing**,
   **Finance Manager** and **Daily digest recipients**.
3. Give the Finance Manager the **TBO Finance Approver** role, and set each deal's accounts
   owner (Finance tab) as Accounts takes them on.
4. When management agrees the wording (Q14): edit the three Email Templates, set
   **Reminder sender**, then tick **Send reminder emails to clients**.

### 14.5 Phase 3: allocation and transfers (built)

Switch: **TBO Smart Settings → Allocation and transfers (Phase 3)** (`enable_fund_allocation`),
off by default, and needs Phase 1 on. While it is off no allocation is made, the transfer
APIs refuse, the pending-transfer job does nothing and the CRM hides the page. **Memo only
(Q1 option C):** nothing in Phase 3 posts to the GL.

**Doctypes** (all `track_changes`): **TBO Fund Allocation** (submittable, `TBO-ALC-.YYYY.-`)
with **TBO Fund Allocation Line**, and **TBO Fund Transfer** (submittable, `TBO-TRF-.YYYY.-`)
with **TBO Fund Transfer Allocation**, as in §3.3 and §3.4. Read for Accounts User / Manager,
TBO Finance Approver and TBO Management; Accounts create and submit transfers; only System
Manager has full rights. Allocations are made only by the engine: a typed or copied one is
refused, and so is cancelling one by hand.

**Allocation engine** - `tbo_smart/finance/allocation.py`, on Payment Entry submit and
cancel, and Journal Entry submit.

| Event | What happens |
| --- | --- |
| Receipt (Receive, Customer) of the invoicing company, on or after the start date | One Receipt allocation, submitted: a line per Sales Invoice / Sales Order reference and one for the unallocated amount, by the §4.1 formula, half-up rounding. The receipt that brings an invoice to zero takes the remainder of its net and share, counting earlier receipts on the invoice **and** the advances (against its orders) pulled into it through its advances table, so example A gives 15,000 + 17,500 + 17,500 and example B gives 4,347.83 + 12,318.84. |
| Unallocated money | Net ratio of the deal's latest submitted Sales Order, else its latest submitted Quotation, else 1 / (1 + default VAT). |
| Refund (Pay to Customer) | A negative Reversal allocation for its net (Q4). |
| Receipt cancelled, nothing transferred yet | Its allocation is cancelled. |
| Receipt cancelled after a transfer | A negative Reversal for the part of the share already transferred (India owes back exactly that; the unsettled rest stops being payable), the original marked Reversed, and a "Receipt cancelled after transfer" exception (category Allocation). The receipt's allocations never block its cancellation. |
| Customer receipt booked as a Journal Entry (customer credit + bank/cash debit) | No allocation; a "Receipt via Journal Entry" exception. |

**Transfers** - TBO Fund Transfer controller, `tbo_smart/finance/transfers.py`,
`tbo_smart/api/finance/allocations.py`.

- The sending leg is an ordinary Payment Entry (Pay) of the invoicing company: amount sent and
  sender charges are read from it (KSA bears the charges, Q7). India confirms by linking its
  receipt (Receive, partner company): amount received, receiver charges and the achieved rate
  (received ÷ sent, Q3) are read from it. One payment sends one transfer, and one receipt
  confirms one.
- Rows settle allocations: at most what is open on each, re-read with a row lock on submit so
  two transfers cannot over-settle one allocation. Negative rows net a reversal (due from
  India, Q4). `withholding_tax` counts as settled (Q8).
- **Deviation:** the plan capped the rows at the amount sent. A transfer may settle more or
  less than it sends; a difference above `amount_tolerance` raises a "Short transfer" or
  "Excess transfer" exception and the transfer shows **Difference** until the exception is
  decided. That is how the plan's own T11 variant (send 32,400 against 32,500) works.
- Status: Draft → Sent (submitted) → Received (India's receipt linked); Difference while an
  exception on it is open. Matched and Closed need verified bank lines (Phase 4).
- Daily: a transfer still Sent after `transfer_pending_days` raises a "Transfer pending"
  exception (once).
- System exceptions are deduplicated by `dedupe_key` (`tbo_smart/finance/exceptions.py`):
  one stays open per document and cause.

| Method | HTTP | Returns / does |
| --- | --- | --- |
| `get_allocation_summary()` | GET | Payable to the partner (share), settled, pending transfer, due from the partner (negative outstanding), recent allocations and transfers, and whether the user may transfer. |
| `get_transfer_form()` | GET | Open allocations oldest first (FIFO) and the invoicing company's submitted payments not yet used by a transfer. |
| `create_transfer(ksa_payment_entry, transfer_date, rows, reference_no, withholding_tax)` | POST | Creates and submits a transfer; TBO Fund Transfer permissions apply. |
| `confirm_receipt(transfer, india_payment_entry)` | POST | Links India's receipt. |
| `preview_allocations(from_date, to_date)` | GET | Q16 "backfill for reporting": what receipts before the start date would have allocated. Records and posts nothing. Uses the plain ratio for every receipt (no closing remainder), since an invoice paid in full today does not say which past receipt settled it. |

All of them, and the Finance tab's new Allocation section (`get_deal_allocation`: the deal's
share, settled and pending), are for Accounts, Finance and Management only (risk R7); a
salesperson gets a 403 and never sees the section. The same goes for exceptions of
category Allocation and Transfer: their permission hooks hide them from everyone else, even
on the salesperson's own deals. The daily digest adds the payable and
the transfers waiting for India.

**Not built (follow-ups):** accounting options A (monthly inter-company invoice) and B (journal
entry per receipt); the nightly "VAT ratio changed" check; re-pointing an allocation when
ERPNext's Payment Reconciliation moves an advance from an order to an invoice (the advances
table case is handled); company-based record rules beyond ERPNext's own User Permissions.

**Tests** - `tbo_smart/tests/test_finance_allocation.py`: examples A and B to the cent,
half-up rounding, receipts outside the allocation (start date, switch, other company),
unallocated money with the deal ratio, cancel before, after and after part of a transfer
(reversal, exception, due from partner), refunds, Journal Entry receipts, FIFO transfer with India's
confirmation and achieved rate, short transfer and over-settlement, a sending payment used once, the pending-transfer job,
engine-only allocations, access (BDE refused, Accounts allowed, Finance tab section,
allocation exceptions hidden), the reporting preview and settings validation.

### 14.6 What the owner switches on after deploying Phase 3

1. Check **Invoicing company** (TBO KSA) and set **Partner company** (TBO India).
2. Check **Allocation start date** (set to the deploy date) and **Partner share %** (50).
3. Tick **Allocation and transfers (Phase 3)**.
4. Accounts book each transfer as a Payment Entry (Pay) in ERPNext, then record it on the CRM's
   **Allocations** page; India confirms there with its receipt.

### 14.7 Phase 4: reconciliation and approvals (built)

Switch: **TBO Smart Settings → Reconciliation and approvals (Phase 4)**
(`enable_reconciliation`), off by default; needs Phase 1. While it is off the Bank
Transaction hooks and the nightly check do nothing and the bank-line APIs refuse.

**Bank Transaction fields** (created on migrate, allow on submit, read-only):
`tbo_verification` (Unverified / Verified), `tbo_matched_by`, `tbo_verified_by`,
`tbo_verified_on`, `tbo_dedupe_key` (hidden, indexed), `tbo_possible_duplicate_of`.

**Hooks** - `tbo_smart/finance/reconciliation.py`.

| Event | What happens |
| --- | --- |
| Line imported (`before_insert`) | Dedupe key = sha1(bank account, date, direction, amount, normalised reference or description). A non-cancelled line with the same key sets `tbo_possible_duplicate_of`; the line is **not blocked** and a "Possible duplicate" exception (action Mark duplicate) is raised. |
| Line matched or unmatched in the Bank Reconciliation Tool (`before_update_after_submit`) | Records who matched it (`tbo_matched_by`) and sets it back to Unverified. A **Verified** line refuses any change to its matches unless an **approved "Correct entry"** exception covers it (that also blocks the tool's Unreconcile on it). Each approval allows one change: the change stamps the exception's `used_on`, and an exception already used no longer counts. |
| After matching (`on_update_after_submit`) | Matched Payment Entries totalling more than the tolerance away from the bank amount raise an "Amount mismatch" exception (T13: 34,450 against 34,500). Matching a transfer leg fills the TBO Fund Transfer's bank line. |
| Nightly | A line still Pending or Unreconciled after `unmatched_alert_days` raises one "Unmatched bank line" exception. |

**Verification ("nothing reconciled until verified")** - `verify_bank_line` (POST): Accounts
Manager or TBO Finance Approver, on a Reconciled line, and never the person who matched it.
**Deviation:** the plan said to read the matcher from Version; the hook records it in
`tbo_matched_by` instead, which survives a later edit and is cheaper to check. Verifying
both legs of a transfer moves it to **Closed** (Matched while both legs are matched but not
yet both verified).

**Second approver (Q5)** - with `second_approval_amount` set, an exception whose largest of
difference / expected / actual is above it needs two approvals by two different approvers
(neither the requester): the first sets `first_approved_by` and keeps it Pending Approval.
Reopening a rejected exception clears the first approval, since the request can be edited
again; the earlier approval stays in the timeline and Version history.

**Stage and closure (§2 stage 15)** - with Phase 4 on, the computed stage continues past
Collected: **Settling** while India's share on the deal is still open (Phase 3), then
**Reconciled** once every receipt of the deal is on a verified bank line. **On Exception**
takes priority while an exception on the deal is Open or Pending Approval (an exception's save
recomputes its deal's stage). *Close finance* then needs the deal **Reconciled**, i.e.
collected, share transferred, receipts verified and no open exception; with Phase 4 off it
still needs only Collected.

**CRM** - the Finance exceptions page lists every category (category filter), is open once
any of Phases 2-4 is on, shows "first approval" progress, and gains a **Bank lines** tab:
matched lines waiting for verification (with Verify for a second person) and unmatched lines
with their age and duplicate flag (`get_bank_queue`, which reads through Bank Transaction
permissions, so User Permissions on company apply). Finance tab: the stage strip shows
Settling and Reconciled, and Close follows the closable stage.

**Tests** - `tbo_smart/tests/test_finance_reconciliation.py`: duplicates flagged not
blocked (and no key while off), match → verify by a second person → lock, amount mismatch,
unmatched ageing once, two approvers above the amount, the stage running to Reconciled and
closing, On Exception, and the bank queue refused to salespeople.

### 14.8 What the owner switches on after deploying Phase 4

1. Tick **Reconciliation and approvals (Phase 4)**; check **Days before an unmatched bank
   line is an exception** (3).
2. If management answers Q5 with a limit, set **Second approval above**.
3. Accounts keep importing statements and matching in ERPNext; a second Accounts Manager or
   TBO Finance Approver verifies on the CRM's Finance exceptions → Bank lines tab.

### 14.9 Phase 5: finance cards on the CRM dashboard (built)

Switch: **TBO Smart Settings → Finance dashboard (Phase 5)** (`enable_finance_dashboard`),
off by default; needs Phase 1. Module `tbo_smart/finance/dashboard.py`, registered with the
CRM fork's `crm_dashboard_providers` (charts resolvable by name) and `crm_dashboard_layout`
(`get_cards`: the cards for the current user, placed below the CRM's own) hooks.

| Card | Who sees it | Definition (§8) |
| --- | --- | --- |
| Quotations | CRM users | Submitted quotations of deals, by quotation date in the range; delta against the previous period of the same length |
| Invoiced | CRM users, with amounts | Σ base grand total of submitted invoices of CRM deals (not returns) posted in the range; delta |
| Collected | CRM users, with amounts | Σ customer receipts of CRM deals in the range minus refunds (company currency); delta |
| Outstanding | CRM users, with amounts | Σ outstanding on submitted invoices of CRM deals, today, in company currency |
| Overdue | CRM users, with amounts | Σ company-currency outstanding (`base_outstanding`) on payment terms due before today |
| Lead → deal | CRM users | Deals from a lead created in the range ÷ leads created in the range |
| Payable to partner | Accounts, finance, management; Phase 3 on | Σ open share on allocations, the Allocations page's "pending" (money due back is not netted in) |
| Transfers waiting | Same; Phase 3 on | Transfers Sent or Difference |
| Bank lines to verify | Same; Phase 4 on | Reconciled lines not Verified |
| Open exceptions | Same; any of Phases 2-4 on | Exceptions Open or Pending Approval |

- **A BDE sees only their own deals' sales figures:** the CRM dashboard passes a plain Sales
  User's own user, and every sales card then counts only deals they own. A manager can pick a
  user in the dashboard's filter; leaving it blank shows everyone.
- Amount cards follow "BDE can see invoice/payment amounts" (Q17), also when called
  directly through the CRM's `get_chart`. Every card is None while Phase 5 is off.
- Amounts are in the invoicing company's currency (that company only), or the CRM default
  company's when no invoicing company is set. The finance counts (transfers, bank lines,
  exceptions) are for that company too. Sales cards count only documents linked to a CRM
  deal, also in the everyone view (blank user).
- **Deviation:** the plan wanted client, employee and entity filters on every metric. The
  CRM dashboard only filters by date range and user, so the cards use those; the Receivables
  and BDE conversion pages carry the client and company filters. The Allocation, Transfer and
  Reconciliation reports of §8 are the Allocations page and the Bank lines tab; the separate
  Script Reports and the Audit Log report are not built (follow-ups).
- The fork also lets users the `crm_app_permission` hook admits (the finance roles) open the
  dashboard.

**Tests** - `tbo_smart/tests/test_finance_dashboard.py`: a BDE's cards count only their
deals (invoiced, collected, outstanding, overdue) and carry no finance cards; Accounts get
the finance cards; hidden amounts and the switch. The fork tests the hooks in
`crm/tests/test_dashboard_providers.py`.

### 14.10 What the owner switches on after deploying Phase 5

1. Tick **Finance dashboard (Phase 5)**. The cards appear on `/crm/dashboard` for each user by
   role; nothing needs to be added to the saved layout.

---

## Changelog

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-10-12 | First plan for management approval. Quotation approval delegated to the separate "Quotation discount/commission approval" feature; its designation roles used in §9. |
| 0.2 | 2026-10-12 | Phase 1 built (owner approved the start). §14.1 records what was built, decisions and deviations; §3.1, §3.2, §7 and §12 updated. |
| 0.3 | 2026-10-12 | Phase 2 built: receivables, reminders, escalation, exceptions (Receivable) and digest (§14.3); §3.2 and §12 updated with the defaults chosen. |
| 0.4 | 2026-10-12 | Phase 3 built: fund allocation per receipt and transfers, memo only (§14.5); §3.2 and §12 updated. |
| 0.5 | 2026-10-13 | Phase 4 built: bank-line duplicate, mismatch and ageing checks, maker-checker verification and lock, second approver, Settling / Reconciled / On Exception stages and closure (§14.7). |
| 0.6 | 2026-10-13 | Phase 5 built: finance cards on the CRM dashboard by role (§14.9). |
| 0.7 | 2026-10-16 | Stages 1 and 4 follow the lead pipeline (`lead-pipeline.md`): its statuses, and the deal made at Closed – Won. |
