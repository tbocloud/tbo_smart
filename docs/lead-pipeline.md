# Lead pipeline: stages, meetings, MOM and quotations on the lead (living spec, v1.7)

**Status:** Phases 1 to 3 built 2026-10-16: the pipeline stages, meetings and minutes of meeting
(MOM), automatic lead stages, the deal at Closed – Won, quotations on the lead (presets,
builder, status tracking, revisions, the TBO PDF), and the pipeline dashboard. v1.3 moved
everything from Frappe CRM's CRM Lead to **ERPNext's own Lead** (and the deal to its
**Opportunity**), `docs/erpnext-lead-cutover.md`. Everything is behind a switch that is off by
default. This file is the living spec: it describes what is built, and every change to the
feature updates it in the same pull request. The screens are the TBO Support hub's lead page
(`docs/hub-leads-api.md`) and the TBO Smart app.

- **Site:** india.teambackoffice.com (tboindia): Frappe v15, ERPNext, HRMS and tbo_smart (Frappe
  CRM until the cutover). Everything here reads as off on a site without ERPNext
  (`tbo_smart.sales.erpnext_installed`).
- **Related specs:** `smart-leads.md` (scores, Call today, follow-ups, AI notes; it reads the
  statuses defined here), `crm-finance-workflow.md` (the deal's Finance tab, quotations,
  Sales Order and invoices), `quotation-discount-control.md` (the BDE → CEO approvals),
  `tbo-smart-api-ess-crm.md` (the mobile contract, v1.8).

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Stages, meetings and MOM, automatic stages, deal at Closed – Won, mobile | Built (this file) |
| 2 | Quotations on the lead: presets, builder, status tracking, revisions, PDF (§9) | Built |
| 3 | Pipeline dashboard (§11) | Built |
| Sales 2 | Segment rate card: a price list and a discount band per segment (§9.8) | Built |
| Sales 3-4 | Proposals: builder, templates, AI draft, the client's page, send, accept (`proposals.md`) | Built |

## 1. Owner's decisions (2026-10-15)

| # | Decision | Effect |
| --- | --- | --- |
| D1 | Everything happens on the lead. A deal is created automatically only at Closed – Won. | Meetings and MOM hang off the Lead. Closed – Won runs ERPNext's own Lead → Opportunity mapper; the deal-based finance workflow carries on from there. While the pipeline is on, nobody converts a lead by hand (§6). |
| D2 | Meeting links are pasted by hand (Google Meet or Teams). No calendar integration. | `meeting_link` is a plain https URL. |

## 2. Stages

### 2.1 The stages (`lead_pipeline/statuses.py`)

A lead's stage is the custom **`Lead.tbo_stage`**, a Select of these 13 names in this order
(default New Lead). ERPNext's own `Lead.status` is left alone: ERPNext sets it itself (Lead,
Opportunity, Quotation, Converted…). Each stage has a type, as Frappe CRM's lead statuses had,
and a colour name for the screens (`stage_options()`, also the hub's `get_options`). The code
names them in this one module.

| # | Stage | Type | Open for follow-ups and Call today | Scoring points |
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

- **Stage history:** every stage a lead enters is a row of its **`tbo_stage_log`** (TBO Stage
  Change: `stage`, `previous_stage`, `changed_on`, `changed_by`; the insert is the first row),
  written on validate by `tbo_smart.sales.stages`. Scores, the dashboard and the hub's timeline
  read it.
- **Lost reason:** a Closed – Lost lead must have `tbo_lost_reason` (an **Opportunity Lost
  Reason**) and, for "Other", `tbo_lost_notes`, as Frappe CRM required (`sales.stages.LeadStage`).
  The mobile `set_lead_status`, the MOM's lead stage and the hub's `set_stage` take a
  `lost_reason`: a lost reason's name is used as it is, free text becomes "Other" with the text
  in the notes (one helper, `sales.stages.set_lost_reason`, also for deals).
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
  submitting a MOM may not own the lead), so the stage log keeps it, plus an Info comment on the
  timeline: "Status set to First Meeting Completed: MOM of the 1st meeting submitted".
