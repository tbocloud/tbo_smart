# Lead departments (living spec, v1.3, Phase 1 to 3 and products)

Leads belong to a department (GrowthX and the like) that admins create. The department's
**heads** see every lead of it; the people who follow a lead (its owner, or anyone it is
assigned to) see theirs. Each lead is also one of two types: **ERP / Software** or
**Content / Digital Marketing**. Phase 1 is the data, the visibility rule and the hub API; the
hub's screens come after.

## 1. Data

- **TBO Lead Department** (`tbo_smart/tbo_smart/doctype/tbo_lead_department`, named by
  `department_name`, unique): `lead_type` (required), `is_active` (default on), `assignment` (Manual, the default, or Round robin), `stale_after_days` (Int; 0 or blank: use the caller's days), hidden `last_assigned_user`, `heads` and
  `members` (tables of **TBO Lead Department User**: `user`, `full_name`). System Manager and
  Sales Manager: full; Sales User: read. A person is listed once per table; no rename.
- **Lead** custom fields (`lead_pipeline/setup.py`, after `tbo_segment`, ERPNext sites only):
  `tbo_department` (Link, in list and standard filter) and `tbo_lead_type` (Select; fetched
  from the department's type only while empty, so it can be set to the other type).

## 2. Who sees a lead (`sales/permissions.py`, `sales/departments.py`)

Every existing rule stays (docs/erpnext-lead-cutover.md §3). On top of the owner/assignee/
hierarchy rule, **a head of an active department also sees every lead whose `tbo_department` is
one of theirs**, in the list (`permission_query_conditions`, one subquery, no per-row query) and
in `has_permission`. Members who are not heads get nothing extra. A lead without a department,
and a department switched off, behave as before. System Manager, Administrator and a Sales
Manager outside the hierarchy still see everything.

## 3. Validation (`sales.departments.validate_lead_department`, Lead validate)

- A lead may be put in a department only while it is active (checked when the department
  changes, so a lead stays editable after its department is switched off).
- When the department has any heads or members, `lead_owner` must be one of them (checked when
  the department or the owner changes).

## 4. Hub API (`tbo_smart.api.hub_leads`, same `acting_user` rules)

- `get_options`: adds `departments` (active only: `{name, lead_type, is_active, assignment, stale_after_days,
  is_head, is_viewer, is_member}` for the acting user) and `lead_types`.
- `list_leads(department=...)`: filters to the department; rows carry `tbo_department` and
  `tbo_lead_type`. `get_lead`'s `lead` carries the same two.
- `create_lead` / `update_lead`: `data` takes `department` (or `tbo_department`) and `lead_type`
  (or `tbo_lead_type`).
- `get_lead_departments` -> `[{name, department_name, lead_type, is_active, assignment, stale_after_days, heads, members}]`
  for any lead user (heads and members are `[{user, full_name}]`, inactive departments included).
- `save_lead_department(values, name=None)` (POST): `values` has `department_name` (new only),
  `lead_type`, `is_active`, `assignment`, `stale_after_days`, `heads`, `members` (lists of emails, replacing the old list).
  Returns the department. `set_lead_department_active(name, active)` (POST). Both need Sales
  Manager or System Manager (PermissionError otherwise).

Not in Phase 1: the overview, daily report and mobile scopes still follow the owner scope, not
the department.

## 4a. Phase 2: the type decides the documents

An ERP / Software lead (or one without a type) keeps the full chain: Proposal, Quotation,
Project Agreement, BRD, SRS, Implementation Plan, then the project. A **Content / Digital
Marketing** lead goes Lead -> a 6-month **strategy proposal** (the preset "Digital Marketing –
6-month strategy", used by default for a new proposal on such a lead) -> once the client accepts,
the project (the hub links it with `link_project` on the proposal and reads
`get_accepted_proposal`). The credentials handover and the content calendar approvals are the
hub's. Details and the data-driven table: `lead-pipeline.md` §13.

## 5. Round-robin assignment (`sales/assignment.py`)

A department with `assignment` = Round robin hands every new Lead that has a department and no
`lead_owner` to its next member: enabled users only, in the members list's order, wrapping round,
starting after `last_assigned_user` (a removed or disabled last user restarts the order). It runs
in Lead `before_insert` (the owner) and `after_insert` (the assignment), so it covers the website
form, Data Import and a hub `create_lead` with the owner left empty. Frappe fills an empty Lead
Owner with the inserting user before any hook runs, so the owner counts as empty when it is that
default: the inserting user, who is not one of the department's people (a hub create sets
`flags.round_robin` instead). The owner is set, the person gets a ToDo assignment through
`frappe.desk.form.assign_to._add` (the ERP's own assignment notice), and the department remembers
the user. The department's row is read `FOR UPDATE` first, so two leads inserted together get two
people. A Manual department, an inactive one, or one with no enabled member does nothing (a hub
create then keeps the acting user as owner). Heads are not in the rotation.

## 6. Duplicates (`sales/duplicates.py`)

`find_duplicate_leads` (hub API §2.9) and the check inside `create_lead`. Email (lowercase), phone
(last 10 digits of mobile, phone or WhatsApp) and company (case-insensitive, exact) are compared
with every lead, **ignoring who may see it**, and only name, owner, stage and contact fields come
back: this is a deliberate choice, since the purpose is that nobody works an enquiry a colleague
already has. Email or phone blocks `create_lead` (`DuplicateEntryError`; `allow_duplicate=1`
gets past an email match, never a number); a company match only shows. **Data Import**
(`frappe.flags.in_import`) refuses a row whose email or phone another lead has, so the importer
skips it and lists it with the same message; the CRM cutover (flag `tbo_cutover`) copies leads as
they were and is not checked.

**One lead per number** (`refuse_taken_numbers`, Lead validate). However a lead comes in (the
hub, the app, the desk, Data Import, the website form, the Lead Bank, prospecting, an
integration), a new lead, or a number new on a lead (its mobile, phone or WhatsApp field, or a
number row), is refused with `DuplicateEntryError` when another lead has that number: the last
10 digits, on any of that lead's numbers whatever their status (a Wrong number too: a manager
removes it from that lead if it really is the new business's). The message names the lead and
its owner by name only (`Possible duplicate of <lead> (<owner name>): it already has the number
<number>, and a number belongs to one lead.`), since whoever is told may not see that lead.
- **Duplicates made before the rule** stay: a lead keeps the numbers it had, so it can still be
  edited. Merge them (hub API §2.26): the merge brings the merged lead's numbers along.
- **Numbers many businesses share** (TBO Smart Settings → **One lead per number** → *Numbers
  many businesses share*, one per line, e.g. a lead portal's virtual number) may be on any number
  of leads, and `find_duplicates` ignores them (the app's own check still shows them as a
  duplicate it may create past).
- The website form, Meta lead ads (the meta_lead_integration app asks the hook
  `meta_lead_existing_lead`, `sales/meta_leads.py`; its log says Duplicate and links the lead)
  and the Lead Bank add to the lead that has the number or email, and prospecting skips the
  business, instead of making a new lead; the hub's lead form and Add number check first and
  say so. The app's `create_lead` refuses a number match even with `force`
  (`tbo-smart-api-ess-crm.md` §6.2).
- Only the last 10 digits are compared, as before: a UAE number typed once with +971 and once
  with a 0 is not seen as the same.

## 7. Stale leads (`sales/stale.py`)

`get_stale_leads` (hub API §2.10): open leads (not Closed – Won, Closed – Lost, On Hold) of the
acting user's normal scope whose newest activity is `days` old or more. Activity is the latest of
a note (Comment type Comment), an email (Communication), a ToDo (its last change), an Event
(calls and meetings, logged by hand or MyOperator), a TBO Call, a stage change and the lead's
creation; system "Info" comments do not count. One grouped query per source. A department's
`stale_after_days` above 0 replaces `days` for its leads.

## 8. Email logging by BCC (`sales/email_log.py`)

TBO Smart Settings → **Lead email log mailbox** (`lead_log_email_account`, an Email Account).
Every Communication received on that account is attached (`reference_doctype`/`reference_name` =
Lead, so it is on the timeline) to the lead whose `email_id` is any outside address on the mail's
sender, recipients, cc or bcc; outside means not an enabled or disabled system user's address and
not one of our Email Accounts. With several leads the most recently changed open one wins; with
none the Communication is left as it is. Setup: create a mailbox such as log@teambackoffice.com,
add it as an **incoming** Email Account in Frappe, select it in the setting, then BCC it on mails
to clients (a mail forwarded to it works too).

## 10. Products / brands under a department (v1.3)

**Why.** GrowthX has sub-teams that sell different things (Solar / PhoebiX, BillaX, WilpeX,
PrintagX, Foamia, Avera). A BD must say which name they are calling under, and each product's
leads are kept apart.

**Data.**
- **TBO Lead Product** (child table `products` on the department): `product_name` (required,
  unique in the department ignoring case), `is_active` (default on), `intro_line` (at most 140
  characters, e.g. "PhoebiX - solar ERP for installers") and an optional `website`.
- **Lead** custom field `tbo_product` (Data, after the lead type). Empty is allowed. A
  department with no products behaves exactly as before.
- **Department** `campaign_product` ("Calls for product", next to `campaign_industry`).
- **TBO Prospect Run** `product`.

**Rules** (`sales/products.py`, Lead validate `validate_lead_product`).
- A product must be an **active** product of the lead's department. Checked when the product or
  the department changes, so a lead stays editable after its product is switched off. Matching
  ignores case and stores the name as listed.
- Only a **System Manager, Sales Manager or a head of the lead's department** may set or change
  it, singly or in bulk. The owner sees the product but cannot change it. Trusted code
  (Find prospects, imports) saves with `ignore_permissions`.
- Renaming a product (a `previous_name` on its row when saving the department) moves its leads and
  the campaign product to the new name.
- Moving a lead to a department that lacks its product is refused until the product is cleared.
- `campaign_product` must be an active product of the department. Switch the campaign off (or
  change it) before switching that product off.

**Calling.** The call-batch campaign filter gains `product` (`CAMPAIGN_COLUMNS`): new leads in
a batch (priority 3 to 5, not follow-ups) are only of the campaign product. Every call in
`get_my_calls` carries `product`, `intro_line` and `calling_for` (the product, else the
department's name). Outreach drafts and the WhatsApp first message use the product's name and
intro; a lead with no product is written from TBO as before (decision: not the department's
name, because "GrowthX" means nothing to the client).

**API.** `docs/hub-leads-api.md` §2.20.

### How to use (staff)

*Managers (Sales Manager, System Manager)*
1. In the support hub open **Settings, CRM, Lead departments** and pick GrowthX.
2. Under **Products / brands** press **Add product**. Type the name (PhoebiX) and one short line
   to say on the call ("PhoebiX - solar ERP for installers"). Add BillaX, WilpeX, PrintagX,
   Foamia, Avera and Solar the same way. Press **Save**.
3. To call for one product tomorrow, set **Calls for product** to PhoebiX and save. The next
   batches give each BD only PhoebiX leads (follow-ups still come).
4. To tag leads: on the **Leads** list tick them, press **Set category**, choose **Product** and
   apply. Or open a lead and pick the **Product**.
5. To stop using a name, switch it **off**. Leads keep it; nobody can pick it any more.

*BDs*
- **Today's calls** shows **Calling for: PhoebiX** and the intro line on every call. A lead
  with no product shows the department name. Say that name when you introduce yourself.
- You can see a lead's product but only managers change it. Ask your manager.

## 9. Tests

`tbo_smart/tests/test_lead_wave1.py`: round-robin order, wrap, skipping disabled users and the
previous one, Manual leaving the owner empty, the hub create with the owner left empty;
duplicates (normalisation, matched_on, the cap of 5, `exclude`, all leads regardless of
visibility, the refusal, `allow_duplicate` never past a number, company-only allowed, Data Import
skip; a Meta enquiry from a known number on that lead); one lead per number in
`test_lead_numbers.py` (a desk insert in another format, adding or typing a taken number, an old
duplicate still editable, shared numbers); stale
leads (each activity source, closed and On Hold excluded, the department's own days, visibility);
BCC logging; the website form (honeypot, validation, duplicates, department fallback, round
robin, the rate limit decorator). Website form: `website-lead-form.md`.

`tbo_smart/tests/test_lead_departments.py`: a head sees the department's leads they do not
own, a member only their own, a lead without a department is unchanged, a department switched
off opens nothing, the lead type default and override, an inactive department is refused, the
owner must be one of the department's people, the options, the list filter, create through the
hub, and who may save departments.

`tbo_smart/tests/test_lead_products.py`: only managers and the department's head change a
product (the owner cannot, singly or in bulk), validation against the department's active
products, the list filter and counts, the options, saving products and the campaign product,
the batch campaign filter, calls carrying the product and intro line, outreach using the product,
and Find prospects giving it to new leads.

`tbo_smart/tests/test_client_documents.py` (`TestContentLeadChain`): a content lead gets the strategy preset by default, its chain after the accepted proposal is the project, an ERP lead's chain is unchanged, and `get_accepted_proposal` / `link_project` on the proposal.
