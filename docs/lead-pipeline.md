# Lead pipeline: statuses, meetings, MOM and quotations on the lead (living spec, v1.1)

**Status:** Phases 1 and 2 built 2026-10-16: the pipeline statuses and their migration, meetings
and minutes of meeting (MOM), automatic lead statuses, the deal at Closed – Won, and quotations
on the lead (presets, builder, status tracking, revisions, the TBO PDF). Everything but
the status migration is behind a switch that is off by default. This file is the living spec:
it describes what is built, and every change to the feature updates it in the same pull
request. The CRM screens are in tbocloud/crm, `docs/tbo-lead-pipeline.md`.

- **Site:** india.teambackoffice.com (tboindia): Frappe v15, ERPNext, HRMS, the Frappe CRM fork
  and tbo_smart. tbo-smart.tbocloud.in has no CRM: everything here reads as off there
  (`tbo_smart.finance.crm_installed`).
- **Related specs:** `smart-leads.md` (scores, Call today, follow-ups, AI notes; it reads the
  statuses defined here), `crm-finance-workflow.md` (the deal's Finance tab, quotations,
  Sales Order and invoices), `quotation-discount-control.md` (the BDE → CEO approvals),
  `tbo-smart-api-ess-crm.md` (the mobile contract, v1.8).

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Statuses, migration, meetings and MOM, automatic statuses, deal at Closed – Won, mobile | Built (this file) |
| 2 | Quotations on the lead: presets, builder, status tracking, revisions, PDF (§9) | Built |
| 3 | Pipeline dashboard | Not built yet |

## 1. Owner's decisions (2026-10-15)

| # | Decision | Effect |
| --- | --- | --- |
| D1 | Everything happens on the CRM Lead. A CRM Deal is created automatically only at Closed – Won. | Meetings and MOM hang off the lead. Closed – Won runs Frappe CRM's own `convert_to_deal`; the deal-based finance workflow carries on from there. While the pipeline is on, nobody converts a lead by hand (§6). |
| D2 | Meeting links are pasted by hand (Google Meet or Teams). No calendar integration. | `meeting_link` is a plain https URL. |

## 2. Statuses

### 2.1 The statuses (`lead_pipeline/statuses.py`)

Ordinary **CRM Lead Status** records, so CRM's status menu, kanban, filters and **CRM Status
Change Log** (the status history) work with them unchanged. `ensure_statuses` creates the
missing ones on install and every migrate and never edits existing ones, so an admin may
recolour or reorder them in CRM Settings. The code names them in this one module.

| # | Status | CRM type | Open for follow-ups and Call today | Scoring points |
| --- | --- | --- | --- | --- |
| 1 | New Lead | Open | yes | 10 |
| 2 | Contacted | Ongoing | yes | 18 |
| 3 | Meeting Scheduled | Ongoing | yes | 24 |
| 4 | First Meeting Completed | Ongoing | yes | 26 |
| 5 | Second Meeting Completed | Ongoing | yes | 28 |
| 6 | Third Meeting Completed | Ongoing | yes | 30 |
| 7 | Proposal Sent | Ongoing | yes | 32 |
| 8 | Negotiation | Ongoing | yes | 34 |
| 9 | Positive / Interested | Ongoing | yes | 22 |
| 10 | Follow-up Required | Ongoing | yes | 16 |
| 11 | On Hold | On Hold | yes | 4 |
| 12 | Closed – Won | Won | no | 0 (closed) |
| 13 | Closed – Lost | Lost | no | 0 (closed) |

- New Lead is the only Open-type status, so Frappe CRM gives it to every new lead.
- **Lost reason:** Closed – Lost is Lost-type, so Frappe CRM's own validation requires
  `lost_reason` (and `lost_notes` when the reason is "Other"), and the lead page asks for it.
  The mobile `set_lead_status` and the MOM's lead status take a `lost_reason`: a CRM Lost
  Reason is used as it is, free text becomes "Other" with the text in the notes (the same rule
  as `move_deal_stage`, one helper `set_lost_reason`).
