# From Frappe CRM to ERPNext's Lead and Opportunity (living spec, v1.1)

**Status:** built 2026-10-16. The owner decided that Frappe CRM is uninstalled from
india.teambackoffice.com (tboindia) and ERPNext's own **Lead** (and **Opportunity** for deals)
becomes the system of record. Leads are created from the TBO Support hub through
`tbo_smart.api.hub_leads` (`docs/hub-leads-api.md`). This file is the living spec of the
switch: what replaced each Frappe CRM piece, the cutover command, and the owner's runbook.

- **Sites:** tboindia (Frappe v15, ERPNext, HRMS, the CRM fork until the cutover, tbo_smart) and
  tbo-smart.tbocloud.in (no CRM and no lead data). tbo_smart no longer needs the `crm` app
  (`required_apps = ["erpnext", "hrms"]`) and imports nothing from it.
- **No ERPNext guard:** everything on Lead and Opportunity checks
  `tbo_smart.sales.erpnext_installed()` (the lesson of tbo_smart#14 with `crm_installed`), so a
  migrate on a site without ERPNext never touches a missing doctype.
- **Related specs:** `lead-pipeline.md`, `smart-leads.md`, `crm-finance-workflow.md`,
  `quotation-discount-control.md`, `tbo-smart-api-ess-crm.md` (the mobile contract, kept),
  `hub-leads-api.md`.

## 1. Decisions

| # | Decision | Why |
| --- | --- | --- |
| E1 | **Record names are kept.** A CRM Lead `CRM-LEAD-2026-00042` becomes the Lead `CRM-LEAD-2026-00042`, a CRM Deal `CRM-DEAL-2026-00007` the Opportunity `CRM-DEAL-2026-00007`. | Every link already pointing at them stays valid: quotations, meetings, approvals, finance documents, notifications and the phones' caches. ERPNext Lead uses the same `CRM-LEAD-.YYYY.-` series as Frappe CRM did, so new leads continue the same counter. A name already taken by another record falls back to a new name and the mapping is kept in `crm_lead_ref` / `crm_deal_ref`. |
| E2 | **The 13 pipeline stages live in `Lead.tbo_stage`** (Select). ERPNext's own `Lead.status` is left to ERPNext, which sets it itself (Lead, Opportunity, Quotation, Converted…). | `status` is a fixed Select that ERPNext's status updater overwrites. |
| E3 | Stage history is the child table **`tbo_stage_log`** (TBO Stage Change) on Lead and Opportunity. | Replaces CRM Status Change Log for the scores, the dashboards and the mobile stage history. |
| E4 | **Notes are Comments**, calls and logged meetings / emails / WhatsApp are **Events**, follow-ups and tasks are **ToDos**. | ERPNext's own activity records: the Lead and Opportunity forms list open ToDos and Events, and the timeline shows comments. |
| E5 | A **deal is an Opportunity**; its stage is ERPNext's `sales_stage` (Sales Stage), typed with the custom `tbo_stage_type` (Open, Ongoing, On Hold, Won, Lost), `tbo_probability` and `tbo_position`. | Sales Stage is ERPNext's deal-stage master; the type is what the code needs (won, lost, open). |
| E6 | **Organizations are Prospects**, and an organization's client is a **Customer** (`Customer.prospect_name`). | ERPNext's own pre-sale organization and its link to the customer. |
| E7 | The sales hierarchy is the new tree **TBO Sales Hierarchy** (user → reports to), switched by TBO Smart Settings → **Sales hierarchy** (`enable_sales_hierarchy`), with the same visibility rules as Frappe CRM had. | ERPNext's Sales Person tree hangs off Employee, not User, and does not limit what people see. |
| E8 | Quotations of a lead are made out to the **Lead** (`quotation_to = "Lead"`), the deal's to its Customer or Lead, and carry ERPNext's own **`opportunity`** link. | Native Quotation parties; ERPNext creates the Customer from the Lead when the Sales Order is made. |
| E9 | The CRM UI's hooks (app access, the overrides of CRM's Convert and Create Quotation) are removed with the CRM. The APIs behind the CRM's TBO screens stay, ported, for the hub and the app; the CRM dashboard's TBO cards are `tbo_smart.api.dashboard.get_cards`. | They had no caller without the CRM app. |
| E10 | The deal link on Sales Order, Sales Invoice, Payment Entry and tbo_smart's own records is a field named **`opportunity`**, like Quotation's own. | ERPNext's mappers copy a same-named field from the quotation to the order and the invoice, as they copied `crm_deal`. |
| E11 | A deal and a quotation keep **the lead they were made from in `tbo_lead`** (set on save while made out to the lead; backfilled on migrate). Every "the lead's deal / quotations" lookup uses it, never the party. | ERPNext v15.123+ moves a lead's Opportunities and Quotations to the Customer it makes from the lead (`Customer.update_lead_status`), so after the first Sales Order the party no longer names the lead. |

