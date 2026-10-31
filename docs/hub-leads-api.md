# Hub leads API (`tbo_smart.api.hub_leads`), v1.18

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
| `get_options` | GET | `acting_user` | `{stages: [{name, label, type, color}], sources: [], segments: [], departments: [{name, lead_type, is_active, assignment, stale_after_days, call_batches_enabled, is_head, is_member}], lead_types: [], owners: [{email, full_name}], territories: [], industries: [], org_sizes: [], states: [], kerala_districts: [], districts_by_state: {state: [district]}, can_call}` (§2.12) |
| `list_leads` | GET | `search?`, `stage?`, `owner?`, `segment?`, `source?`, `temperature?` (Hot / Warm / Cold), `industry?`, `state?`, `district?`, `org_size?` (each a value or a list, §2.14), `last_call_result?` (a value or a list, §2.16), `metric?` with `period?` / `from_date?` / `to_date?` (§2.4), `view` (`all` / `mine` / `call_today` / `follow_up`, default `all`), `start` (0), `page_length` (20, at most 100), `acting_user` | `{rows: [Row], total}` |
| `get_overview` | GET | `period` (`week` / `month` / `quarter` / `custom`; default `month`, or `custom` when dates are sent), `from_date?`, `to_date?` (custom), `owner?`, `acting_user` | Overview (§2.4) |
| `get_lead_intake` | GET | `from_date?`, `to_date?` (default today, at most 62 days), `owner?`, `acting_user` | Intake (§2.4.1) |
| `get_proposal_money` | GET | `period`, `from_date?`, `to_date?`, `owner?` (as `get_overview`), `acting_user` | Money (§2.8) |
| `get_sales_activity` | GET | `from_date?`, `to_date?` (default this month), `acting_user?` (§2.5) | `{from_date, to_date, currency, people: [Activity]}` |
| `get_lead` | GET | `name`, `acting_user` | `{lead: Lead, timeline: [Item], quotations: [Quotation], proposals: [Proposal row], allowed_stages: [stage name]}` |
| `create_lead` | POST | `data`, `allow_duplicate?` (1 to create past a duplicate, §2.9), `acting_user` | as `get_lead` |
| `find_duplicate_leads` | GET | `email?`, `phone?`, `company?`, `exclude?` (a lead name), `acting_user` | `[Match]` (§2.9) |
| `get_category_counts` | GET | `acting_user` | `{industry, state, district, org_size, last_call_result}`, each `{value: leads}` (§2.14, §2.16) |
| `add_industry` | POST | `name`, `acting_user` | `{name, created}` (§2.14) |
| `bulk_set_lead_category` | POST | `leads` (list of names), `values`, `acting_user` | `{updated, skipped: [{name, reason}]}` (§2.14) |
| `get_stale_leads` | GET | `days` (3), `acting_user` | `[Stale]` (§2.10) |
| `update_lead` | POST | `name`, `data`, `acting_user` | as `get_lead` |
| `get_lead_photos` | GET | `lead`, `acting_user` | `[Photo]` (§2.11), newest first |
| `upload_lead_photo` | POST | `lead`, `filename`, `content` (base64), `caption?`, `acting_user` | the new `Photo` |
| `delete_lead_photo` | POST | `lead`, `file` (a Photo's `name`), `acting_user` | `{deleted: file}` |
| `get_lead_photo` | GET | `lead`, `file`, `thumb?` (1: a small JPEG), `acting_user` | the image bytes (`Content-Type` image/png, image/jpeg or image/webp), not JSON |
| `set_stage` | POST | `name`, `stage`, `note?`, `acting_user` | as `get_lead` |
| `log_call` | POST | `lead`, `outcome`, `note?`, `callback_at?` (Call back), `acting_user` | `{name, status, outcomes, stage}` (§2.7, §2.16) |
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
| `get_my_calls` | GET | `acting_user` | MyCalls (§2.13) |
| `get_next_batch` | POST | `department?`, `industry?`, `state?`, `district?`, `acting_user` | `{created, reason, batch?, slot?, size?}` (§2.13) |
| `record_call_outcome` | POST | `batch_item`, `outcome`, `note?`, `callback_at?`, `acting_user` | `{item, outcome, retry_at}` (§2.13) |
| `get_call_batches_report` | GET | `date?` (default today), `acting_user` | `{date, people: [Person], totals}` (§2.13) |

### 2.1 Shapes

```
Row = {name, lead_name, company_name, email_id, mobile_no, tbo_stage, segment, lead_owner,
  industry, state, district, org_size, owner_name, score (0-100), next_follow_up (date | null), modified,
  latest_quotation: {name, tbo_quote_status, grand_total, currency, tbo_sent_on} | null,
  latest_proposal: {name, document_type (Proposal | Quotation), status, view_count, sent_on} | null}

Lead = {name, lead_name, first_name, last_name, company_name, email_id, mobile_no, phone,
  whatsapp_no, job_title, website, source, territory, industry, state, district, org_size, segment, tbo_stage,
  status (ERPNext's own, read only), lead_owner, owner_name, tbo_score, tbo_temperature
  (Hot / Warm / Cold), tbo_score_reason, tbo_lost_reason, tbo_lost_notes, creation, modified,
  next_follow_up (date | null), deal (the Opportunity once won | null), can_write,
  can_call (the acting user may use MyOperator click-to-call),
  place: {google_profile_url, maps_url, latitude, longitude, location_note} (each null while
  empty, §2.11), photo_count}

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
| `state`, `district`, `org_size` | the categories (§2.14): `state`, `tbo_district`, `no_of_employees`; an empty value clears one |
| `segment` | `tbo_segment`; moving a lead from one segment to another needs a preset editor (417 otherwise; `lead-pipeline.md` §9.8) |
| `lead_owner` | another sales user: only within the acting user's reach (anyone for a Sales Manager outside the hierarchy, their team for a team lead, nobody for a salesperson); 403 otherwise |
| `note` | `create_lead` only: the lead's first note |
| `google_profile_url`, `maps_url`, `latitude`, `longitude`, `location_note` | the lead's place (`tbo_google_profile_url`, `tbo_maps_url`, `tbo_latitude`, `tbo_longitude`, `tbo_location_note`; rules in §2.11); an empty value clears it |
| `next_follow_up` | `YYYY-MM-DD`: a Sales follow-up ToDo for the lead owner that day, unless one is already open for it; an empty value adds nothing (it never closes one) |

- `create_lead` needs a name or a company. ERPNext checks the email (unique unless CRM Settings
  allow duplicates, and never the owner's own).
- `set_stage` to **Closed – Lost** needs `note` (Not Interested and Wrong Number need none, §2.16): a lost reason's name, or free text saved as
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
| `meetings` | meetings held in the period: a MOM submitted, or the lead entering First / Second / Third Meeting Completed (stage log). A lead moved there by hand or imported has no meeting record, so the MOM alone read 0 |
| `quotations_sent` | leads a proposal went to in the period, each once: a quotation sent (`tbo_sent_on`), a proposal document sent (`sent_on`), or the lead entering Proposal Sent (stage log) |
| `won` / `lost` | leads that entered Closed – Won / Closed – Lost in the period (stage log); `won_value` their worth: the latest quotations' total, else the deal's amount, else its Sales Orders' total; `won_unpriced` counts won leads with none of them |
| `stale` | open leads with no touch for `lead_stale_days` (21), counted from creation when never touched |
| `overdue` | open follow-ups (ToDos) on open leads past their day, now |
| `hot` | open leads that are Hot or Positive / Interested, now |
| `open`, `open_value`, `pipeline` | leads in each stage now and their latest quotations' total (`tbo_is_latest`) |
| `funnel` | of the leads created in the period, how many reached Contacted, a meeting held (First Meeting Completed or later), Proposal Sent, Closed – Won (from the stage log; a skipped stage counts as passed) |
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

**`log_call(lead, outcome, note?, callback_at?, acting_user?)`** (POST) logs a call made outside
MyOperator (MyOperator is not live everywhere yet). `outcome` is `Connected`, `Not answered`,
`Busy`, `Call back` (needs `callback_at`), `Not interested` or `Wrong number`, the call batch
outcomes; the older `No answer` and `Voicemail` still work and read as Not answered. It needs
write permission on the lead. It creates an **outgoing TBO Call** for the acting user (the one
source of calls) and its call Event on the lead's timeline, and returns
`{name, status, outcomes, stage}`: Connected, Call back and Not interested are stored as
Answered, Not answered, No answer, Busy and Wrong number as Missed, `Voicemail` as Voicemail,
with `call_id` `manual-<random>`, no duration and no recording. `stage` is the stage the call
moved the lead to, else `null` (§2.16). It works with telephony switched off. A connected manual
call counts in the Scoreboard's `calls` (with no talk time).

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
| `proposals_sent` | leads a proposal went to in the period, each once (the overview's "Proposals sent") |
| `proposals_value` | the total of the quotations sent in the period, each lead at the last one |
| `unpriced_sent` | leads sent a proposal in the period without a quotation sent: no value, so not in `proposals_value` |
| `won`, `won_value` | as the overview: leads won in the period and their worth |
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

### 2.11 Place and photos

```
Photo = {name (the File), file_name, file_url (private: not loadable by the hub), file_size,
  caption | null, uploaded_by (user), uploaded_by_name, uploaded_on (datetime)}
```

For whoever goes to meet the client (`lead_place.py`; the Lead's **Place** section holds the
fields, saved through `update_lead`/`create_lead` or the desk form alike):

- `google_profile_url`: the client's Google Business Profile. `maps_url`: a Google Maps place or
  pin link. Both are https links to Google only (a scheme-less link gets `https://`); anything else,
  or a link with a user name in it, is 417. Profile: `maps.app.goo.gl`, `g.page`,
  `business.google.com`, `share.google`, `goo.gl/maps…`, or `google.<tld>/maps…`. Map link:
  `maps.app.goo.gl`, `goo.gl/maps…` or `google.<tld>/maps…`. At most 500 characters.
- `latitude` (-90 to 90), `longitude` (-180 to 180): the pin, from a map or the phone's GPS. Both
  or neither; 0 and 0 means none. 417 outside the range. `location_note`: how to find it, at most
  500 characters.
- **Photos** are private Files attached to the Lead and marked `tbo_lead_photo` (so a scanned
  business card on the same lead is never listed). PNG, JPEG or WebP, told by the bytes, at most
  **10 MB** each and **20 per lead**; anything else is 417 (HEIC with its own message: the hub
  resizes to JPEG in the browser before it uploads). `caption` is cut to 140 characters.
- **Permissions:** `get_lead_photos` and `get_lead_photo` need read on the lead,
  `upload_lead_photo` and `delete_lead_photo` write. Delete is further limited to who added the
  photo, the lead's owner, or a Sales Manager / System Manager (403). A `file` that isn't a photo
  of this lead is 404.
- `get_lead_photo` answers with the bytes (`thumb=1`: a JPEG of at most 480 px, turned upright);
  the hub streams them to the person's browser with private cache headers.
- `get_lead` returns the place and `photo_count`; the list `Row` has neither.
- Needs `bench migrate` (Lead's Place section; File's `tbo_lead_photo` and `tbo_caption`).

### 2.12 Business overview (management only)

`get_business_overview(from_date, to_date, department=None, include_profit=0, acting_user)`
(GET, `lead_pipeline/business.py`): the company-wide figures for a management meeting. **Who:**
System Manager or the **TBO Management** role (created by the migrate; nobody has it until the
owner grants it). Anyone else, a Sales Manager included, gets `PermissionError`. After the check
the figures are **not** narrowed to the caller's sales scope. `get_options` returns
`can_view_business` for the acting user, which the hub uses to show the tab. The acting user
may hold TBO Management without any sales role.

```
Business = {from_date, to_date, department, currency,
  pipeline: {count, value, unpriced, weighted (false: lead stages have no probability, so the
    value is unweighted), stages: [{stage, color, count, value}]},     open leads now
  proposals: {sent: {count, value, unpriced}, accepted: {count, value}, declined, acceptance_rate},
  onboarding: {won: {count, value, unpriced}, projects_started},
  revenue: {invoiced, collected, outstanding, months: [{month, invoiced, collected}]},
  funnel: [{step, count, rate}], new_leads, meetings_held,
  trend: [{month, new, won, invoiced, collected}]   the six months up to `to_date`,
  top_clients: [{customer, name, invoiced}] (5),
  by_department: [{department (null: none), label, open, open_value, won, won_value, invoiced,
    collected}],
  profit: {available, reason, income, expense, profit}}
```

| Figure | Counts |
| --- | --- |
| `pipeline` | open leads by stage now; value = latest quotations' total (`unpriced` leads have none) |
| `proposals.sent` | as §2.4 `quotations_sent`; `value` the worth of those leads |
| `proposals.accepted` / `declined` | leads whose Proposal / Quotation document was accepted / declined in the period (`decided_on`); `acceptance_rate` = accepted / sent in the same period (a period ratio, not a cohort) |
| `onboarding.won` | leads that entered Closed – Won in the period, with their worth (§2.4); `unpriced` won without any value |
| `onboarding.projects_started` | hub projects linked on a client document (`hub_project`) whose document changed in the period: the link has no date of its own |
| `revenue.invoiced` | submitted Sales Invoices posted in the period, `base_grand_total`, credit notes net off (negative) |
| `revenue.collected` | submitted customer Payment Entries (Receive) posted in the period, `base_received_amount` |
| `revenue.outstanding` | the submitted invoices' outstanding amounts **now** (not the period) |
| `by_department` | leads by `tbo_department`; money traced through the Sales Order's deal or quotation line to the lead. What cannot be traced is "Not linked to a lead" (the company-wide totals include it) |
| `profit` | with `include_profit=1`: ledger income minus expense for the period (no period closing vouchers, no cancelled entries). `available: false` with a `reason` when not requested or when `department` is set |

With `department`, the lead figures, invoices, receipts and outstanding are narrowed to it.

### 2.13 Today's calls (`call-batches.md`)

```
MyCalls = {enabled, reason?,            # enabled false: none of the person's departments hands out batches
  date, batch: {name, slot, department, starts_at, ends_at, seconds_left, batches: [name]} | null,
  counts: {size, called, connected, pending, scheduled, overdue},   # size/called/connected: the running batches
  today: {size, called, connected},                                  # all of today's batches
  pending: [Call], scheduled: [Call], overdue: [Call],
  can_call, next_batch: {allowed, reason}, next_slot: {label, starts_at} | null}
Call = {item, batch, slot, lead, priority (1-5), reason, is_repeat, retry_at, attempts, batch_ends_at,
  summary: {name, lead_name, company_name, industry, district, phone, stage, temperature,
            last_note, next_step, wrong_number}}
Person = {user, user_name, department, size, called, connected, overdue, batches: [{name, slot,
  status, starts_at, ends_at, size, called, connected, overdue}]}
```

`pending` are due now (best priority first), `scheduled` come back later in the running batch
(a retry or a call back), `overdue` belong to a slot that is over (kept 7 days, still callable).
`record_call_outcome` takes an `item` from these lists; `outcome` is `Connected`, `Not answered`,
`Busy`, `Call back` (`callback_at` required, a date-time), `Not interested` or `Wrong number`.
It logs the call as `log_call` does and removes the item from the queue. Not answered and Busy
come back once, two hours later, while the batch still runs. `get_next_batch` answers
`{created: false, reason}` (HTTP 200) while calls are pending, overdue calls pass the department's
`overdue_block_limit`, the daily cap is reached or no slot is open. `get_call_batches_report`
is for Sales Managers, System Managers and department heads (their departments); `connected`
counts answered calls (Connected, Call back, Not interested). The batch rules are part of
`get_lead_departments` / `save_lead_department` (below).

`save_lead_department` `values` also take: `call_batches_enabled`, `daily_call_cap`,
`pull_unassigned`, `overdue_block_limit`, `campaign_industry`, `campaign_state`,
`campaign_district`, `call_slots` (`[{label, start_time "HH:MM", end_time, size}]`, no overlaps;
empty: Morning 10:00-13:00 and Afternoon 14:00-18:00, 50 each) and `member_caps` (`{user: cap}`).
`get_lead_departments` returns the same keys.

### 2.14 Categories: industry, state, district, organisation size

Four fields say what kind of client a lead is. Three are ERPNext's own Lead fields, reused (no
duplicates); only district is new (`lead_pipeline/lead_category.py`, the field in `setup.py`).

