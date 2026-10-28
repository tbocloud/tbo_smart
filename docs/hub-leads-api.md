# Hub leads API (`tbo_smart.api.hub_leads`), v1.10

**Status:** built 2026-10-16. The TBO Support hub's lead page (tbocloud/helpdesk) shows and edits
tboindia's leads through these methods. The leads are ERPNext's own **Lead**
(`docs/erpnext-lead-cutover.md`). This file is the contract: the hub builds against exactly this,
and every change to the API updates it in the same pull request.

## 1. Calling it

- **One API key:** the hub's integration user on tboindia, a System User with the role in
  TBO Smart Settings → **Hub integration role** (blank: **Hub Integration**, created on migrate).
  Give it System Manager as well, so ERPNext's own role permissions never stop a call the acting
  user may make.
- **`acting_user`** on every call: the hub user's email. It must be a user on tboindia that is
  **enabled**, a **System User**, and has a **sales role** (Sales User, Sales Manager or System
  Manager) and is not itself an integration user; otherwise the call fails with **403**
  (`PermissionError`). The integration user without `acting_user` also gets 403, except for
  `get_sales_activity` (§2.5), which then counts everyone.
- **The call runs as that user** (`frappe.set_user` for the call, then back to the integration
  user): lists are their `frappe.get_list`, a lead opens only when they may read it, writes need
  their write permission, and every record written is theirs (Comment owner, the lead's
  `owner` and default `lead_owner`). Who sees which lead is TBO's sales rule: owner, assignee,
  their team in the sales hierarchy, Sales Managers outside the hierarchy and System Managers
  everything (`erpnext-lead-cutover.md` §3).