- **System saves skip unrelated mandatory fields.** Every change the system makes to a lead goes
  through one helper, `sales.stages.system_save`, which sets `flags.ignore_mandatory`, so an old
  or imported lead with an empty mandatory field (e.g. Source made required on the site) doesn't
  block it: a stage move (`set_lead_stage`: the hub, the app, the MOM dialog, a lost quotation,
  the autopilot), the pipeline's moves (`transitions.set_lead_status`: proposals shared, sent,
  viewed and accepted, quotations, meetings, the deal at Closed – Won), a smart-leads status
  and the lead triage's close (`smart_leads.notes.set_lead_status`), an owner change
  (`reassign_leads`) and a caller's new lead (`telephony.calls.new_caller_lead`). A person
  editing the lead's own fields (`hub_leads.update_lead`, the desk) is still asked for them.

## 3. The status migration (done)

On 2026-10-16 a patch mapped the 1,572 migrated CRM Leads' old statuses (New, Contacted, Nurture,
Qualified, Unqualified, Junk, Converted) onto these stages. It ran on Frappe CRM's records and
was removed with the move to ERPNext's Lead; the cutover copies each lead's stage as the CRM
has it (`erpnext-lead-cutover.md` §4).

## 4. Data and settings

### 4.1 Fields (`lead_pipeline/setup.py`, ERPNext sites only)

| Doctype | Field | Purpose |
| --- | --- | --- |
| Lead | `tbo_stage` (Select), `tbo_stage_log` (TBO Stage Change), `tbo_lost_reason`, `tbo_lost_notes` | §2 |
| Lead | `tbo_segment` (Select Retail, Small, Medium, Large, Premium) | The client's segment: its price list and discount band (§9.8) |
| Lead | `tbo_department` (Link TBO Lead Department), `tbo_lead_type` (Select) | Lead departments and who sees them (`lead-departments.md`) |
| Lead / Opportunity | `crm_lead_ref` / `crm_deal_ref` (hidden) | The Frappe CRM record a copy came from (`erpnext-lead-cutover.md`) |
| Opportunity | `tbo_customer`, `tbo_next_step`, `tbo_closed_on` (won or lost on), `tbo_stage_log` | The deal (§6) |
| Sales Stage | `tbo_stage_type` (Open, Ongoing, On Hold, Won, Lost), `tbo_probability`, `tbo_position` | The deal's stage; a Won and a Lost stage are created when none is typed so |
| TBO Smart Settings | `enable_lead_pipeline` | The switch (§8) |
| TBO Smart Settings | `meeting_reminder_minutes` (60, 15-1,440) | The reminder before a meeting |
| TBO Smart Settings | `enable_lead_quotations`, `quotation_validity_days` (30), `quotation_follow_up_days` (3), `quotation_terms`, `quotation_payment_terms`, `quotation_print_format` (blank: TBO Quotation), `quotation_preset_roles` (Sales Manager, System Manager) | §9 |
| TBO Smart Settings | `enable_pipeline_dashboard` | §11 |
| TBO Smart Settings | `segment_rates` (table **TBO Segment Rate**: `segment`, `price_list`, `max_discount`) | §9.8 |
| Quotation | `tbo_lead_meeting`, `tbo_package`, `tbo_quote_status`, `tbo_quote_family`, `tbo_revision_no`, `tbo_is_latest`, `tbo_sent_via`, `tbo_sent_on`, `tbo_proposal` | §9.1 |
| Sales Order, Sales Invoice | `tbo_proposal` (read only) | §9.5 |

Masters: the Opportunity Lost Reason "Other" and the role **Hub Integration** are created on
migrate.

### 4.2 TBO Lead Meeting (`TBO-MTG-YYYY-#####`, track changes)

| Section | Fields |
| --- | --- |
| Meeting | `lead`, `lead_name`, `stage` (number), `stage_label` ("1st meeting", "2nd meeting", "3rd meeting", then "Additional meeting N"), `status`, `bde` (default: the lead owner) |
| Schedule | `meeting_type` (Online / Physical visit), `meeting_link` (https, Online only), `location` (Physical visit only), `scheduled_at`, `duration_minutes` (60; 5-720), `participants` |
| MOM | `agenda`, `discussion_points`, `client_requirements`, `decisions`, `client_interest` (Positive / Neutral / Negative), `objections`, `quotation_feedback` |
| Next steps | `action_items` (table **TBO Lead Meeting Action**: `item`, `responsible`, `due_date`, `done`), `next_follow_up_on`, `next_meeting_details`, `prepared_by`, `submitted_on`, `follow_up_task` (ToDo), `next_meeting` |
| Tracking | `mom_pending_since`, `escalation_level` (0 BDE, 1 BDM, 2 sales head), `reminded_at` |