| API key | Lead field | Kind |
| --- | --- | --- |
| `industry` | `industry` | Link to Industry Type. Seeded on migrate (idempotent, existing ones kept): Builders & Developers, Real Estate Companies, Resorts, Hotels & Restaurants, Ayurvedic Hospitals, Hospitals & Clinics, Schools & Colleges, Retail & Supermarkets, Manufacturing, Trading & Distribution, Logistics, IT & Software, Travel & Tourism, Automobile, Jewellery, Textiles & Garments, Construction Materials, Healthcare & Pharma, NGOs & Trusts, Government, Other. Only these Industry Types are accepted (417 otherwise); add more with `add_industry`. |
| `state` | `state` (Data) | Free text. `get_options.states` is the canonical list: the 28 Indian states and 8 UTs, then the UAE emirates (Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah), Saudi Arabia, Qatar, Oman, Bahrain and Kuwait. |
| `district` | `tbo_district` (Data, new) | Free text. `get_options.kerala_districts` are the 14 Kerala districts, as suggestions only. |
| `org_size` | `no_of_employees` (Select) | ERPNext's bands, used as they are: `1-10`, `11-50`, `51-200`, `201-500`, `501-1000`, `1000+` (`get_options.org_sizes`). They fit companies of TBO's size range; a different band set would be a Property Setter on that field. Anything else is 417. ERPNext gives the field no empty choice, so every lead saved without one held `1-10`; migrate adds a leading empty option (Property Setter) so a new lead has none, and the patch `clear_default_org_size` clears `1-10` on all leads while no lead holds any other band (then it was only the default). |