- **Anyone else** calling these (a user's own session or key) acts as themselves; passing
  another user's `acting_user` is 403.
- **TBO Smart** (the mobile app) reaches the overview and the AI methods through
  `tbo_smart.api.mobile.leads`, which calls these as the signed-in person behind the app's `crm`
  capability (`tbo-smart-api-ess-crm.md` §6.5).
- **Writes are POST**, reads GET. Arguments are form or JSON fields; `data` is a JSON object
  (or its string). Errors are Frappe's: `exc_type` `PermissionError` (403), `ValidationError`
  (417), `DoesNotExistError` (404).

```
curl -s -X POST https://india.teambackoffice.com/api/method/tbo_smart.api.hub_leads.create_lead \
  -H "Authorization: token <api key>:<api secret>" -H "Content-Type: application/json" \
  -d '{"acting_user": "faisal@teambackoffice.com", "data": {"lead_name": "Ravi Kumar", "mobile_no": "9847012345"}}'
```

## 2. Methods

| Method | Verb | Arguments | Returns |
| --- | --- | --- | --- |
| `get_options` | GET | `acting_user` | `{stages: [{name, label, type, color}], sources: [], segments: [], departments: [{name, lead_type, is_active, assignment, stale_after_days, is_head, is_member}], lead_types: [], owners: [{email, full_name}], territories: [], industries: [], can_call}` |
| `list_leads` | GET | `search?`, `stage?`, `owner?`, `segment?`, `source?`, `temperature?` (Hot / Warm / Cold), `metric?` with `period?` / `from_date?` / `to_date?` (§2.4), `view` (`all` / `mine` / `call_today` / `follow_up`, default `all`), `start` (0), `page_length` (20, at most 100), `acting_user` | `{rows: [Row], total}` |
| `get_overview` | GET | `period` (`week` / `month` / `quarter` / `custom`; default `month`, or `custom` when dates are sent), `from_date?`, `to_date?` (custom), `owner?`, `acting_user` | Overview (§2.4) |
| `get_lead_intake` | GET | `from_date?`, `to_date?` (default today, at most 62 days), `owner?`, `acting_user` | Intake (§2.4.1) |
| `get_proposal_money` | GET | `period`, `from_date?`, `to_date?`, `owner?` (as `get_overview`), `acting_user` | Money (§2.8) |
| `get_sales_activity` | GET | `from_date?`, `to_date?` (default this month), `acting_user?` (§2.5) | `{from_date, to_date, currency, people: [Activity]}` |
| `get_lead` | GET | `name`, `acting_user` | `{lead: Lead, timeline: [Item], quotations: [Quotation], proposals: [Proposal row], allowed_stages: [stage name]}` |
| `create_lead` | POST | `data`, `allow_duplicate?` (1 to create past a duplicate, §2.9), `acting_user` | as `get_lead` |
| `find_duplicate_leads` | GET | `email?`, `phone?`, `company?`, `exclude?` (a lead name), `acting_user` | `[Match]` (§2.9) |
| `get_stale_leads` | GET | `days` (3), `acting_user` | `[Stale]` (§2.10) |
| `update_lead` | POST | `name`, `data`, `acting_user` | as `get_lead` |
| `set_stage` | POST | `name`, `stage`, `note?`, `acting_user` | as `get_lead` |
| `add_note` | POST | `name`, `note`, `acting_user` | the new timeline `Item` |
| `get_rate_card` | GET | `acting_user` | `RateCard` (§2.3) |
| `save_rate_card` | POST | `rows` (JSON list of `{segment, price_list, max_discount}`), `acting_user` | `RateCard` |
| `set_segment_rate` | POST | `item_code`, `rate`, `segment?` (blank: the selling price list), `acting_user` | `{item_code, segment, rate}` |
| `get_lead_ai` | GET | `name`, `acting_user` | LeadAI (§2.6) |
| `refresh_lead_ai` | POST | `name`, `acting_user` | LeadAI, after a fresh read (write on the lead; once a minute) |
| `apply_ai_stage` | POST | `name`, `acting_user` | as `get_lead`: moved to the suggested stage as the acting user |
| `dismiss_ai_stage` | POST | `name`, `acting_user` | LeadAI, without the stage suggestion |
| `draft_lead_message` | POST | `name`, `channel` (`whatsapp` / `email`), `language` (`en` / `ml`, WhatsApp), `acting_user` | `{channel, subject, text, to?, url?}`: nothing is sent |
| `log_message_sent` | POST | `name`, `channel`, `text`, `acting_user` | the new timeline `Item` (an activity by the acting user) |

### 2.1 Shapes

```
Row = {name, lead_name, company_name, email_id, mobile_no, tbo_stage, segment, lead_owner,
  owner_name, score (0-100), next_follow_up (date | null), modified,
  latest_quotation: {name, tbo_quote_status, grand_total, currency, tbo_sent_on} | null,
  latest_proposal: {name, document_type (Proposal | Quotation), status, view_count, sent_on} | null}

Lead = {name, lead_name, first_name, last_name, company_name, email_id, mobile_no, phone,
  whatsapp_no, job_title, website, source, territory, industry, segment, tbo_stage,
  status (ERPNext's own, read only), lead_owner, owner_name, tbo_score, tbo_temperature
  (Hot / Warm / Cold), tbo_score_reason, tbo_lost_reason, tbo_lost_notes, creation, modified,
  next_follow_up (date | null), deal (the Opportunity once won | null), can_write,
  can_call (the acting user may use MyOperator click-to-call)}

Item = {kind: "note" | "call" | "activity" | "meeting" | "stage" | "quotation" | "ai" |
  "proposal", at, who (user | null), who_name, text, ref (the record's name)}
  + for a call through MyOperator: {call, duration, direction, status, recording, summary}
  (telephony-myoperator.md §4.1)

Quotation = {name, docstatus, tbo_quote_status, transaction_date, valid_till, currency,
  grand_total, tbo_revision_no, tbo_is_latest, tbo_proposal (the proposal it prices), url (its
  desk form, for staff: the client only ever sees the proposal)}
```

- **Stages** are TBO's 13 pipeline stages in order (`lead-pipeline.md` §2), each with its type
  (Open, Ongoing, On Hold, Won, Lost) and colour name. `allowed_stages` is every other stage when
  the acting user may write the lead, else `[]`. Automatic moves (meetings, quotations) still
  happen on the server.
- **Segments:** Retail, Small, Medium, Large, Premium (`Lead.tbo_segment`); each has a price
  list and a discount band on the rate card (§2.3).
- **Views:** `mine` = the acting user owns it; `call_today` = their Call today list, in its
  order (`smart-leads.md` §4.3; empty while lead scoring is off); `follow_up` = leads with an
  open follow-up due today or earlier, earliest first. `total` counts the whole view; `start` /
  `page_length` page it. Other filters apply in every view.
- **Timeline:** notes (Comments), calls and logged meetings, emails and WhatsApp (Events), the
  pipeline meetings (TBO Lead Meeting, `text` "1st meeting: MOM Submitted"), stage changes
  (`text` "Contacted → Proposal Sent"), quotations, proposals (`text` "<title>: Viewed") and
  what the AI did (`kind` "ai", `who` null, `who_name` "TBO AI", `smart-leads.md` §11), newest
  first.
- **Where a lead stands with the client** (the list row): `latest_quotation` is the newest
  latest revision (`tbo_is_latest`) of the lead's quotations (`tbo_lead`), cancelled ones left
  out; `latest_proposal` the newest latest revision of its priced client documents (proposal or
  quotation document), null while Proposals is off. Two queries for the whole page.
- **Proposals:** the lead's proposals while Proposals is on (`hub-proposals-api.md`, `Row`);
  empty otherwise.

### 2.2 Writing

`data` keys (anything else is refused with 417):

| Key | Field |
| --- | --- |
| `lead_name` | split into `first_name` / `last_name` when those are not sent |
| `first_name`, `last_name`, `company_name`, `email_id`, `mobile_no`, `phone`, `whatsapp_no`, `job_title`, `website` | the Lead's own |
| `source` (a Lead Source), `territory`, `industry` (Industry Type) | links; `get_options` lists them |
| `segment` | `tbo_segment`; moving a lead from one segment to another needs a preset editor (417 otherwise; `lead-pipeline.md` §9.8) |
| `lead_owner` | another sales user: only within the acting user's reach (anyone for a Sales Manager outside the hierarchy, their team for a team lead, nobody for a salesperson); 403 otherwise |
| `note` | `create_lead` only: the lead's first note |
| `next_follow_up` | `YYYY-MM-DD`: a Sales follow-up ToDo for the lead owner that day, unless one is already open for it; an empty value adds nothing (it never closes one) |

- `create_lead` needs a name or a company. ERPNext checks the email (unique unless CRM Settings
  allow duplicates, and never the owner's own).
- `set_stage` to **Closed – Lost** needs `note`: a lost reason's name, or free text saved as
  "Other" with the text as `tbo_lost_notes`. For any other stage `note` is added as a note.
  Closed – Won makes the deal (with the lead pipeline on).
- `add_note` needs read on the lead (as the app's notes do).

### 2.3 The segment rate card

```
RateCard = {segments: [{segment, price_list (null: the selling list), max_discount (0: none)}],
  default_price_list, price_lists: [selling price list names], can_edit,
  services: [{item_code, item_name, item_group, description, uom, rate (selling list),
    rates: {segment: rate | null}}]}
```

- Every segment is listed, in order, whether or not it is on the card. `rates` has a key for
  each segment with its own price list; `null` where that list has no price for the service.
- Needs Quotations on the lead (417 otherwise). Writes need a role in "Roles that edit presets
  and prices" (403 otherwise); `can_edit` tells the hub whether to show the edit controls.
- `save_rate_card` replaces the card: a row with neither a list nor a band is dropped. Refused
  (417): an unknown segment, a segment twice, a band outside 0-100, a list that isn't selling.
- `set_segment_rate` with a segment that has no list of its own is refused (417).
- What the card does to quotations: `lead-pipeline.md` §9.8.

### 2.4 The overview

The hub's Leads → Overview. Built on the pipeline dashboard's figures
(`lead_pipeline/overview.py`, `LeadOverview` extends `PipelineDashboard`, `lead-pipeline.md` §11),
for the acting user's **sales scope**: their own leads, their sales team's (a team lead in the
TBO Sales Hierarchy), or everyone's (a Sales Manager outside the hierarchy, a System Manager).
`owner` narrows it to one person in that scope; anyone outside it is 403. The pipeline
dashboard's own switch (Pipeline dashboard) is not needed: the overview only reads.

`period`: **week** (Monday to today), **month** (the 1st to today), **quarter** (its first day to
today) or **custom** (`from_date` and `to_date`, at most two years).

```
Overview = {period, from_date, to_date, scope ("own" | "team" | "all"), people: [{user, full_name}],
  stale_days, currency,
  totals: {new, contacted, meetings, mom_pending, quotations_sent, won, won_value, lost, stale,
    overdue, hot, open, open_value, win_rate, conversion_rate},
  pipeline: [{stage, type, color, count, value}],       every stage, in pipeline order, now
  funnel: [{step: created | contacted | meeting | proposal | won, count, rate}],
  lost_reasons: [{reason, count}],
  hot: {count, rows: [{name, lead_name, organization, status, lead_score, lead_owner}]},
  by_owner: [{user, full_name, new, contacted, open, meetings_held, mom_pending, mom_rate,
    quotations, quotations_sent, won}],
  by_segment / by_source: [{value (null: none), new, open, won}]}
```

| Figure | Counts |
| --- | --- |
| `new` | leads created in the period |
| `contacted` | leads touched in the period: a note, a call or logged activity, an email, a stage change after the first, a follow-up done (the migration note is no touch) |
| `meetings` | meetings whose MOM was submitted in the period |
| `quotations_sent` | lead quotations sent in the period (`tbo_sent_on`) |
| `won` / `lost` | leads that entered Closed – Won / Closed – Lost in the period (stage log); `won_value` their latest quotations' total |
| `stale` | open leads with no touch for `lead_stale_days` (21), counted from creation when never touched |
| `overdue` | open follow-ups (ToDos) on open leads past their day, now |
| `hot` | open leads that are Hot or Positive / Interested, now |
| `open`, `open_value`, `pipeline` | leads in each stage now and their latest quotations' total (`tbo_is_latest`) |
| `funnel` | of the leads created in the period, how many reached Contacted, Meeting Scheduled, Proposal Sent, Closed – Won (from the stage log; a skipped stage counts as passed) |
| `win_rate`, `conversion_rate` | won of won + lost; created in the period and won now |

**Every number opens its leads:** `list_leads(metric=…)` with the same `period` (or dates) and
`owner` keeps exactly the leads behind a figure (dates without a `period` are a custom
range; neither is this month): `new`, `contacted`, `meetings`, `quotations`,
`won`, `lost`, `stale`, `overdue`, `hot`, `intake` (leads taken in the period, §2.4.1), and
`open` (open leads now, for the "open" counts
by owner, segment and source). Stages, owners, segments and sources open with the plain
filters (`stage`, `owner`, `segment`, `source`). A metric combines with the view and the
other filters.

#### 2.4.1 New leads taken (the daily target)

`get_lead_intake` counts the leads salespeople **took** each day: created that day, owned in the
acting user's scope (narrowed to `owner`), **leaving out leads copied in from the old CRM**
(`crm_lead_ref` set: the cutover's and the tboindia-old import's), which were not taken that
day. The hub holds the target (its HD CRM Settings → daily new-lead target) and compares.

```
Intake = {from_date, to_date,
  days: [{date, total, by_owner: [{user (null: no owner), full_name, count}]}],   every day, newest first
  people: [{user, full_name}]}       the scope's salespeople (Sales User), so someone with none shows
```

`list_leads(metric="intake", period="custom", from_date, to_date, owner?)` lists exactly the
leads a count is made of.

### 2.5 Sales activity (the hub's Sales scoreboard)

`get_sales_activity` gives each salesperson's CRM activity in the range; the hub scores it
(tbocloud/helpdesk `docs/team-dashboard.md`, Sales). **Counts and totals only, no lead is
named.** The hub's integration user may call it **without `acting_user`** and gets everyone:
the hub's Scoreboard is open to the whole team and closes its periods at night with nobody
signed in. With `acting_user` (or anyone else calling) it is that person's sales scope.

```
Activity = {user, full_name, contacted, meetings_held, moms_submitted, mom_pending,
  quotations_sent, follow_ups_done, follow_ups_on_time, follow_ups_overdue, new, won,
  won_value, conversion, calls, talk_seconds}
```

Each counts for the **lead's owner**, follow-ups for whom they are allocated to: `contacted`
(as the overview), `meetings_held` (meetings scheduled in the range that have ended, not
cancelled), `moms_submitted` (MOM submitted in the range), `mom_pending` (now),
`quotations_sent`, `follow_ups_done` (closed in the range) and `follow_ups_on_time` (closed by
their day), `follow_ups_overdue` (open past their day now), `new` (created in the range), `won`
and `won_value` (latest quotations' total), `conversion` (won of new, %). `calls` and
`talk_seconds` count for **the person who took the call**: answered MyOperator calls started in
the range and their talk time (`telephony-myoperator.md` §4.2; 0 without telephony).

### 2.5.1 Daily sales report

`get_daily_report(date?, owner?, acting_user?)` is the Sales Manager's **Daily Sales Performance
Report**: one row per salesperson for one `date` (default today, site time). `acting_user`'s sales
scope decides who is listed (a salesperson: themselves; a team lead: the team; a Sales Manager:
all Sales Users); `owner` narrows it to one person and must be in the scope (else 403). The ERP
returns counts and the leads behind them; **the hub holds the targets** (10 qualified leads a
day; 10 meetings, 5 proposals and 40 follow-ups a week; lead to meeting 30% and proposal win 40%
over 30 days) and compares.

```
DailyReport = {date, week_from (the Monday), currency,
  people: [{user, full_name,
    calls_total, calls_connected,
    fresh_leads, follow_up_leads,
    positive_leads: [{name, lead_name, company_name}],
    new_quotations: {count, value, items: [{kind ("quotation"|"proposal"), name, lead, lead_name, value}]},
    meetings_held, follow_ups_done, follow_ups_overdue,
    new_leads: [{name, lead_name, company_name}],
    week_to_date: {leads, meetings, proposals, follow_ups}}]}
```

Each figure, for the day (site timezone) and for whom it counts:

- `calls_total`: TBO Calls started that day by the person who took them, outgoing or incoming,
  answered or not. `calls_connected`: those answered (manual `log_call` calls included).
- `fresh_leads`: distinct leads whose **first contact** was that day and the person contacted
  that day. A lead's first contact is the earlier of its first TBO Call (anyone's) and its first
  move out of New Lead (its stage log). "Contacted that day" is a call by the person, a stage
  move on a lead they own (the first row of a stage log, the lead coming in, is not a move) or a
  follow-up allocated to them completed that day.
- `follow_up_leads`: the other leads they contacted that day, i.e. ones contacted before. The
  two never overlap; a lead called three times today counts once.
- `positive_leads`: leads owned by the person that entered **Positive / Interested** that day
  (the stage log), once each, named.
- `new_quotations`: what went to a client that day for the person's leads, the hub overview's
  "Proposals sent": each Quotation with `tbo_sent_on` that day (every revision; cancelled left
  out) and each TBO Proposal sent that day with no quotation behind it (value 0; a proposal
  with a quotation is that quotation). `count`, `value` (base currency) and the `items`.
- `meetings_held`, `follow_ups_done`, `follow_ups_overdue`: as the scoreboard (§2.5) for that
  day: meetings scheduled that day that have ended and are not cancelled; follow-ups completed
  that day (by allocation); follow-ups still open and past their day **now** (not a count for
  that day).
- `new_leads`: leads created that day with the person as owner, **leaving out leads copied in
  from the old CRM** (`crm_lead_ref` set), named. The same rule as `get_lead_intake` (§2.4.1).
- `week_to_date`: Monday of `date`'s week to `date`: `leads` (new, as above), `meetings`
  (held), `proposals` (as `new_quotations`) and `follow_ups` (done).

Everything is read in grouped queries for the whole scope (`lead_pipeline/daily_report.py`),
never one query per lead.

### 2.6 AI runs leads

Behind TBO Smart Settings → AI runs leads (`smart-leads.md` §11). `get_lead_ai` answers with
the switch off too (`enabled` false; health and duplicates still come).

```
LeadAI = {enabled, ai_ready, open, at (last read | null), stale (the lead changed since),
  score: {score, temperature, reason} | null (lead scoring off),
  insight: {summary, sentiment (Positive | Neutral | Negative), intent (High | Medium | Low | None),
    signals: [text], next_action: {what, why, channel (call | whatsapp | email | meeting), due} | null,
    suggested_stage | null, stage_reason | null,
    message: {channel (whatsapp | email), subject, text} | null} | null (never read),
  health: {status (healthy | watch | at_risk), signals: [{key, state, text}]} | null (closed),
  duplicates: [{name, lead_name, company_name, tbo_stage, lead_owner, owner_name,
    match: ["phone" | "email" | "company"]}],
  contact: {whatsapp (digits with the country code, for wa.me) | null, email | null}}
```

`get_overview` adds `ai`: `{enabled: false}`, or `{enabled, ai_ready, plan: {user, rows:
[{name, lead_name, company_name, tbo_stage, score, temperature, why, next_action, next_due,
intent}]}, high_intent, unhappy, stage_suggested, read}` for the scope (the plan is the
`owner` picked, else the acting user's). `high_intent`, `unhappy` and `stage_suggested` are
also `list_leads` metrics (open leads the AI read so, now; none while AI runs leads is off).

### 2.7 Calls (MyOperator)

Click-to-call, the recording player and the telephony settings are in
`tbo_smart.api.telephony` (`telephony-myoperator.md` §4), with the same `acting_user` rule.

**`log_call(lead, outcome, note?, acting_user?)`** (POST) logs a call made outside MyOperator
(MyOperator is not live everywhere yet). `outcome` is `Connected`, `No answer`, `Busy` or
`Voicemail`; it needs write permission on the lead. It creates an **outgoing TBO Call** for the
acting user (the one source of calls) and its call Event on the lead's timeline, and returns
`{name, status, outcomes}`: `Connected` is stored as Answered, `No answer` and `Busy` as Missed,
`Voicemail` as Voicemail, with `call_id` `manual-<random>`, no duration and no recording. It
works with telephony switched off. A connected manual call counts in the Scoreboard's `calls`
(with no talk time).

### 2.8 Proposals and money

`get_proposal_money` is the hub overview's "Proposals & money" (`lead_pipeline/money.py`,
`ProposalMoney` extends `LeadOverview`): the same scope, `owner` and `period` as `get_overview`.
Amounts are in the company currency (`currency`, base amounts).

```
Money = {period, from_date, to_date, currency,
  totals: {proposals_sent, proposals_value, unpriced_sent, won, won_value, advances,
    to_collect, overdue},
  by_month: {overdue, months: [{month (its 1st), amount}] (this month and the next 5), later,
    undated},
  splits: [Split] (by due date, undated last; at most 300), splits_total}

Split = {lead, lead_name, company_name, lead_owner, owner_name,
  document_type (Sales Order | Quotation), document, quote_status (a quotation's), idx,
  label (payment term, description or milestone; null: the whole amount), percent, amount,
  received, outstanding, due_date (null: on a milestone), source (schedule | quotation | proposal | whole),
  status (Received | Due | Overdue | Upcoming)}
```

| Figure | Counts |
| --- | --- |
| `proposals_sent` | quotations sent in the period (`tbo_sent_on`), every revision: the overview's "Proposals sent" |
| `proposals_value` | their total, each lead once (the last one it was sent in the period) |
| `unpriced_sent` | proposals sent in the period with no quotation: no value, so not in the two above |
| `won`, `won_value` | as the overview: leads won in the period, their latest quotations' total |
| `advances` | customer receipts (Payment Entry, Receive, submitted) allocated to the scope's Sales Orders, posted in the period |
| `to_collect`, `overdue`, `by_month` | the splits' outstanding now (not the period): overdue, by due month, later and undated |

**Which documents:** a lead's submitted Sales Orders (through the deal made from it,
`Sales Order.opportunity`, or a line from its quotation, `Sales Order Item.prevdoc_docname`)
while one of their splits is outstanding; a lead with no order its newest latest-revision
quotation that is with the client or accepted (`tbo_quote_status`).

**Splits:** the document's Payment Schedule when a person set one (a payment term on a row,
or more than one row). ERPNext gives every quotation and order a schedule; one 100% row with
no payment term is its own filler. ERPNext also doesn't carry a quotation's payment terms
template to the Sales Order it makes (`field_no_map`), so an order with only the filler takes
its quotation's schedule (the quotation of its first line), as shares of the order's total
(`source` quotation). Without either, the Payment Terms milestones ("Advance on signing |
30%") of the newest proposal or quotation document pricing that quotation (an order: the
quotation of its first line) split the total, **undated**; without them the whole amount is one
split (an order's dated by its schedule, a quotation's undated).

**Received:** the larger of the order's `advance_paid` and what its invoices have been paid
(each invoice's paid share, split over its orders by their lines), settling the splits in due
order. ERPNext fills Payment Schedule `paid_amount` only when a receipt names the payment
term, so it is not used. An advance and an invoice paid separately on one order count as the
larger of the two.

**Status:** Received (nothing outstanding); Overdue (an order's split past its date); Due (on
or before a week from today; a quotation's split past its date is expected now, never
overdue: the client hasn't committed); Upcoming (later, or no date).

### 2.9 Duplicates (`lead-departments.md` §6)

```
Match = {name, lead_name, company_name, email_id, mobile_no, lead_owner, owner_name, tbo_stage,
  matched_on: ["email" | "phone" | "company", ...]}
```

`find_duplicate_leads` returns at most 5, email and phone matches first, then the newest. Email is
compared lowercase, phone by its last 10 digits (7 at least) against the lead's mobile, phone and
WhatsApp numbers, company case-insensitive and exact. It searches **every lead**, whatever the
acting user may see, and returns only the fields above: it is there to stop double work, so a
salesperson must learn that a colleague owns the enquiry.

`create_lead` first runs the same search with the new lead's `email_id`, `mobile_no` (or `phone`)
and `company_name`. An email or phone match refuses the call with **`DuplicateEntryError`
(HTTP 409)**, message `Possible duplicate of <lead> (<owner name>)`, and the matches in the
response's `matches` (a `[Match]`). A company-only match never blocks. Pass `allow_duplicate=1` to
create anyway. With the owner left empty in a **Round robin** department the lead goes to the
department's next member (`lead-departments.md` §5); otherwise the owner is the acting user as
before.

### 2.10 Stale leads (`lead-departments.md` §7)

```
Stale = {name, lead_name, company_name, lead_owner, tbo_department, tbo_stage,
  last_activity_on (datetime), idle_days (whole days)}
```

The leads the acting user may see (their normal scope) that are open (not Closed – Won, Closed –
Lost or On Hold) and have been quiet for `days` or more, longest quiet first. A department's
`stale_after_days` (when above 0) replaces `days` for its leads.

## 3. Links back to the hub

Teams messages, emails and the morning digest link to the lead page when TBO Smart Settings →
**Lead page URL** is set (the hub's page, e.g. `https://support.tbocloud.in/helpdesk/leads`):
a lead opens at `<url>/<lead name>`, Call today at `<url>?view=call_today`. Blank: the desk.

## 4. Tests

`tbo_smart/tests/test_lead_overview.py`: the periods; the overview follows the acting user's
scope and owner filter; totals, the pipeline's value, the funnel and the breakdowns; every
metric opens exactly its leads, alone and with a view; the sales activity for the integration
user (everyone) and a salesperson (themselves only).

`tbo_smart/tests/test_lead_money.py`: the intake by day and owner without imports, its scope
and owner filter and the `intake` metric; a list row's latest quotation and proposal; a sent
proposal split by its milestones; an order's schedule settled by a receipt (received, upcoming,
overdue, paid off), the advances and the months.

`tbo_smart/tests/test_hub_leads.py`: the integration user must name an enabled sales user
(unknown, disabled and non-sales users refused); anyone else acts only as themselves; lists and
reads follow the acting user; writes are theirs (owner, lead owner, note author) and the session
returns to the integration user; field, owner and lost-reason rules; the follow-up view; the
options. `tests/test_segment_rate_card.py`: the rate card methods (acting user, editors only).

`tbo_smart/tests/test_lead_wave1.py`: see `lead-departments.md` §9.

`tbo_smart/tests/test_daily_report.py`: the daily report's calls, fresh and follow-up leads,
positive leads, quotations, meetings and follow-ups, new leads without imports, the week to date
and the scope (a salesperson sees themselves, a Sales Manager everyone, `owner` outside the scope
is 403); `log_call` (the outcomes, the acting user, the lead's write permission).

## Changelog

- **v1.10 (2026-10-29):** `find_duplicate_leads` and `get_stale_leads` (§2.9, §2.10);
  `create_lead`'s `allow_duplicate` and its duplicate refusal; departments carry `assignment`
  (`Manual` / `Round robin`) and `stale_after_days` in `get_lead_departments`,
  `save_lead_department` and `get_options`' `departments` .
- **v1.9 (2026-10-28):** lead departments (`lead-departments.md` §4): `get_lead_departments`,
  `save_lead_department`, `set_lead_department_active`, `get_options`' `departments` and
  `lead_types`, `list_leads`' `department`, the lead's `tbo_department` and `tbo_lead_type`.
- **v1.8 (2026-10-26):** `get_daily_report` (§2.5.1) and `log_call` (§2.7).
- **v1.7 (2026-10-24):** `get_lead_intake` and the `intake` metric (§2.4.1),
  `get_proposal_money` (§2.8), the row's `latest_quotation` and `latest_proposal`.
- **v1.6 (2026-10-20):** a quotation carries `tbo_proposal` and its desk `url`.
- **v1.5 (2026-10-19):** call timeline items carry MyOperator's call fields, `lead.can_call`
  and `get_options`' `can_call`,
  and `get_sales_activity`'s `calls` and `talk_seconds` (`telephony-myoperator.md`).

- **v1.4 (2026-10-19):** `get_lead` returns `proposals` and `proposal` timeline items
  (`hub-proposals-api.md`).
- **v1.3 (2026-10-19):** AI runs leads (§2.6): `get_lead_ai`, `refresh_lead_ai`,
  `apply_ai_stage`, `dismiss_ai_stage`, `draft_lead_message`, `log_message_sent`,
  `get_overview`'s `ai`, the timeline's `ai` items.
- **v1.2 (2026-10-19):** `get_overview` and `get_sales_activity`; `list_leads` takes `source`,
  `temperature` and `metric` with its period.
- **v1.1 (2026-10-19):** `get_rate_card`, `save_rate_card`, `set_segment_rate`.
- **v1.0 (2026-10-16):** first version.