## 2. Inventory: every Frappe CRM dependency and its replacement

### 2.1 Doctypes and fields

| Frappe CRM | ERPNext / tbo_smart |
| --- | --- |
| CRM Lead | **Lead** (same name, E1) |
| `status` (CRM Lead Status) | `tbo_stage` (Select, 13 stages, `lead_pipeline/statuses.py`) |
| CRM Lead Status (type, colour, position) | The `STATUSES` table in `lead_pipeline/statuses.py` (no doctype); `get_options` returns it |
| CRM Status Change Log | `tbo_stage_log` (TBO Stage Change: `stage`, `previous_stage`, `changed_on`, `changed_by`) |
| `organization`, `email`, `mobile_no`, `phone`, `job_title`, `website`, `territory`, `industry`, `source`, `lead_owner`, `no_of_employees`, `annual_revenue` | `company_name`, `email_id`, `mobile_no`, `phone`, `job_title`, `website`, `territory` (Territory), `industry` (Industry Type), `source` (Lead Source), `lead_owner`, `no_of_employees`, `annual_revenue` |
| `lead_score`, `lead_temperature` | `tbo_score`, `tbo_temperature` (custom) |
| `lost_reason` (CRM Lost Reason), `lost_notes` | `tbo_lost_reason` (Opportunity Lost Reason), `tbo_lost_notes` (custom) |
| `converted` | The lead has an Opportunity (`opportunity_from = "Lead"`) |
| `tbo_score_reason`, `tbo_scored_at`, `tbo_ai_*` | The same custom fields on Lead |
| `tbo_pre_pipeline_status` | Dropped with the CRM status migration (it ran on 2026-10-16; the cutover copies the pipeline stages) |
| — | `tbo_segment` (Select Retail, Small, Medium, Large, Premium) for the segment price lists of Phase 2 |
| — | `crm_lead_ref` (Data, hidden): the CRM Lead it was copied from |
| CRM Deal | **Opportunity** (same name, E1); `crm_deal_ref` (hidden) |
| `status` (CRM Deal Status) | `sales_stage` (Sales Stage + `tbo_stage_type`, `tbo_probability`, `tbo_position`); a Lost-type stage also sets `status = "Lost"` |
| `deal_owner`, `deal_value`, `exchange_rate`, `currency`, `probability`, `expected_closure_date` | `opportunity_owner`, `opportunity_amount`, `conversion_rate`, `currency`, `probability`, `expected_closing`; `base_opportunity_amount` (the value in the company currency) is set on every save by `sales.opportunity`, since ERPNext fills it only in the browser |
| `closed_date`, `next_step` | `tbo_closed_on`, `tbo_next_step` (custom) |
| `lost_reason`, `lost_notes` | `lost_reasons` (Opportunity Lost Reason Detail), `order_lost_reason` |
| `lead`, `organization`, `lead_name`, `email`, `mobile_no`, `phone` | `opportunity_from` / `party_name` (the Lead, else the Customer or Prospect), `customer_name`, `contact_display`, `contact_email`, `contact_mobile`, `phone` |
| `erpnext_customer` | `tbo_customer` (custom Link Customer) |
| `tbo_company` | `company` (ERPNext's own) |
| `tbo_stage`, `tbo_onboarding_complete`, `tbo_closed`, `tbo_accounts_owner` | `tbo_finance_stage` (renamed: ERPNext copies same-named Lead fields into a new Opportunity, and Lead has `tbo_stage`), `tbo_onboarding_complete`, `tbo_closed`, `tbo_accounts_owner` |
| CRM Contacts (deal contacts) | `contact_person` (the primary Contact) |
| CRM Products (deal products) | Opportunity Item rows (the product's ERPNext item) |
| CRM Deal Status | Sales Stage + the custom type fields (E5) |
| CRM Organization | **Prospect** (same name); its client a Customer with `prospect_name` |
| FCRM Note | **Comment** (`comment_type = "Comment"`) on the Lead or Opportunity; the migration note keeps its title as its first line |
| CRM Call Log | **Event** (`event_category = "Call"`, Completed), the lead or deal as participant, the note as description |
| CRM Task | **ToDo** (`reference_type/name`, `allocated_to`, `date`, `priority`, `description`); Backlog / Todo / In Progress → Open, Done → Closed, Canceled → Cancelled. The custom `tbo_task_type`, `tbo_escalation_level`, `tbo_sales_invoice`, `tbo_promised_date`, `tbo_promised_amount` move to ToDo |
| CRM Lost Reason | Opportunity Lost Reason |
| CRM Lead Source | Lead Source |
| CRM Sales Hierarchy, FCRM Settings → `enable_sales_hierarchy` | TBO Sales Hierarchy, TBO Smart Settings → `enable_sales_hierarchy` (E7) |
| FCRM Settings → `currency` | The default company's currency |
| CRM View Settings ("Collections" view) | Dropped (CRM screen) |
| Quotation `quotation_to` CRM Lead / CRM Deal, `crm_deal` (Data), `tbo_crm_lead` | `quotation_to` Lead / Customer, ERPNext's `opportunity` (E8) |
| Sales Order, Sales Invoice, Payment Entry `crm_deal` (Link CRM Deal) | `opportunity` (Link Opportunity, custom; E10) |
| TBO Finance Exception, TBO Fund Allocation, TBO Fund Allocation Line `crm_deal` | `opportunity` (renamed by the patch `rename_crm_deal_fields`; the values stay valid, E1) |
| TBO Quotation Adjustment `deal` (CRM Deal) | `deal` (Opportunity) |
| TBO Lead Meeting `lead` (CRM Lead), `follow_up_task` (CRM Task) | `lead` (Lead), `follow_up_task` (ToDo) |
| TBO Lead Triage Row `lead`, `task` | Lead, ToDo |

### 2.2 Code

| Frappe CRM | Replacement |
| --- | --- |
| `crm.permissions.org_hierarchy` (Lead and Deal visibility) | `tbo_smart.sales.permissions`: `has_permission` and `permission_query_conditions` for Lead and Opportunity, the same rules (§3) |
| The CRM's own tree of users (`CRM Sales Hierarchy`) | `tbo_smart.sales.hierarchy` (`get_sales_team`, `managers_of`), used by the scopes, the escalations and the reminders |
| `crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal` | `tbo_smart.sales.opportunity.make_opportunity` (ERPNext's own `lead.make_opportunity` mapper) |
| `crm.api.activities.get_linked_notes` / `get_linked_tasks`, `crm.api.comment.add_comment` | `tbo_smart.sales.activities` |
| `crm.api.dashboard.TimestampDiff` | `frappe.query_builder.CustomFunction("TIMESTAMPDIFF")` in `sales/activities.py` |
| ERPNext CRM Settings helpers (`get_local_customer`, `get_primary_contact`, `get_organization_address`, `prefill_quotation_items`, `check_customer_for_deal`, `can_create_quotations`) | The Opportunity's `tbo_customer` / party, `contact_person`, `customer_address`, items; ERPNext's Quotation → Sales Order makes the Customer from the Lead |
| Hooks `crm_app_permission`, `crm_unrestricted_access`, the two `override_whitelisted_methods` | Removed (CRM UI). Finance roles read every Opportunity through `sales.permissions` while "Let finance roles read deals" is on |
| Hooks `crm_dashboard_providers`, `crm_dashboard_layout` | `tbo_smart.api.dashboard.get_cards(from_date, to_date, user)`: the finance, smart lead and pipeline cards, each module's `get_cards` as before |
| ERPNext CRM Settings → ERPNext company (the deals' default company) | TBO Smart Settings → **Default sales company** (`sales_company`; the cutover copies it), else the site's default company |
| `doc_events` on CRM Lead, CRM Deal, FCRM Note, CRM Call Log, CRM Task | The same handlers on Lead, Opportunity, Comment, Event, ToDo |
| `tbo_smart.finance.crm_installed` | `tbo_smart.sales.erpnext_installed` |
| Patches `migrate_lead_pipeline_statuses`, `backfill_finance_deal_links` | Removed: one-offs on CRM records, both already run on tboindia |
| Links `/crm/leads/<lead>`, `/crm/call-today` in Teams and email | TBO Smart Settings → **Lead page URL** (`lead_page_url`, the hub's lead page) + `/<lead>` and `?view=call_today`; blank: the desk `/app/lead/<lead>` |
| Push routes for CRM Lead / Deal / Organization | Lead → `/crm/lead/<name>`, Opportunity → `/crm/deal/<name>`, Prospect → `/crm/org/<name>` (the app's screens are unchanged) |

## 3. Who sees which lead (`tbo_smart.sales.permissions`)

Frappe CRM's rule, unchanged, now on Lead and Opportunity:

- Administrator, System Manager: everything.
- A finance role (TBO Smart Settings → finance roles, while "Let finance roles read deals" is
  on): read every Opportunity.
- With the sales hierarchy on and the user in it: leads (deals) owned by or assigned (a ToDo,
  open or closed but not cancelled) to the user or anyone below them.
- A Sales Manager outside the hierarchy: everything.
- Everyone else: what they own (`lead_owner`, `opportunity_owner`) or what is assigned to them.

ERPNext's own role permissions still apply first (Lead: Sales User, Sales Manager; read for
Desk User, which this rule narrows to their own).

## 4. The cutover command (`tbo_smart.cutover.crm_to_erpnext`)

Not a patch: the owner runs it by hand, once, while the CRM app is still installed.

```
bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.run --kwargs "{'dry_run': True}"
bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.run --kwargs "{'dry_run': False}"
bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.verify
bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.finalize
```

- **Reads** the CRM tables by their raw names (`tabCRM Lead`, …) through `frappe.qb`, never the
  CRM's Python, so it runs while Frappe CRM is installed and never imports it.
- **dry_run** (the default) prints the counts (in the CRM, already copied) and the problems it
  sees (duplicate emails, lead names already taken by another Lead, CRM statuses outside the
  pipeline, deals with neither a lead nor an organization, deal products without an ERPNext
  item) and writes nothing.
- **Idempotent and resumable:** each copy stores its source in `crm_lead_ref` / `crm_deal_ref`
  (Lead, Opportunity); the other copies keep their source's name (Prospect, TBO Sales
  Hierarchy, Comment from FCRM Note, Event from CRM Call Log, ToDo from CRM Task), so a record
  already copied is found and skipped, and links to a CRM Task (a meeting's follow-up, a triage
  row's task) stay valid. It commits every `batch_size` (200) records, so a run cut off halfway
  continues where it stopped. A record that fails is undone, listed in the report, and the run
  goes on.
- **Notifications muted** during the run (`frappe.flags.mute_emails`, `in_import`, no
  assignment mails), as the 2026-10-14 migration did, and restored after.
- **Order:** masters (Lead Source, Opportunity Lost Reason with "Other", Territory, Industry
  Type, Sales Stage with its type, probability and position from CRM Deal Status; FCRM Settings'
  sales hierarchy switch and ERPNext CRM Settings' company into TBO Smart Settings), TBO Sales
  Hierarchy, CRM Organization → Prospect, CRM Lead → Lead (stage, score and AI fields, lost
  reason, owner, `_assign`, creation and modified times, the status history as the stage log),
  CRM Deal → Opportunity (its lead as party, else its Customer, else its organization; stage,
  value, owner, customer, primary contact, products, finance fields, lost reason, won day, stage
  log), then notes → Comments (a call's note goes into the call instead), calls → Events, tasks
  → ToDos (an open one assigns the lead or deal to its person, as ERPNext does), then the links:
  Quotations (`quotation_to`, `party_name`, `opportunity`), Sales Order / Sales Invoice /
  Payment Entry `crm_deal` → `opportunity`, and every Comment, ToDo (assignment), File,
  Communication, Communication Link, Notification Log and Event Participant on a CRM record
  moved to its copy; a deal's Customer gets its organization as `prospect_name`. A CRM status
  that is not a pipeline stage maps by its type (Won → Closed – Won, Lost → Closed – Lost,
  else the first stage of its type); a converted lead is Closed – Won.
- **Report:** a JSON report under `sites/<site>/private/files/crm_to_erpnext/` (counts per
  kind, the problems, the name mapping when a name changed, every record that failed with its
  error), and the same summary printed.
- **verify()** compares the counts (CRM vs copied), checks 25 random leads and deals field by
  field (name, stage, owner, organization, mobile, score; stage, owner and next step) with their
  open tasks, and that nothing still points at a CRM record that has a copy. It prints PASS or
  the differences. Links to CRM records that no longer existed (broken before the cutover) are
  listed separately and don't fail it.
- **finalize()** (refuses until verify passes, unless `force=1`) deletes tbo_smart's own custom
  fields that linked to CRM doctypes (Sales Order / Sales Invoice / Payment Entry `crm_deal`,
  Quotation `tbo_crm_lead`; those on the CRM's own doctypes go with the app), puts Quotation To back to ERPNext's
  own parties (Customer, Lead, Prospect), and lists any other custom field that names a CRM
  doctype. Frappe CRM's own Data fields (`Quotation.crm_deal`, `Customer.crm_deal`) stay as
  history.

## 5. The owner's runbook

1. **Press backup** of tboindia (database and files), and note the time.
2. **Deploy** this tbo_smart (and keep the CRM app installed for now) at a quiet time, with
   `bench migrate`. From this deploy on, the TBO screens inside the CRM read ERPNext leads,
   so tell the team the CRM is read-only until step 6.
3. **Dry run:** `bench --site india.teambackoffice.com execute
   tbo_smart.cutover.crm_to_erpnext.run --kwargs "{'dry_run': True}"`. Read the counts and
   the problems (duplicate emails are allowed on purpose, see §6).
4. **Run:** the same with `'dry_run': False`. Rerun it if it stops; it continues.
5. **Verify:** `bench --site india.teambackoffice.com execute
   tbo_smart.cutover.crm_to_erpnext.verify` must print PASS. Open a few leads in the desk
   (`/app/lead`) and the hub's lead page.
6. **Finalize:** `bench --site india.teambackoffice.com execute
   tbo_smart.cutover.crm_to_erpnext.finalize`.
7. **Uninstall the CRM app through Press** (Site → Apps → crm → Uninstall), then remove it from
   the bench / release group.
8. **Archive the fork** tbocloud/crm on GitHub (Settings → Archive), after the release group
   no longer lists it.
9. In TBO Smart Settings set **Lead page URL** to the hub's lead page, and give the hub's
   integration user the **Hub Integration** role (`docs/hub-leads-api.md`).

Undo before step 7: restore the Press backup of step 1.

**On Press (no bench shell):** the System Console runs scripts sandboxed, so `import` fails
there. Steps 3–6 have System Manager-only methods in `tbo_smart.api.cutover`, called from the
System Console (Python) one at a time:

| Step | System Console |
| --- | --- |
| 3. Dry run | `print(frappe.call("tbo_smart.api.cutover.dry_run"))` |
| 4. Run (queued on the long queue, one at a time) | `print(frappe.call("tbo_smart.api.cutover.start"))` |
| 4. Progress and report | `print(frappe.call("tbo_smart.api.cutover.status"))` (`running`; `failed` with the error if the run broke; `last_report` from the last completed run) |
| 5. Verify | `print(frappe.call("tbo_smart.api.cutover.verify"))` (`pass` must be `True`) |
| 6. Finalize | `print(frappe.call("tbo_smart.api.cutover.finalize"))` |

## 6. Known differences

- ERPNext refuses two leads with the same email unless CRM Settings → **Allow Lead Duplication
  based on Emails** is on. The CRM allowed it, so the cutover switches it on when the CRM has
  duplicates (and says so); the mobile `create_lead` keeps its own duplicate check.
- ERPNext's CRM Settings → **Auto Creation of Contact** makes a Contact for every new lead; the
  cutover turns it off while it runs and restores it after, so 1,500 contacts aren't made at
  once.
- The CRM's TBO screens (Call today page, triage, settings pages, quotation builder, Finance
  tab, pipeline page) go with the CRM app. Their APIs remain for the hub; the settings are
  edited in the desk (TBO Smart Settings) until the hub has them.
- A follow-up's time of day is gone: ToDo has a date only.
- The app's duplicate check leaves out the Contacts ERPNext makes for leads: the lead is the
  match, and its Contact would show a hidden lead's person to someone who may not open it.
- Between the deploy (step 2) and the run (step 4) the meetings, quotations and finance records
  already point at the kept names, which exist only once the run has copied them; saving one of
  those records in that window fails its link check. Keep the window short.

## 7. Settings (TBO Smart Settings → Sales: leads and deals)

| Field | Purpose |
| --- | --- |
| `enable_sales_hierarchy` | §3 (from FCRM Settings at the cutover) |
| `sales_company` | The default company of new deals and quotations (from ERPNext CRM Settings at the cutover) |
| `lead_page_url` | Where links to a lead open (the hub's lead page); blank: the desk |
| `hub_integration_role` | The role that may act for a hub user (`hub-leads-api.md`; blank: Hub Integration) |

## 8. Tests

`tests/test_sales.py` (visibility by owner, assignment, hierarchy and Sales Manager; the stage
log and lost reason; a deal's stage type, won day, value and customer; the activity records;
the lead links), `tests/test_hub_leads.py`, and `tests/test_cutover.py`: a CRM data set with the
shapes tboindia has, a dry run that writes nothing, the copy (names, stage history, notes,
calls, tasks, the deal, quotations, assignments, hierarchy), a second run that copies nothing,
verify and finalize. The cutover tests need the crm app and are skipped without it; CI runs
them in a second job with the crm app installed. Every other test runs on a site without
Frappe CRM.

## Changelog

- **v1.0 (2026-10-16):** the inventory, the port to Lead / Opportunity, the hub API and the
  cutover command.
- **v1.1 (2026-10-16):** `tbo_lead` on Opportunity and Quotation (E11), for ERPNext v15.123's
  move of a lead's records to its Customer.