- `OPEN_STATUSES` (everything but Closed – Won and Closed – Lost) is the single list smart leads
  use for scoring, Call today, the follow-up engine and the triage.

### 2.2 Automatic moves (`lead_pipeline/transitions.py`)

| Event | Lead moves to |
| --- | --- |
| A meeting is scheduled | Meeting Scheduled |
| The MOM of the 1st / 2nd / 3rd meeting is submitted | First / Second / Third Meeting Completed |
| A quotation is sent / under negotiation / accepted (§9.3) | Proposal Sent / Negotiation / Closed – Won |

- An automatic move only goes **forward** along `PROGRESS` and never touches a closed lead.
  Contacted, Positive / Interested, Follow-up Required and On Hold rank together before the
  first meeting. So a status someone set by hand (say Negotiation) is never pulled back by a
  later meeting; Additional meetings (4th and later) move nothing.
- **Manual changes** are always allowed, from the lead page, the MOM dialog or the app.
- The status is read under a row lock held until the save, so a status someone sets at the
  same moment is never overwritten by an older stage.
- Each move is saved on the lead as a system change (`ignore_permissions`, since the person
  submitting a MOM may not own the lead), so the Status Change Log and Version keep it, plus an
  Info comment on the timeline: "Status set to First Meeting Completed: MOM of the 1st meeting
  submitted".

## 3. The status migration (deploy, whatever the switches say)

Patch `tbo_smart.patches.v1.migrate_lead_pipeline_statuses` → `lead_pipeline/status_migration.py`.
It creates the statuses, puts them in order, then maps every lead that still has an old status
and has not been mapped before:

| Old status | New status |
| --- | --- |
| New | New Lead |
| Contacted | Contacted; **First Meeting Completed** when the migration note's original status starts with "Meeting" or "Demo" |
| Nurture | Positive / Interested |
| Qualified | **Proposal Sent** when the original status was "Quotation", else Positive / Interested |
| Unqualified, Junk | Closed – Lost; the lead's lost reason and notes are kept, and an empty one becomes "Other" with "Junk before the pipeline statuses" |
| Converted, and any lead with `converted` = 1 | Closed – Won. **No deal is created** for these (the patch writes with `frappe.db.set_value`, so no hook runs) |

- The original status comes from the lead's migration note through the smart leads parser
  (`smart_leads/migration.py`, labels "Status" / "Lead Status" / "Original Status").