Attachments are ordinary Frappe files attached to the meeting.

**Permissions** (`has_permission`, `permission_query_conditions` hooks): a meeting is open to
whoever may read its Lead under the sales rules (owner, assignee, sales hierarchy, Sales
Manager: `tbo_smart.sales.permissions`); changing it needs write on the lead. List queries are
limited to the leads `frappe.get_list("Lead")` returns for the user. **Only a System Manager deletes a
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
3. creates the **"Sales follow-up" ToDo** on the lead for `next_follow_up_on` (allocated to the
   BDE, "Follow up after the 1st meeting", with the next meeting details below it), so the
   smart leads follow-up engine, Call today and the mobile follow-ups pick it up;
4. books the next meeting when its schedule was sent (`next_meeting`);
5. sets the lead stage chosen by hand, if any, after the automatic move.

A draft MOM (`submit=0`) saves the fields without any of that.

### 5.4 The morning (09:30, working days; `run_meeting_morning`)

- **MOM Pending escalation, like a follow-up:** working days since `mom_pending_since` on the
  smart leads ladder (`lead_ladder_l1_days` 2, `lead_ladder_l2_days` 4): level 1 adds the BDE's
  BDM (TBO Sales Hierarchy), level 2 the sales head ("Head - Sales & Marketing", else the Sales
  & Marketing Head role). One in-app notification per level; people on approved leave are left
  out. The meeting card says who has been told (`escalation_level`).
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

- Lead `on_update`: when a lead turns Won-type with the switch on, ERPNext's own Lead →
  Opportunity mapper makes the deal (contact, address, owner and notes carried over;
  `opportunity_from` Lead, the lead as party) in the **first Won-type Sales Stage**, and the
  finance workflow's Finance view takes over. ERPNext moves the lead's open ToDos and Events to
  the deal. A lead with a deal keeps it. The lead gets a comment "Deal CRM-OPP-… created". An
  import (the CRM cutover) makes no deals: it copies them.
- **No manual conversion** while the switch is on: the mobile `convert_lead`
  (`won.convert_lead`) refuses a lead that isn't Closed – Won ("Set the lead to Closed – Won:
  its deal is created then."); for a Won lead it returns its deal. Off, it makes the deal in the
  first Open-type (else Ongoing) stage and closes the lead as Closed – Won, as Frappe CRM's
  conversion closed it.
- The lead's quotations are linked to the deal here (§9.5).

## 7. API (`tbo_smart.api.lead_pipeline`)

| Method | Verb | Needs | Returns / does |
| --- | --- | --- | --- |
| `get_access` | GET | – | `{enabled, ai}`; off without the switch, ERPNext or Lead read |
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

1. Deploy tbo_smart and run the cutover (`erpnext-lead-cutover.md` §5). `bench migrate`
   creates the fields, the Won and Lost Sales Stages and the "Other" lost reason.
2. Desk → **TBO Smart Settings** (System Manager) → Lead pipeline: switch on **Lead pipeline:
   meetings, MOM and automatic statuses**; set the reminder minutes (60 by default). (The CRM's
   settings pages went with the CRM app; the hub may show them later.)
3. For the AI MOM draft, the smart leads AI provider and **AI call notes** must be on (same
   settings).