- **Cleaning:** nothing is refused for being unusual. State and district are trimmed, inner spaces
  collapsed, matched to the list ignoring case (`kerala` is `Kerala`; old names such as Orissa
  become Odisha), and Title Cased when typed all lower or all upper; any other value is kept.
  The same cleaning applies to the `list_leads` filters.
- **`get_options`** adds `industries`, `org_sizes`, `states`, `kerala_districts` and
  `districts_by_state` (`{state: [district, most used first]}` over the leads the acting user
  may see; leads with a district but no state are under `""`).
- **`list_leads`** takes `industry`, `state`, `district` and `org_size`: each a value, a list or
  a JSON list (a leading `[`), meaning any of them; different fields are all required. They
  combine with every other filter and view, and with `metric` (so an overview number drills down
  by category). Rows and `get_lead` return `industry`, `state`, `district`, `org_size`.
- **`get_category_counts`** gives, per field, how many of the acting user's leads have each
  value (leads without one are left out): four grouped queries.
- **`add_industry(name)`:** System Manager or Sales Manager (403 otherwise). A name that exists
  in any case returns that one with `created: false`.
- **`bulk_set_lead_category(leads, values)`:** System Manager or Sales Manager. `values` holds
  any of `industry`, `state`, `district`, `org_size`; only those are set and `""` clears one.
  Up to 500 leads; one the acting user may not write, or that does not exist, is in `skipped`
  (`not allowed` / `missing`) rather than failing the call. It writes the fields directly (the
  lead's modified time moves; no stage rules run).
- **Overview and dashboards do not take these filters.** Their figures are built per owner and
  period in several grouped queries (`overview.py`); filtering every one would not be small.
  `list_leads(metric=…, industry=…)` does narrow the leads behind a number.

### 2.15 Find prospects (`prospecting.md`)

```
Run = {name, industry, state, districts: [str], provider, department, max_results, require_phone,
  status ("Draft" | "Estimated" | "Running" | "Done" | "Failed"),
  estimated_cost (USD), expected_count, estimate_note, actual_cost (USD),
  count_found, count_duplicates, count_skipped, count_created,
  started_at, finished_at, creation, owner, owner_name}
RunDetail = {...Run, log, leads: [{name, company_name, phone, website, city, stage, owner, why_call}]}   # up to 200
Prospecting = {providers: [{key, label, paid, available, coverage}], default_department,
  max_results, max_districts, monthly_cap, month_spend}      # USD
Prospect = {why_call: str | null, signals: {...}, read_on}   # on get_lead's lead, null until read
```

`get_options` returns `can_find_prospects` and `prospecting`: true and the providers (`available`
is false while a provider is switched off or lacks its login or key) for System Managers, Sales
Managers and department heads while TBO Smart Settings → Prospecting → **Enable Find prospects**
is on; otherwise false and `null`. Everything below is 403 (`PermissionError`) for anyone else; a
head acts for their own departments only.

| Method | Does |
|---|---|
| `estimate_prospect_run(values, acting_user)` POST | Creates a run (or, with `name` in `values`, a draft again) and prices it. `values`: `industry` (an Industry Type), `state`, `districts` (list or lines, at most `max_districts`), `provider` (`OpenStreetMap`, `DataForSEO` or `Serper`), `max_results` (1 to `prospecting.max_results`), `department` (active; blank: the default), `require_phone` (default 1). Nothing is searched or bought. OpenStreetMap counts what the map holds (`expected_count`, capped at `max_results`); a paid provider gives its price and `max_results` as the upper bound. A paid run that would pass the monthly cap is refused here. Returns the Run, Estimated. |
| `start_prospect_run(name, acting_user)` POST | Only an Estimated run. Checks the cap again, sets Running and queues the job. Returns the Run. |
| `get_prospect_runs(acting_user)` | The newest 50 runs the user may see (managers all, a head their departments'). |
| `get_prospect_run(name, acting_user)` | RunDetail. Poll it while Running: the counts grow district by district. |

A finished run's leads have no owner (`lead_owner` empty) in the run's department, source
`Prospecting – <provider>`, and read for signals shortly after: `get_lead`'s `lead.prospect` is
`{why_call, signals: {has_website, online_booking, booking_tool, whatsapp, email, phone, social,
hiring, tech, software, last_updated_year, https} | {has_website: false} | {unreadable: true},
read_on}`. `get_lead`'s `lead` no longer carries `tbo_web_profile`.

### 2.16 Call outcomes: stages and the last call

Three stages say what a call told the salesperson (`lead_pipeline/statuses.py`, `get_options`'
`stages` carries them with their type and colour):

| Stage | Type | Colour | Meaning |
| --- | --- | --- | --- |
| Not Responding | Ongoing | orange | Called, not answered, busy or switched off: call again. Open, callable, in Call today, batches and stale leads |
| Not Interested | Lost | red | Picked up and declined. `tbo_lost_reason` is set to "Not interested" |
| Wrong Number | Lost | gray | Invalid or wrong number. `tbo_lost_reason` "Wrong number" and `tbo_wrong_number` set |

Lost-type stages count as lost everywhere (funnel, win rate, business, lost reasons) and leave
the open lists. Setting Not Interested or Wrong Number by hand (`set_stage`, any `note` is
added as a note) needs no reason; Closed – Lost still does.

**One helper, three callers.** `lead_pipeline/call_outcomes.apply_call_outcome` is used by
`log_call`, a call-batch outcome (and the batch sync) and a MyOperator call (outgoing, or
incoming and answered; a missed incoming call says nothing about the lead). It sets the lead's
**`last_call_result`** (`Connected`, `Not answered`, `Busy`, `Call back`, `Not interested`,
`Wrong number`) and **`last_call_on`**, and, with the lead pipeline on, moves the stage:

| Outcome | From | To |
| --- | --- | --- |
| Not answered, Busy (also MyOperator missed, voicemail) | New Lead, Contacted, Not Responding | Not Responding |
| Connected (MyOperator answered) | New Lead, Not Responding | Contacted |
| Call back (`callback_at` makes the follow-up) | New Lead, Contacted, Not Responding | Follow-up Required |
| Not interested | any stage before the first meeting | Not Interested |
| Wrong number | any stage before the first meeting | Wrong Number |

A lead at Meeting Scheduled or later, and a Won or Lost one, keeps its stage (the BD moves it
by hand); its last call is still recorded. A call older than the lead's last call changes
nothing, so a call logged by hand and then synced is counted once. Each move is a system save
with a timeline comment, so the stage log keeps it.

- **Rows and `get_lead`** return `last_call_result` and `last_call_on` (`null` when never
  called).
- **`list_leads`** takes `last_call_result`: a value, a list or a JSON list, any of them;
  another value is refused (400). It combines with every other filter and view.
- **`get_category_counts`** adds `last_call_result`: `{outcome: leads}` over the leads the
  acting user may see (never-called leads are left out).

### 2.17 Outreach: first messages and replies (`prospecting.md` §10, §11)

Methods are in **`tbo_smart.api.hub_outreach`** (same auth, `acting_user` and 403 rules as §1).
All need TBO Smart Settings → Outreach → **Enable Outreach** (otherwise 417 "Outreach is switched
off") and, for each message, write permission on its lead. **Nothing sends without
`approve_outreach`.**

```
Message = {name, lead, lead_name, company_name, lead_stage, unsubscribed, last_reply_class,
  department, channel ("Email" | "WhatsApp"), status ("Draft" | "Approved" | "Sent" | "Failed" |
  "Cancelled"), subject, body, recipient, drafted_by ("AI" | "User"), owner, owner_name,
  approved_by, approved_on, sent_on, error, communication, prospect_run, batch,
  url (WhatsApp: https://wa.me/<number>?text=<encoded body>, else null),
  queued (an Approved email waiting for the window or the cap), creation, modified}
Result = {name, status, message}      # status null: not found or no permission
```

| Method | Does |
|---|---|
| `draft_outreach(channel, leads?, run?, acting_user)` POST | `channel` `Email` or `WhatsApp` (any case); `leads` a list (or JSON list) or `run` a prospect run (its leads). Checks each lead now, then drafts the eligible ones in the background (`autopilot.draft_message`, §2.6: for a lead Find prospects created and nobody has written to, from its website signals and Why call line). Returns `{batch, queued, skipped: [{lead, lead_name, reason}]}`. Skipped: not found, the user cannot edit it, a first message is already in the outbox (Draft, Approved or Sent) on that channel, unsubscribed, Lost-type stage, no address for the channel, or over **Drafts per request** (default 50, at most 200). 417 when the AI is not set up. |
| `get_outbox(status?, batch?, run?, acting_user)` | `status` one value, a list or a JSON list. The user's own messages, a department head's department's too, managers all; newest 200. Returns `{enabled, email_ready (an outgoing Email Account exists), rows: [Message], counts: {status: n}, cap: {limit, used, left, queued}, window: {start, end, open, next}, drafting: {batch, total, done, failed} or null, opt_out, today}`. `counts` ignore the status filter. |
| `update_outreach(name, body, subject?, acting_user)` POST | Edit a Draft (or a Failed message, which becomes a Draft again). An email needs a subject. Returns Result. |
| `regenerate_outreach(name, acting_user)` POST | A new AI text for a Draft or Failed message. Returns Result. |
| `approve_outreach(names, acting_user)` POST | `names` a list or JSON list. Returns `[Result]`. A Draft becomes Approved (`approved_by`, `approved_on`). **Email**: sent at once through the default outgoing Email Account when the send window is open and the approver has sends left today (status Sent, a Communication on the lead, `communication`, `sent_on`); otherwise it stays Approved with the reason in `message` and the 10-minute sender delivers it. A send error makes it Failed with `error`. **WhatsApp**: stays Approved (ready); the hub opens `url`. Unsubscribed and Lost-type leads make it Cancelled with `error`. 417 "Set up an outgoing email account first." when an email is among the names and the site has no outgoing account. |
| `mark_outreach_sent(names, acting_user)` POST | The BD sent these approved WhatsApp messages by hand: Sent, `sent_on`, logged on the lead as the BD's WhatsApp. |
| `cancel_outreach(names, acting_user)` POST | Draft, Approved and Failed messages become Cancelled; a Sent one is refused in its Result. |

**Rules every email passes:** one first message per lead per channel (a Cancelled message may be
redrafted); an opt-out line is added under every email when it is sent (`opt_out` in `get_outbox`);
**Emails per user per day** (default 30, counted on the approver, 0 stops email); the send window
(Monday to Saturday, default 09:30 to 18:00 site time; outside it approved emails wait); an
unsubscribed or Lost-type lead is never written to, also when it became so after approval.

**Replies.** With Outreach on, an incoming email on a lead from outside the team (a normal reply,
or mail BCC-logged by `email_log`) is classified by the AI into `Interested`, `Question`,
`Objection`, `Not now`, `Not interested`, `Unsubscribe` or `Out of office`; with the AI off or
failing it is `New reply`. The class is kept as the lead's **`last_reply_class`** and
**`last_reply_on`** (`null` when none) and acted on:

| Class | Stage (only from before the first meeting) | Also |
| --- | --- | --- |
| Interested, Question | Positive / Interested | Follow-up for the owner today (unless one is due by today); notification |
| Objection | stays | Follow-up for the owner today; notification |
| Not now | Follow-up Required (from New Lead, Contacted, Not Responding) | Follow-up in 30 days; notification |
| Not interested | Not Interested | Notification |
| Unsubscribe | stays | `unsubscribed` set; its Draft and Approved messages are Cancelled; notification |
| Out of office | stays | nothing |
| New reply | stays | notification, for a person to read |

The owner is the lead's owner, else whoever approved its latest outreach message. A lead that is
closed (Won or Lost-type) keeps its stage; an older reply never undoes a newer one.

- **`get_options`** returns `outreach`: true while Outreach is on (the hub shows its tab then).
- **Rows and `get_lead`** return `last_reply_class` and `last_reply_on`.
- **`list_leads`** takes `last_reply_class`: a value, a list or a JSON list; another value is
  refused (417).
- **`get_category_counts`** adds `last_reply_class`: `{class: leads}`.
- **`draft_lead_message`** (§2.6) returns `first_message` (true when written from website signals)
  and, for WhatsApp, `to`.

### 2.18 Commission (`commission.md`)

Module `tbo_smart.api.hub_commission`; every method takes `acting_user` (§1). Money is the
company currency. `month` is `YYYY-MM` (or any date in it).

- **`get_commission(month, user=None, department=None)`** (GET): the month, live. A salesperson sees
  themselves, a department head their departments' people, a Sales Manager, System Manager or
  TBO Management everyone; asking for anyone else is 403. A statement that is Approved or Paid
  stands as approved (`frozen`: 1). Returns `{month, currency, scope ("own" / "department" /
  "all"), can_manage, rows, totals: {collected, commission}, unattributed}`. A row is `{user,
  full_name, plans, collected, target, achievement, reached: {slab_no, rate, above_target} or null,
  commission, statement, status (null: no statement yet), frozen, approved_by, approved_on,
  paid_on, projects: [{project, plan, collected, share_percent, credited, target, commission}],
  breakdown: [{project, slab_no, from_amount, to_amount, amount, slab_rate, rate, above_target,
  commission}], payments: [{payment_entry, date, kind, amount, project, lead, customer}]}`.
  `unattributed` (managers, with no `user` or `department`) is `{amount, count, reasons:
  {reason: amount}, payments: [{payment_entry, date, kind, amount, customer, lead, project,
  reason}]}`; everyone else gets null. `kind` is Receipt, Refund, Cancelled receipt or Cancelled
  refund; cancelled and refunded money is negative.
- **`generate_statements(month)`** (POST, managers): creates or refreshes the Draft statements of
  everyone with money in the month. Returns `[{user, name, status, commission, skipped}]`;
  `skipped` is 1 for an Approved or Paid statement, which is never touched.
- **`approve_statement(name)`** (POST, managers): Draft to Approved; returns `{name, status,
  approved_by}`. Nobody approves their own statement except a System Manager. Another status is 417.
- **`mark_paid(name)`** (POST, managers): Approved to Paid; returns `{name, status}`.
- **`get_commission_plans()`** (managers): `{plans: [{name, plan_name, is_active, basis,
  department, lead_type, project_type, user, monthly_target, above_target_rate, slabs: [{from_amount,
  to_amount (null: no limit), rate}]}], currency, departments, lead_types}`.
- **`save_commission_plan(values, name=None)`** (POST, managers): creates (no `name`) or updates a
  plan from the plan fields above. Slabs may not overlap, rates are 0 to 100, and two active plans
  may not have the same conditions (417). A plan cannot be renamed.
- **`get_project_commission(project)`** (managers): `{setup, collected, payments, commission,
  currency}`. `setup` is null when the project has none, else `{project, is_active, lead,
  project_type, earners: [{user, share_percent}], plan, use_override, override_target,
  override_above_target_rate, override_slabs}`. `collected` is all money collected on the project
  so far, `commission` what statements credit on it.
- **`save_project_commission(values)`** (POST, managers): `values` is the setup above (`project`
  names it). Earners' shares must add up to 100. Returns `get_project_commission`'s answer.

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

`tbo_smart/tests/test_lead_place.py`: the place round trip and clearing, Google-only links,
coordinate ranges, write permission; photos added, listed, served and thumbnailed, images only,
size and count limits, privacy, other leads' files and other attachments never served, who may
delete.
`tbo_smart/tests/test_lead_category.py`: the seeded industries, the state list, options, the four
filters (single, several, JSON), rows and `get_lead`, cleaning on create and update, refusals,
`add_industry` (idempotent, managers only), `bulk_set_lead_category` and the counts.

`tbo_smart/tests/test_lead_wave1.py`: see `lead-departments.md` §9.

`tbo_smart/tests/test_prospecting.py`: see `prospecting.md` §9.

`tbo_smart/tests/test_daily_report.py`: the daily report's calls, fresh and follow-up leads,
positive leads, quotations, meetings and follow-ups, new leads without imports, the week to date
and the scope (a salesperson sees themselves, a Sales Manager everyone, `owner` outside the scope
is 403); `log_call` (the outcomes, the acting user, the lead's write permission).
`tbo_smart/tests/test_call_outcomes.py`: the new stages and their types, the stage each outcome moves
a lead to (and where it stays), the automatic lost reason and wrong-number flag, an older call
never undoing a newer one, the pipeline off, `log_call`'s `stage`, the rows, `get_lead`, the
`last_call_result` filter and its counts; `test_telephony.py`: MyOperator calls set the last
call.

`tbo_smart/tests/test_outreach.py` (AI and email mocked): see `prospecting.md` §12.

`tbo_smart/tests/test_commission.py`: the slab maths and plan matching (`commission.md` §3), receipts to
projects and earners, splits, own slabs, pooled and per-project basis, Unattributed, a cancelled receipt
in the next month, statements (draft, refresh, approve, freeze, paid), who sees whom, and plans and
project set-ups saved by managers only.

## Changelog

- **v1.18 (2026-10-31):** commission (§2.18): `hub_commission.get_commission`,
  `generate_statements`, `approve_statement`, `mark_paid`, `get_commission_plans`,
  `save_commission_plan`, `get_project_commission`, `save_project_commission`; the monthly draft
  job. `BusinessOverview.traced_allocations` returns each allocation's lead, owner, lead type and
  project (`allocations` is unchanged).
- **v1.17 (2026-10-30):** outreach (§2.17): `hub_outreach.draft_outreach`, `get_outbox`,
  `update_outreach`, `regenerate_outreach`, `approve_outreach`, `mark_outreach_sent`,
  `cancel_outreach`; `last_reply_class` and `last_reply_on` on rows and `get_lead`;
  `list_leads`' `last_reply_class`; `get_category_counts`' `last_reply_class`;
  `get_options`' `outreach`; `draft_lead_message`'s `first_message`.
- **v1.16 (2026-10-30):** call-outcome stages and the last call (§2.16): the Not Responding, Not
  Interested and Wrong Number stages in `get_options`; `last_call_result` and `last_call_on` on
  rows and `get_lead`; `list_leads`' `last_call_result`; `get_category_counts`' `last_call_result`;
  `log_call` takes the call batch outcomes and `callback_at` and returns `stage`.
- **v1.15 (2026-10-30):** find prospects (§2.15): `estimate_prospect_run`, `start_prospect_run`,
  `get_prospect_runs`, `get_prospect_run`; `get_options`' `can_find_prospects` and `prospecting`;
  `get_lead`'s `lead.prospect`.
- **v1.14 (2026-10-30):** lead categories (§2.14): `industry`, `state`, `district`, `org_size`
  on `list_leads`, rows, `get_lead`, `create_lead` and `update_lead`; `get_options`' `org_sizes`,
  `states`, `kerala_districts`, `districts_by_state`; `get_category_counts`, `add_industry`,
  `bulk_set_lead_category`; the Industry Types are seeded on migrate.
- **v1.13 (2026-10-30):** today's calls (§2.13): `get_my_calls`, `get_next_batch`,
  `record_call_outcome`, `get_call_batches_report`; departments carry the call-batch rules.
- **v1.12 (2026-10-30):** `get_business_overview` (§2.12) and `get_options`' `can_view_business`;
  the TBO Management role. The overview's Meetings held and Proposals sent now also count from the
  stage log (they read 0 for leads moved by hand or imported), and a won lead's value falls back
  to its deal's amount, then its Sales Orders'; `get_proposal_money` follows (§2.4, §2.8).
- **v1.11 (2026-10-30):** place and photos (§2.11): `get_lead_photos`, `upload_lead_photo`,
  `delete_lead_photo`, `get_lead_photo`; the lead's `place` and `photo_count`; `update_lead` takes
  `google_profile_url`, `maps_url`, `latitude`, `longitude` and `location_note`.
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