- **Expected counts** for the 1,572 leads migrated on 2026-10-14 (New 848, Junk 371, Nurture 125,
  Qualified 85, Contacted 83, Unqualified 35, Converted 25):

  | Mapping | Leads |
  | --- | --- |
  | New → New Lead | 848 |
  | Junk + Unqualified → Closed – Lost | 406 |
  | Nurture → Positive / Interested | 125 |
  | Qualified → Proposal Sent (original Quotation) / Positive / Interested | 85 in all; the split depends on each note's original status |
  | Contacted → Contacted / First Meeting Completed (original Meeting… / Demo…) | 83 in all; split likewise |
  | Converted → Closed – Won | 25 |

  The split of the two mixed rows needs the live notes, which this build had no access to. The
  patch prints the exact count per mapping in the migrate log ("Lead pipeline: 61 leads
  Qualified → Positive / Interested").
- **Idempotent:** every mapped lead keeps its old status in `tbo_pre_pipeline_status` (hidden),
  and a lead that has it is never mapped again, even when someone later sets it back by hand.
- **Retiring the old statuses:** CRM Lead Status has no "disabled" flag. Leaving New, Nurture,
  Qualified, Unqualified, Junk and Converted would keep them in every status menu and kanban,
  make Frappe CRM give new leads "New", and make its conversion set "Qualified". So each is
  deleted once no lead uses it, and its definition (type, colour, position) is kept as JSON in
  TBO Smart Settings → `pipeline_retired_statuses` (hidden). Contacted keeps its name.
- **Undo:** `bench --site <site> execute tbo_smart.lead_pipeline.status_migration.revert`
  recreates the retired statuses, puts each mapped lead back on its old status **while it still
  has the status the migration gave it**, and deletes the pipeline statuses no lead uses.
  Leads that moved on since (say, Closed – Won with a deal) are left alone and returned in
  `kept` for a person to settle. Lost reasons it added stay. Deploy a tbo_smart without the
  pipeline afterwards, or the next migrate creates the pipeline statuses again.
- **New sites:** install creates the pipeline statuses next to Frappe CRM's defaults (the setup
  wizard's CRM demo data still uses the defaults). Run the same `execute
  tbo_smart.lead_pipeline.status_migration.execute` to retire them. Test sites do this in
  `before_tests`, after the wizard.
- Saved CRM views or kanban filters that name an old status are not rewritten.

## 4. Data and settings

### 4.1 Fields (`lead_pipeline/setup.py`, CRM sites only)

| Doctype | Field | Purpose |
| --- | --- | --- |
| CRM Lead | `tbo_pre_pipeline_status` (Data, hidden) | The status before the migration (§3) |
| TBO Smart Settings | `enable_lead_pipeline` | The switch (§8) |
| TBO Smart Settings | `meeting_reminder_minutes` (60, 15-1,440) | The reminder before a meeting |
| TBO Smart Settings | `pipeline_retired_statuses` (hidden) | §3 |
| TBO Smart Settings | `enable_lead_quotations`, `quotation_validity_days` (30), `quotation_follow_up_days` (3), `quotation_terms`, `quotation_payment_terms`, `quotation_print_format` (blank: TBO Quotation), `quotation_preset_roles` (Sales Manager, System Manager) | §9 |
| Quotation | `tbo_crm_lead`, `tbo_lead_meeting`, `tbo_package`, `tbo_quote_status`, `tbo_quote_family`, `tbo_revision_no`, `tbo_is_latest`, `tbo_sent_via`, `tbo_sent_on` | §9.1 |

### 4.2 TBO Lead Meeting (`TBO-MTG-YYYY-#####`, track changes)

| Section | Fields |
| --- | --- |
| Meeting | `lead`, `lead_name`, `stage` (number), `stage_label` ("1st meeting", "2nd meeting", "3rd meeting", then "Additional meeting N"), `status`, `bde` (default: the lead owner) |
| Schedule | `meeting_type` (Online / Physical visit), `meeting_link` (https, Online only), `location` (Physical visit only), `scheduled_at`, `duration_minutes` (60; 5-720), `participants` |
| MOM | `agenda`, `discussion_points`, `client_requirements`, `decisions`, `client_interest` (Positive / Neutral / Negative), `objections`, `quotation_feedback` |
| Next steps | `action_items` (table **TBO Lead Meeting Action**: `item`, `responsible`, `due_date`, `done`), `next_follow_up_on`, `next_meeting_details`, `prepared_by`, `submitted_on`, `follow_up_task` (CRM Task), `next_meeting` |
| Tracking | `mom_pending_since`, `escalation_level` (0 BDE, 1 BDM, 2 sales head), `reminded_at` |

Attachments are ordinary Frappe files attached to the meeting.

**Permissions** (`has_permission`, `permission_query_conditions` hooks): a meeting is open to
whoever may read its lead under Frappe CRM's own rules (owner, assignee, sales hierarchy,
Sales Manager); changing it needs write on the lead. List queries are limited to the leads
`frappe.get_list("CRM Lead")` returns for the user. **Only a System Manager deletes a
meeting**: previous meetings are never lost; one that doesn't happen is Cancelled.

## 5. Meetings and MOM (`lead_pipeline/meetings.py`, `lead_pipeline/mom.py`)

### 5.1 Numbering

A meeting takes its number when it is scheduled: the lead's meetings that aren't Cancelled,
plus one, counted under a row lock on the lead so two at once can't take the same number. A
cancelled 2nd meeting lets the next one be the 2nd again. A Closed – Lost lead can't get a new
meeting.