4. For the BDM step of the MOM escalation, the **TBO Sales Hierarchy** must list the BDEs under
   their BDM (the cutover copies the CRM's); the sales head is TBO Smart Settings → Head - Sales
   & Marketing.
5. Tell the team: set the lead to Closed – Won to make its deal.
6. **Quotations (Phase 2):** in ERPNext set up the masters the builder offers: sales **Items**
   for TBO's services with an **Item Group** each, their rates, the GST **Sales Taxes and
   Charges Template**, a **Payment Terms Template** and **Terms and Conditions**. Then switch on
   **Quotations on the lead** in TBO Smart Settings, pick the default terms, payment terms and
   print format (TBO Quotation), the validity and follow-up days and the roles that edit prices;
   add the **packages** (TBO Quotation Package). For Convert to Sales Order the default customer
   group must be a leaf group (ERPNext makes the Customer from the lead).
7. **Pipeline dashboard (Phase 3):** switch on **Pipeline dashboard** in the same settings. The
   hub shows the page (`api/lead_pipeline_dashboard.py`) and the cards (`api/dashboard.py`).

**Roles:** no new roles. Sales User / Sales Manager work on meetings of the leads they may
open; System Manager deletes meetings and edits the settings.

## 9. Quotations on the lead (Phase 2)

Switch: **TBO Smart Settings → Quotations on the lead** (`enable_lead_quotations`, off; needs the
lead pipeline). Code: `lead_pipeline/quotations.py`, `lead_pipeline/presets.py`,
`api/lead_quotation.py`; the builder reuses `api/finance/quotation.py` (the deal's dialog).

### 9.1 The quotation

An ordinary ERPNext **Quotation**, made out to the lead: *Quotation To* = **Lead** (one of
ERPNext's own parties), *Party* = the lead, `customer_name` = its company name (else its name),
and ERPNext's own `opportunity` = the lead's deal once there is one. A quotation belongs to a lead
when it is made out to it or linked to its deal (`quotations.lead_of`, `lead_quotations`). The
discount comes off the **net total** (`apply_discount_on`), as the approval limits read it. Because it is a
normal Quotation, ERPNext's permissions, taxes, payment schedule and the discount/commission
control (`quotation-discount-control.md`) all apply.

| Quotation field (custom) | Purpose |
| --- | --- |
| `tbo_lead_meeting` | The meeting it came from ("Create quotation" on a meeting card) |
| `tbo_package` | The package used, for the PDF's project duration |
| `tbo_quote_status` | §9.3 (allow on submit; history in Version and timeline comments) |
| `tbo_quote_family`, `tbo_revision_no`, `tbo_is_latest` | §9.4 |
| `tbo_sent_via`, `tbo_sent_on` | How and when it went to the client |
| `tbo_proposal` | The client document it prices (a TBO Proposal: the proposal, or a quotation document), set when it is priced and sent; the client's reference (§9.5) |

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

They are managed through `api/lead_quotation.py` (`get_catalogue`, `save_package`,
`set_service_rate`; the CRM's catalogue screen went with the CRM app) or in the desk: new and
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
  and **links the lead's quotations to it** (ERPNext's `opportunity`, where empty). They stay
  made out to the Lead. The deal's Finance view then lists them.
- **Convert to Sales Order** (`make_sales_order`): only an **Accepted** quotation of a won lead,
  only for people who may create Sales Orders. ERPNext's own mapper makes a **draft** Sales Order
  and, for a quotation made out to a Lead, the Customer from the lead (`Customer.lead_name`)
  when it has none. When the deal already names the client's Customer (`tbo_customer`, e.g. one
  an earlier deal made) and no Customer was made from this lead, the quotation is made out to
  that Customer first, so no second Customer is made. The order carries the deal
  (`opportunity`), and the deal records the order's Customer as `tbo_customer`. The finance
  workflow's pending-approval guard runs on its validate.
- **The proposal reference on the order:** the quotation's `tbo_proposal` (Link, not `no_copy`)
  is copied by ERPNext's mapper to the Sales Order's and on to the Sales Invoice's own
  `tbo_proposal`, so accounts trace an order or an invoice to the proposal the client accepted.
  Chosen over `po_no`: that is the client's own purchase order number, printed on the invoice as
  theirs, and a proposal number there would be wrong once the client sends a PO; a Link field
  also opens the proposal from the desk.

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
| `get_catalogue(segment?)` / `save_package(values)` / `set_service_rate(item_code, rate)` | GET / POST / POST | preset roles for the writes | §9.2; the catalogue priced for `segment` (§9.8) |

`get_quotation_form` also returns `pricing: {segment, price_list, discount_band}` for the lead,
and its catalogue is priced from that price list.

### 9.8 The segment rate card

TBO Smart Settings → **Segment rate card** (`segment_rates`, one row per segment, table **TBO
Segment Rate**). Code: `lead_pipeline/rate_card.py`. It reads as empty while Quotations on the
lead is off.

| Column | Meaning |
| --- | --- |
| `segment` | Retail, Small, Medium, Large or Premium (`Lead.tbo_segment`); once per segment |
| `price_list` | An enabled **selling** ERPNext Price List; its **Item Price** rows are the segment's rates. Blank (or since disabled): Selling Settings' selling price list |
| `max_discount` | The **discount band**: the discount % anyone may give on that segment's quotations without approval (0 to 100; blank or 0: none) |

- **Price:** the builder's catalogue and package lines take each service's rate from the lead's
  segment's list, item by item, falling back to the selling price list's rate where the
  segment's list has none. A new lead quotation's `selling_price_list` is the segment's list.
  A lead without a segment, or a segment not on the card, uses the selling price list.
- **Band and approval:** the discount control (`quotation-discount-control.md`) reads the
  discount limit on a lead's quotation as **the higher of** the person's level limit and the
  segment's band (`quotation_control.effective_limit`). So a BDE with no limit set may give up
  to the band; above it the quotation needs approval exactly as before (request, BDM → CEO,
  voiding, Mark sent refused while pending); a level whose own limit is higher keeps it.
  Commission is untouched. `get_quotation_adjustment_status` and `request_adjustment` use the
  same limit, so the chips and "within your limit" agree with the save.
- **The lead's segment is guarded:** it sets the band, so once a lead has a segment only the
  preset editors move it to another (`rate_card.validate_lead_segment`, Lead validate; refused
  with a message otherwise). A lead without one gets its first from whoever works it, as on
  creation. Imports (the cutover) are not checked.
- **Lookups:** the status of many quotations at once (`get_quotations_adjustment_status`) reads
  their bands in a fixed number of queries (`rate_card.quotation_bands`).
- **Who edits:** only the roles in "Roles that edit presets and prices"
  (`presets.can_edit_presets`); everyone with quotations reads it. The settings check the rows
  (a segment once, a selling list, a band from 0 to 100%).
- **Hub:** `tbo_smart.api.hub_leads` `get_rate_card`, `save_rate_card`, `set_segment_rate`
  (`hub-leads-api.md` §2.3), for the hub's Settings screen.

## 11. Pipeline dashboard (Phase 3)

Switch: **TBO Smart Settings → Pipeline dashboard** (`enable_pipeline_dashboard`, off; needs the
lead pipeline). Code: `lead_pipeline/dashboard.py` (one `PipelineDashboard` class, one grouped
query per figure), `api/lead_pipeline_dashboard.py`.

**Who sees what:** the CRM scope rule of the mobile API (`capabilities.crm_scopes`): a
salesperson their own leads, a team lead in the TBO Sales Hierarchy their team, a Sales Manager
outside the hierarchy and a System Manager everyone. Every figure counts only the leads of those
owners. The BDE filter is refused for someone outside the scope.

**Filters:** from / to date (this month by default, at most two years), BDE, lead source, lead
status.

| Figure | Definition |
| --- | --- |
| Leads by status | Leads created in the range, by their stage now, in pipeline order (empty stages too) |
| Meetings completed | MOM submitted in the range, split 1st / 2nd / 3rd / Additional |
| Upcoming meetings | Scheduled or Rescheduled from now on (count and the next 10) |
| MOM pending | Meetings MOM Pending now (count and 10) |
| Overdue follow-ups | Open ToDos on open leads due before today |
| High potential | Open leads that are Hot (smart-leads score) or Positive / Interested, best score first |
| Won / lost, lost reasons | Leads that entered Closed – Won / Closed – Lost in the range (their stage log), with the lost reasons |
| Win rate | Won ÷ (won + lost) |
| Conversion (Lead → won) | Leads created in the range that are won now ÷ leads created in the range |
| By BDE | Meetings held, MOM submission rate (meetings due in the range that ended, with their MOM ÷ all of them), MOM pending now, quotations made and sent in the range, leads won |
| Quotations | Lead quotations made in the range by status, count and value (company currency); pending approval and with the client now; accepted / rejected; **active value**: the latest revision of each quotation, Approved or with the client and still valid (the revenue potential) |

**Dashboard cards** (`get_cards`, served with the finance and smart-lead cards by
`tbo_smart.api.dashboard.get_cards(from_date?, to_date?, user?)`): **Meetings held**, **MOM
pending** (lower is better), **Lead win rate** and **Active quotations** (with quotations on). They count the dashboard's
user (a salesperson always gets themselves; a team lead in the sales hierarchy one of their team,
else themselves) when the session user may see that person, else nothing; their scope otherwise.

| Method | Verb | Does |
| --- | --- | --- |
| `get_access` | GET | `{enabled}` |
| `get_pipeline_dashboard(from_date?, to_date?, bde?, source?, status?)` | GET | The figures above plus `scope` (own / team / all) and `people` (the BDE filter's choices) |

**The hub's Leads overview and Sales scoreboard** reuse this class: `lead_pipeline/overview.py`
(`LeadOverview` and `SalesActivity` extend `PipelineDashboard` with the pipeline's value by
stage, the funnel, contacted and stale leads, segments, sources and per-salesperson activity),
served by `hub_leads.get_overview` and `get_sales_activity` (`hub-leads-api.md` §2.4, §2.5).

## 12. Tests

`tbo_smart/tests/test_lead_pipeline.py` (helpers in `tbo_smart/test_utils.py`; AI mocked at the
HTTP level): the stages and their types, the Select and New Lead as the default with ERPNext's
own status left alone, the stage log's first row, lost reason required (the Lead and the mobile
API), forward-only moves; meeting
numbering with a cancelled meeting and Additional meetings, a manual status not pulled back, the
switch off; the clock (in progress, MOM Pending with the BDE told, one reminder only); MOM submit
moving the lead, creating the Sales follow-up and the next meeting, final once submitted;
closing the lead from the MOM with a reason; the needs-follow-up flag; MOM escalation BDE → BDM →
sales head once per level on working days; permissions (another BDE, list query, delete); the
https link rule; the AI draft writing nothing and dropping a guessed number; the mobile shape
against the fixture; the deal at Closed – Won (an Opportunity from the lead in a Won-type stage,
owner, once only, manual conversion refused) and with the switch off (no deal; converting by
hand makes one and closes the lead); and the no-ERPNext guard.

`tbo_smart/tests/test_pipeline_dashboard.py`: a BDE's own figures (status counts, won, lost,
win rate, conversion, meetings, MOM pending, upcoming, overdue follow-ups, high potential, the
BDE row, quotations and active value) without another BDE's leads, and the BDE filter refused
outside the scope; a manager's everyone view and the BDE, source and status filters; the cards
for the dashboard's user; the switch.

`tbo_smart/tests/test_segment_rate_card.py`: the card's rows and rates, only preset editors
change it, rows checked, segment rates on their own list; the builder priced by segment; within
the band no approval, a segment without a band keeps the levels' limits, above the band the
approval chain (request, BDM approves, then Mark sent), a higher level limit kept.

`tbo_smart/tests/test_lead_quotations.py`: the builder from a package with GST and a 10%
discount (totals 70,000 → 63,000 + 11,340 = 74,340), party and package on the quotation; only
preset roles edit rates and packages, duplicate package names; a quotation from a meeting;
permissions and the switch; Sent → Negotiation → Accepted moving the lead to Closed – Won with the
deal created and the quotation linked to it (still made out to the Lead), Version rows; sending blocked while approval is pending
and allowed once the BDM approves; Rejected needs a reason and can close the lead; expiry; WhatsApp
link and email through ERPNext with the TBO format; the revision chain with its diff and latest
flag; the PDF saved on the lead and the Sales Order from an accepted quotation (permissions, the
Customer ERPNext made from the lead, the deal on the order and the order's customer on the
deal); the print format rendering.

## 13. The document chain by lead type (`lead_pipeline/document_types.py`: `CHAINS`)

The documents a lead goes through follow its `tbo_lead_type` (`lead-departments.md`). One table,
`CHAINS`, holds the difference; `chain_for(lead)` reads it and everything else asks it.

| Lead type | Documents | After the last one | New proposal starts from |
|---|---|---|---|
| ERP / Software, or none | Proposal -> Quotation -> Project Agreement -> BRD -> SRS -> Implementation Plan (each on its own switch) | `create_project`, then UAT | the template chosen |
| Content / Digital Marketing | Proposal only | `create_project` from the accepted proposal (no UAT step) | **Digital Marketing – 6-month strategy** |

- `get_document_chain` shows only the types of the lead's chain; `create_document` refuses a type
  outside it (so no Quotation from a content lead's proposal).
- **The strategy preset** (`proposal_templates.py`, `STRATEGY_SECTIONS`) is the same 23 master
  sections headed for marketing: Brand & Audience, Goals & KPIs, Strategy: Platforms & Content
  Pillars, What We Will Do, Monthly Content & Approval Cycle, Deliverables Each Month, 6-Month
  Roadmap (Month 1-6), Your Team, Fees (per month and for six months, six monthly instalments),
  Terms (six-month term, credentials handover on starting, handback on cancellation). The
  software-only sections are off. Its modules (Content Strategy & Planning, Content Creation &
  Publishing, Reporting & Review) start at price 0 like all modules: the owner prices them. The AI
  writes only the four narrative sections, from the section guidance, and its numbers are checked
  against the brief as always: posts / reels / stories counts and targets are never invented, so
  presales types them into Deliverables or the scoping notes. A library row or module may carry
  `!<solution type>` in Solution types to leave that type out; the general software rows are
  left out of the strategy that way (`setup.add_strategy_preset` brings the preset to sites seeded
  earlier; patch `proposal_strategy_preset`).
- **The hub's project** (`strategy_project.py`): `get_accepted_proposal(name)` returns the accepted
  proposal's project brief (`hub-proposals-api.md`); `link_project(project, plan?, proposal?, lead?, url?)` takes the
  accepted proposal as it takes a plan, once. The start is the proposal's `start_date`, else the day
  it was accepted; the end is six months later.

## Changelog

- **v1.8 (2026-10-28):** the document chain by lead type (§13): Content / Digital Marketing leads
  go Proposal (the 6-month strategy) -> project.
- **v1.7 (2026-10-20):** `tbo_proposal` on the Quotation, the Sales Order and the Sales Invoice:
  the client document the price was accepted on (§9.5).
- **v1.6 (2026-10-20):** every system save of a lead skips unrelated mandatory fields through
  `sales.stages.system_save` (§2.2); a shared proposal no longer fails with "Value missing for
  Lead: Source".
- **v1.5 (2026-10-19):** proposals (`proposals.md`): a proposal's quotation goes out with it,
  its view moves the quotation to Viewed / Acknowledged, its acceptance to Accepted (the lead to
  Closed – Won) and its decline to Rejected.
- **v1.4 (2026-10-19):** the segment rate card (§9.8): a price list and a discount band per
  segment, the builder priced by the lead's segment, the band in the discount control, and the
  hub's rate card methods.

- **v1.3 (2026-10-16):** on ERPNext's Lead and Opportunity instead of Frappe CRM's records
  (`erpnext-lead-cutover.md`): the stage is `Lead.tbo_stage` with its stage log, the deal an
  Opportunity made by ERPNext's mapper, follow-ups ToDos, quotations made out to the Lead and
  linked to the deal by `opportunity`; the status migration and the Convert override removed;
  the dashboard cards behind `api/dashboard.py`.

- **v1.2 (2026-10-16):** Phase 3: the Pipeline page and the pipeline cards on the CRM dashboard
  (§11).
- **v1.1 (2026-10-16):** Phase 2: quotations on the lead (§9): the catalogue (TBO Quotation
  Package, service rates), the builder reusing the deal's dialog, status tracking with Mark sent,
  revisions with their diff, the TBO Quotation print format and PDF on the lead, re-linking at
  Closed – Won and Convert to Sales Order.
- **v1.0 (2026-10-16):** Phase 1: pipeline statuses and their migration, TBO Lead Meeting with
  its MOM, automatic statuses, MOM Pending clock, reminders and escalation, AI MOM draft, the
  deal at Closed – Won, the API, settings and mobile (contract v1.8).