### 5.2 Statuses and the clock

```
Scheduled ──(its time)──▶ Meeting in Progress ──(its end)──▶ MOM Pending ──(MOM submitted)──▶ MOM Submitted
     │                                                          ▲
     ├─ Rescheduled (new time: reminder and MOM clock restart) ─┘
     ├─ Completed (by hand; still MOM Pending once it ended)
     ├─ Follow-up Required (by hand)
     └─ Cancelled (by hand)
```

- **Every 15 minutes** (`run_meeting_clock`, cron `*/15 * * * *`, switch on): Scheduled /
  Rescheduled meetings whose time has come turn **Meeting in Progress**; any meeting without
  minutes (Scheduled, Rescheduled, In Progress, Completed) whose end has passed turns **MOM
  Pending** (with `mom_pending_since`), and the BDE gets an in-app notification (push through
  `tbo_smart.push`).
- **Reminder** `meeting_reminder_minutes` before a meeting (checked before the clock moves
  meetings on, so one booked minutes before it starts still gets it): Teams DM (the smart leads workflow
  URL), else email, plus in-app, once (`reminded_at` is written first, so a failed Teams post
  never sends it twice).
- **By hand:** Meeting in Progress, Completed, Follow-up Required and Cancelled. MOM Submitted
  and Cancelled are final.

### 5.3 Submitting the MOM

`save_mom(..., submit=1)` needs the key discussion points and a next step: a follow-up date, the
next meeting, or a closing lead status (Closed – Lost needs a reason). The meeting must have
started. It then:

1. sets MOM Submitted, `prepared_by`, `submitted_on`;
2. moves the lead to Nth Meeting Completed (§2.2);
3. creates the **"Sales follow-up" CRM Task** for `next_follow_up_on` (10:00, assigned to the
   BDE, titled "Follow up after the 1st meeting"; the next meeting details are its
   description), so the smart leads follow-up engine, Call today and the mobile follow-ups
   pick it up;
4. books the next meeting when its schedule was sent (`next_meeting`);
5. sets the lead status chosen by hand, if any, after the automatic move.

A draft MOM (`submit=0`) saves the fields without any of that.

### 5.4 The morning (09:30, working days; `run_meeting_morning`)

- **MOM Pending escalation, like a follow-up:** working days since `mom_pending_since` on the
  smart leads ladder (`lead_ladder_l1_days` 2, `lead_ladder_l2_days` 4): level 1 adds the BDE's
  BDM (CRM Sales Hierarchy), level 2 the sales head ("Head - Sales & Marketing", else the Sales
  & Marketing Head role). One in-app notification per level; people on approved leave are left
  out. The CRM card says who has been told.
- **Today's meetings and pending minutes** go into the smart leads follow-up digest (Teams,
  else email, plus in-app) as "Meetings today" and "MOM pending" sections; a BDE with only
  meetings still gets it. With the follow-up digest off, the same sections go out as a
  meetings-only digest.

### 5.5 AI draft (`draft_mom`)

"Draft MOM from my notes" sends the BDE's rough English, Malayalam or Manglish notes, the
meeting (number, date, type, participants, agenda) and the lead's smart-leads facts to
`tbo_smart.ai` (feature "MOM draft" in the TBO AI Usage Log). The answer fills agenda,
discussion points, requirements, decisions, client interest, objections, quotation feedback,
action items, next follow-up and next meeting details. **Nothing is written**; the BDE edits and
submits. The number guard (`ai.guarded`) drops any text field or action item with a number that
isn't in the notes, the meeting or the lead's facts, with a warning; dates must be from today to
180 days ahead. It needs the pipeline switch, **AI call notes** (`enable_lead_notes`) and a
provider set up.

### 5.6 The lead summary and "Needs immediate follow-up"

`get_lead_pipeline` returns, besides the cards: the last held meeting's date, the **next
activity** (the earlier of the next upcoming meeting and the next open follow-up task), the
meetings with minutes, the deal once Won, and **needs follow-up** with its reasons, on open
leads only: a MOM Pending meeting, a meeting set to Follow-up Required, an overdue follow-up,
or (past New Lead) nothing planned at all.

## 6. The deal at Closed – Won (`lead_pipeline/won.py`)

- CRM Lead `on_update`: when a lead turns Won-type with the switch on, Frappe CRM's own
  `convert_to_deal` makes the deal (contact, organization, products, owner and assignees as
  CRM maps them) in the **first Won-type CRM Deal Status**, so ERPNext CRM Settings' customer
  creation and the finance workflow's Finance tab take over. A lead with a deal keeps it. The
  lead stays Closed – Won (CRM's conversion would set "Qualified" when it exists) and gets a
  comment "Deal CRM-DEAL-… created". A failure (say, no name for the contact) refuses the status
  change with CRM's message.
- **No manual conversion** while the switch is on: tbo_smart overrides the whitelisted
  `crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal` (the lead page's Convert to Deal) and the
  mobile `convert_lead` refuses a lead that isn't Closed – Won ("Set the lead to Closed – Won:
  its deal is created then."); for a Won lead it returns its deal. Off, CRM's own conversion
  runs unchanged.
- The lead's quotations are re-linked to the deal here (§9.5).

## 7. API (`tbo_smart.api.lead_pipeline`)

| Method | Verb | Needs | Returns / does |
| --- | --- | --- | --- |
| `get_access` | GET | – | `{enabled, ai}`; off without the switch, the CRM app or CRM Lead read |
| `get_lead_pipeline(lead)` | GET | read on the lead | `{lead, status, status_type, deal, can_write, ai, summary: {last_meeting_on, next_activity, meetings_completed, needs_follow_up: {flag, reasons}}, meetings: [card]}` |
| `get_meeting(meeting)` | GET | read | The card plus the MOM fields, `action_items`, `attachments`, `can_write` |
| `schedule_meeting(lead, values)` | POST | write on the lead | `values`: meeting_type, meeting_link, location, scheduled_at (future), duration_minutes, participants, agenda, bde (an active user) |
| `update_meeting(meeting, values)` | POST | write | Schedule fields; a new time makes it Rescheduled |
| `set_meeting_status(meeting, status)` | POST | write | Meeting in Progress, Completed, Follow-up Required, Cancelled |
| `save_mom(meeting, values, submit=0, lead_status?, lost_reason?, next_meeting?)` | POST | write | §5.3 |
| `draft_mom(meeting, notes)` | POST | write, AI | `{fields, action_items, warnings}`, nothing written |

Settings (`tbo_smart.api.lead_pipeline_settings`, System Manager): `get_settings`,
`save_settings(values)` for `enable_lead_pipeline` and `meeting_reminder_minutes`.

Mobile: `tbo_smart.api.mobile.meeting` (contract §6.4) wraps these: `get_meetings(lead)`,
`get_meeting(name)`, `schedule(...)`, `set_status(name, status)`, `submit_mom(name, mom,
lead_status?, lost_reason?, next_meeting?)`. Fixtures `docs/fixtures/meeting.*.json`.

## 8. What the owner does after deploying

1. Deploy tbocloud/crm first, then tbo_smart. `bench migrate` runs the status migration:
   check the "Lead pipeline: N leads Old → New" lines in its output.
2. Optional: open CRM → Settings → Lead Statuses to recolour or reorder the 13 statuses.
3. CRM → **Settings → TBO → Lead pipeline** (System Manager): switch on **Lead pipeline:
   meetings, MOM and automatic statuses**; set the reminder minutes (60 by default).
4. For the AI MOM draft, the smart leads AI provider and **AI call notes** must be on
   (Settings → TBO → TBO smart leads).
5. For the BDM step of the MOM escalation, the CRM Sales Hierarchy must list the BDEs under
   their BDM; the sales head is TBO Smart Settings → Head - Sales & Marketing.
6. Tell the team: the Convert to Deal button is gone; set the lead to Closed – Won.
7. **Quotations (Phase 2):** in ERPNext set up the masters the builder offers: sales **Items**
   for TBO's services with an **Item Group** each, their rates (or set them in the CRM's catalogue),
   the GST **Sales Taxes and Charges Template**, a **Payment Terms Template** and **Terms and
   Conditions**. Then in CRM → Settings → TBO → Lead pipeline: switch on **Quotations on the
   lead**, pick the default terms, payment terms and print format (TBO Quotation), the validity
   and follow-up days and the roles that edit prices; Save; then add the **packages** in the
   catalogue below. For Convert to Sales Order, ERPNext CRM Settings must be on with ERPNext's
   CRM Settings → Frappe CRM data synchronization, and the default customer group a leaf group.

**Roles:** no new roles. Sales User / Sales Manager work on meetings of the leads they may
open; System Manager deletes meetings and edits the settings.

## 9. Quotations on the lead (Phase 2)

Switch: **TBO Smart Settings → Quotations on the lead** (`enable_lead_quotations`, off; needs the
lead pipeline). Code: `lead_pipeline/quotations.py`, `lead_pipeline/presets.py`,
`api/lead_quotation.py`; the builder reuses `api/finance/quotation.py` (the deal's dialog).

### 9.1 The quotation

An ordinary ERPNext **Quotation**, made out to the lead: *Quotation To* = **CRM Lead** (added to
the field's link filter next to Frappe CRM's CRM Deal), *Party* = the lead, `customer_name` = its
organization (else its name), `tbo_crm_lead` = the lead and `crm_deal` empty. The discount comes
off the **net total** (`apply_discount_on`), as the approval limits read it. Because it is a
normal Quotation, ERPNext's permissions, taxes, payment schedule and the discount/commission
control (`quotation-discount-control.md`) all apply.

| Quotation field (custom) | Purpose |
| --- | --- |
| `tbo_crm_lead` | The lead it belongs to |
| `tbo_lead_meeting` | The meeting it came from ("Create quotation" on a meeting card) |
| `tbo_package` | The package used, for the PDF's project duration |
| `tbo_quote_status` | §9.3 (allow on submit; history in Version and timeline comments) |
| `tbo_quote_family`, `tbo_revision_no`, `tbo_is_latest` | §9.4 |
| `tbo_sent_via`, `tbo_sent_on` | How and when it went to the client |

### 9.2 The catalogue (presets)

ERPNext's own masters wherever they fit (`presets.py`):

| Need | Master |
| --- | --- |
| Services and products, their category | **Item** (enabled sales items) and **Item Group** |
| Rates | **Item Price** on Selling Settings' selling price list |
| Packages: default lines with description, qty and rate, project duration, validity | **TBO Quotation Package** (new, with **TBO Quotation Package Item** lines). ERPNext's Product Bundle is a stock bundle with no rates, text or duration, so it doesn't fit. A line's rate of 0 means the item's price. |
| GST | **Sales Taxes and Charges Template** (the company's default is preselected) |
| Payment terms | **Payment Terms Template** (default in settings; ERPNext builds the schedule) |
| Terms and conditions | **Terms and Conditions** (default in settings; its text fills the quotation) |
| Validity | `quotation_validity_days` (30), or a package's own |

They are managed in the CRM (**Settings → TBO → Lead pipeline → Quotation catalogue**): new and
edited packages, and each service's rate. **Only the roles in "Roles that edit presets and
prices"** (`quotation_preset_roles`, default Sales Manager and System Manager) may change them;
the API refuses everyone else. A package name must be unique, also when one is renamed.

### 9.3 Status tracking (`tbo_quote_status`)

| Status | Set by |
| --- | --- |
| Draft | A draft with nothing waiting |
| Pending Approval | A draft with an open discount/commission request that is still needed (reuses TBO Quotation Adjustment; kept in step on save, submit and every request or decision). Once the discount is lowered within the limit it stops blocking. |
| Approved | A draft whose above-limit adjustment was granted by an approver (not one applied within the person's own limit), or a submitted one not sent yet |
| Sent to Client | **Mark sent** (below) |
| Viewed / Acknowledged | By hand, from Sent |
| Under Negotiation | By hand, from Sent, Viewed or Revision Required |
| Revision Required | By hand, or automatically when a revision is made of a quotation that was with the client |
| Accepted | By hand, from any with-the-client status |
| Rejected | By hand, with the client's reason; optionally closes the lead as Closed – Lost (lost reason required) |
| Expired | Nightly (`expire_quotations`): submitted, Approved or with the client, and `valid_till` has passed |

- Every change is written with a **Version** row and an Info comment on the quotation ("Quote
  status: Under Negotiation: wants 5% off").
- The lead follows: Sent → **Proposal Sent**, Under Negotiation → **Negotiation**, Accepted →
  **Closed – Won** (forward only, §2.2), which creates the deal (§6).
- **Mark sent** (`mark_sent`): a draft is **submitted first**, so the discount/commission control
  refuses it while an approval is missing; a draft with a request **pending approval can't be
  sent**. Then: **Email** sends it through ERPNext (`communication.email.make`) with the TBO PDF
  attached, logged on the quotation; **WhatsApp** returns the `wa.me` link with the message
  (the user attaches the PDF); **Manual** records it. A **Sales follow-up** task follows after
  `quotation_follow_up_days` (3; 0 = none), assigned to the lead owner.

### 9.4 Revisions

**Create revision** copies the quotation (lines, taxes, discount, terms) as a new **draft** with
the next `tbo_revision_no` in its family (`tbo_quote_family` = the first quotation's name), today's
date and a fresh validity; it becomes the **latest** (`tbo_is_latest`), the others are not. A
copy rather than ERPNext's amend: amend needs a cancelled document, and the earlier version must
stay valid and visible. Every version stays linked to the lead. **Revision history**
(`get_revisions`) lists the family with, for each, what changed from the one before: grand total,
discount %, lines added, removed and changed (qty, rate), terms changed, validity. Only the
latest revision can be sent (the API refuses an older one); the diff also flags a line whose
description changed.

### 9.5 Won: the deal, re-linking and the Sales Order

- When the lead turns Closed – Won (by hand or through an accepted quotation), §6 creates the deal
  and **re-links the lead's quotations**: `crm_deal` = the deal, and those made out to the lead
  become *Quotation To* CRM Deal with the deal as party. The deal's Finance tab then lists them,
  and ERPNext finds the deal's customer.
- **Convert to Sales Order** (`make_sales_order`): only an **Accepted** quotation of a won lead,
  only for people who may create Sales Orders. When the client's Customer was made for an earlier
  deal (so its `crm_deal` names that deal), the order still gets it. Frappe CRM creates the deal's ERPNext customer when
  missing (ERPNext CRM Settings must be on, and ERPNext's CRM Settings → Frappe CRM data
  synchronization), then ERPNext's own mapper makes a **draft** Sales Order; the finance
  workflow's pending-approval guard runs on its validate.

### 9.6 The PDF: print format "TBO Quotation"

A Jinja print format created and kept in step on every migrate from
`lead_pipeline/quotation_print.html`: the company logo (or name), address and GSTIN; the client
(organization, contact, email, phone); quotation number and revision, date, valid till, project
duration; the lines with description, qty, rate, discount and amount; subtotal, discount, each tax
and the grand total in words; the payment schedule; terms and conditions; the validity line.
Tables, not flex, so wkhtmltopdf lays it out. To change it, copy it under another name and pick
it in `quotation_print_format`. **Preview** (`/printview`) and **Download PDF** use it; **Save PDF
to the lead** (`save_pdf`) stores it as a private file "<quotation>--<time>.pdf" on the lead's Attachments.

### 9.7 API (`tbo_smart.api.lead_quotation`)

| Method | Verb | Needs | Does |
| --- | --- | --- | --- |
| `get_access` | GET | – | `{enabled, can_edit_presets, can_make_orders}` |
| `get_lead_quotations(lead)` | GET | read on the lead | The quotations (status, totals, family, latest, sent, package, saved PDF) and the deal |
| `get_quotation_form(lead, quotation?, meeting?)` | GET | read | The builder: choices (companies, tax templates with rates, payment terms, terms), the catalogue, and a new quotation's defaults or a draft |
| `save_quotation(lead, values, quotation?)` | POST | write | A draft (the deal dialog's values plus `description` per line, `tc_name`, `payment_terms_template`, `package`, `meeting`); returns it with `needs_approval` |
| `mark_sent(quotation, via, recipients?, message?, follow_up_days?)` | POST | write | §9.3 |
| `set_quote_status(quotation, status, note?, close_lead?, lost_reason?)` | POST | write | §9.3 |
| `create_revision(quotation)` / `get_revisions(quotation)` | POST / GET | write / read | §9.4 |
| `save_pdf(quotation)` | POST | write | §9.6 |
| `make_sales_order(quotation)` | POST | Sales Order create | §9.5 |
| `get_catalogue` / `save_package(values)` / `set_service_rate(item_code, rate)` | GET / POST / POST | preset roles for the writes | §9.2 |

## 10. Tests

`tbo_smart/tests/test_lead_pipeline.py` (helpers in `tbo_smart/test_utils.py`; AI mocked at the
HTTP level): the statuses and their types, New Lead as the default, lost reason required (CRM
and the mobile API), forward-only moves; the migration table, a full run with migration notes
(original Demo / Quotation), kept lost reasons, no deals for converted leads, retired statuses
kept as JSON, idempotency (a lead set back by hand isn't moved again) and revert; meeting
numbering with a cancelled meeting and Additional meetings, a manual status not pulled back, the
switch off; the clock (in progress, MOM Pending with the BDE told, one reminder only); MOM submit
moving the lead, creating the Sales follow-up and the next meeting, final once submitted;
closing the lead from the MOM with a reason; the needs-follow-up flag; MOM escalation BDE → BDM →
sales head once per level on working days; permissions (another BDE, list query, delete); the
https link rule; the AI draft writing nothing and dropping a guessed number; the mobile shape
against the fixture; the deal at Closed – Won (Won stage, owner, once only, manual conversion
refused) and with the switch off; and the no-CRM guard. Smart leads, mobile and insights tests
use the new status names.

`tbo_smart/tests/test_lead_quotations.py`: the builder from a package with GST and a 10%
discount (totals 70,000 → 63,000 + 11,340 = 74,340), party and package on the quotation; only
preset roles edit rates and packages, duplicate package names; a quotation from a meeting;
permissions and the switch; Sent → Negotiation → Accepted moving the lead to Closed – Won with the
deal created and the quotation re-linked, Version rows; sending blocked while approval is pending
and allowed once the BDM approves; Rejected needs a reason and can close the lead; expiry; WhatsApp
link and email through ERPNext with the TBO format; the revision chain with its diff and latest
flag; the PDF saved on the lead and the Sales Order from an accepted quotation (permissions, the
deal's customer); the print format rendering.

## Changelog

- **v1.1 (2026-10-16):** Phase 2: quotations on the lead (§9): the catalogue (TBO Quotation
  Package, service rates), the builder reusing the deal's dialog, status tracking with Mark sent,
  revisions with their diff, the TBO Quotation print format and PDF on the lead, re-linking at
  Closed – Won and Convert to Sales Order.
- **v1.0 (2026-10-16):** Phase 1: pipeline statuses and their migration, TBO Lead Meeting with
  its MOM, automatic statuses, MOM Pending clock, reminders and escalation, AI MOM draft, the
  deal at Closed – Won, the API, settings and mobile (contract v1.8).
