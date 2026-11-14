# Hub leads API (`tbo_smart.api.hub_leads`), v1.30

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
| `get_options` | GET | `acting_user` | `{stages: [{name, label, type, color}], sources: [], segments: [], departments: [{name, lead_type, is_active, assignment, stale_after_days, call_batches_enabled, is_head, is_viewer, is_member}], lead_types: [], owners: [{email, full_name}], territories: [], industries: [], org_sizes: [], states: [], kerala_districts: [], districts_by_state: {state: [district]}, can_call}` (§2.12) |
| `list_leads` | GET | `search?`, `stage?`, `owner?`, `segment?`, `source?`, `temperature?` (Hot / Warm / Cold), `industry?`, `state?`, `district?`, `org_size?` (each a value or a list, §2.14), `last_call_result?` (a value or a list, §2.16), `metric?` with `period?` / `from_date?` / `to_date?` (§2.4), `view` (`all` / `mine` (owned or created) / `call_today` / `follow_up` / `called` (called at least once, last called first; `from_date` / `to_date` without a `metric` keep the leads last called in those days), default `all`), `mine?` (any view kept to the leads they own or created), `created_by_me?` (only the leads they created, owned by anyone: read only for them), `due?` (with `follow_up`: the leads whose earliest open follow-up is `overdue`, `today`, `tomorrow`, `week` (2 to 7 days on) or `later`; without it `follow_up` is overdue and today), `start` (0), `page_length` (20, at most 100), `acting_user` | `{rows: [Row], total}` |
| `get_follow_up_counts` | GET | `owner?`, `mine?`, `acting_user` | `{overdue, today, tomorrow, week, later}`: how many leads the acting user may see have their earliest open follow-up in each, as `list_leads(view="follow_up", due=…)` lists them |
| `get_overview` | GET | `period` (`week` / `month` / `quarter` / `custom`; default `month`, or `custom` when dates are sent), `from_date?`, `to_date?` (custom), `owner?`, `acting_user` | Overview (§2.4) |
| `get_lead_intake` | GET | `from_date?`, `to_date?` (default today, at most 62 days), `owner?`, `acting_user` | Intake (§2.4.1) |
| `get_proposal_money` | GET | `period`, `from_date?`, `to_date?`, `owner?` (as `get_overview`), `acting_user` | Money (§2.8) |
| `get_sales_activity` | GET | `from_date?`, `to_date?` (default this month), `acting_user?` (§2.5) | `{from_date, to_date, currency, people: [Activity]}` |
| `get_lead` | GET | `name`, `acting_user` | `{lead: Lead, timeline: [Item], quotations: [Quotation], proposals: [Proposal row], allowed_stages: [stage name]}` |
| `create_lead` | POST | `data`, `allow_duplicate?` (1 to create past an email match, §2.9; never past a number), `acting_user` | as `get_lead` |
| `find_duplicate_leads` | GET | `email?`, `phone?`, `company?`, `exclude?` (a lead name), `acting_user` | `[Match]` (§2.9) |
| `get_category_counts` | GET | `acting_user` | `{industry, state, district, org_size, last_call_result}`, each `{value: leads}` (§2.14, §2.16) |
| `add_industry` | POST | `name`, `acting_user` | `{name, created}` (§2.14) |
| `bulk_set_lead_category` | POST | `leads` (list of names), `values`, `acting_user` | `{updated, skipped: [{name, reason}]}` (§2.14) |
| `get_merge_preview` | GET | `first`, `second` (lead names), `acting_user` | `{leads: [Summary, Summary]}` (§2.26) |
| `merge_leads` | POST | `keep`, `merge` (lead names), `acting_user` | as `get_lead`, for `keep` (§2.26) |
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
| `get_member_calls` | GET | `user`, `acting_user` | MyCalls of `user`, read-only (no actions) + `member` {user, full_name} + `called` [Call + outcome, called_at, note]; planners and viewers of their department only (call-batches.md §11) |
| `set_calling_focus` | POST | `department?`, `product?`, `district?`, `acting_user` | `{focus, label, message, next_batch}` (§2.21) |
| `get_next_batch` | POST | `department?`, `industry?`, `state?`, `district?`, `product?`, `acting_user` | `{created, reason, batch?, slot?, size?}` (§2.13) |
| `record_call_outcome` | POST | `batch_item`, `outcome`, `note?`, `callback_at?`, `acting_user` | `{item, outcome, retry_at}` (§2.13) |
| `get_call_batches_report` | GET | `date?` (default today), `acting_user` | `{date, people: [Person], totals}` (§2.13) |

### 2.1 Shapes

```
Row = {name, lead_name, company_name, email_id, mobile_no, tbo_stage, segment, lead_owner,
  industry, state, district, org_size, owner_name, created_by (who created it), call_count (calls
  it has had), score (0-100), next_follow_up (date | null), modified,
  latest_quotation: {name, tbo_quote_status, grand_total, currency, tbo_sent_on} | null,
  latest_proposal: {name, document_type (Proposal | Quotation), status, view_count, sent_on} | null}

Lead = {name, lead_name, first_name, last_name, company_name, email_id, mobile_no, phone,
  whatsapp_no, job_title, website, source, territory, industry, state, district, org_size, segment, tbo_stage,
  status (ERPNext's own, read only), lead_owner, owner_name, tbo_score, tbo_temperature
  (Hot / Warm / Cold), tbo_score_reason, tbo_lost_reason, tbo_lost_notes, creation, modified,
  next_follow_up (date | null), deal (the Opportunity once won | null), can_write,
  can_call (the acting user may use click-to-call, through MyOperator or CallerDesk),
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
- `set_stage` to **Closed – Lost** needs `note` (Not Interested, Wrong Number and Small Business need none, §2.16): a lost reason's name, or free text saved as
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

### 2.5.2 My calls

`get_my_call_counts(days?, acting_user?)` is the acting user's own calls for the hub's My leads,
counted exactly as the daily report counts `calls_total` and `calls_connected` (every TBO Call they
made or took; connected: the answered ones): `{today, week (from Monday), month, all_time: {calls,
connected}, days: [{date, calls, connected}]}`, `days` being the days with calls in the last
`days` days (default 60, at most 366), newest first.

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
(HTTP 409)** and the matches in the response's `matches` (a `[Match]`); the message is `Possible
duplicate of <lead> (<owner name>): it already has the number <number>, and a number belongs to
one lead.` when a lead has the number, else `Possible duplicate of <lead> (<owner name>)`. A
company-only match never blocks. Pass `allow_duplicate=1` to create anyway past an email match; a
number another lead has is refused even then (one lead per number, `lead-departments.md` §6),
with the same number message and no `matches`. The same refusal stops `update_lead` putting such
a number in `mobile_no`, `phone` or `whatsapp_no`. With the owner left empty in a **Round robin** department the lead goes to the
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
A call is listed only while its lead is at New Lead and is not a newer duplicate of another lead
(`call-batches.md` §3, §5), so a retry or call back of a lead a call moved on is not listed.
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
empty: Morning 10:00-13:00 (40), Afternoon 14:00-16:00 (30) and Evening 16:00-18:00 (30)) and `member_caps` (`{user: cap}`).
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
added as a note) needs no reason; Closed – Lost still does. Small Business (Lost, gray) works the
same way with the reason "Small business": a lead too small for TBO, set by hand only, never by a
call (`lead-pipeline.md` §2.1).

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
  queued (an Approved email waiting for the window or the cap),
  whatsapp (WhatsApp only, else null: §2.19), creation, modified}
Result = {name, status, message}      # status null: not found or no permission
```

| Method | Does |
|---|---|
| `draft_outreach(channel, leads?, run?, acting_user)` POST | `channel` `Email` or `WhatsApp` (any case); `leads` a list (or JSON list) or `run` a prospect run (its leads). Checks each lead now, then drafts the eligible ones in the background (`autopilot.draft_message`, §2.6: for a lead Find prospects created and nobody has written to, from its website signals and Why call line). Returns `{batch, queued, skipped: [{lead, lead_name, reason}]}`. Skipped: not found, the user cannot edit it, a first message is already in the outbox (Draft, Approved or Sent) on that channel, unsubscribed, Lost-type stage, no address for the channel, or over **Drafts per request** (default 50, at most 200). 417 when the AI is not set up. |
| `get_outbox(status?, batch?, run?, acting_user)` | `status` one value, a list or a JSON list. The user's own messages, a department head's department's too, managers all; newest 200. Returns `{enabled, email_ready (an outgoing Email Account exists), rows: [Message], counts: {status: n}, cap: {limit, used, left, queued}, window: {start, end, open, next}, drafting: {batch, total, done, failed} or null, opt_out, today}`. `counts` ignore the status filter. |
| `update_outreach(name, body, subject?, acting_user)` POST | Edit a Draft (or a Failed message, which becomes a Draft again). An email needs a subject. Returns Result. |
| `regenerate_outreach(name, acting_user)` POST | A new AI text for a Draft or Failed message. Returns Result. |
| `approve_outreach(names, acting_user, send_whatsapp?)` POST | `names` a list or JSON list. Returns `[Result]`. A Draft becomes Approved (`approved_by`, `approved_on`). **Email**: sent at once through the default outgoing Email Account when the send window is open and the approver has sends left today (status Sent, a Communication on the lead, `communication`, `sent_on`); otherwise it stays Approved with the reason in `message` and the 10-minute sender delivers it. A send error makes it Failed with `error`. **WhatsApp**: stays Approved (ready); the hub opens `url`, or, with `send_whatsapp` 1 (the hub has a TBO Chat inbox), it is queued for the hub to send (§2.19). Unsubscribed and Lost-type leads make it Cancelled with `error`. 417 "Set up an outgoing email account first." when an email is among the names and the site has no outgoing account. |
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

### 2.19 WhatsApp through TBO Chat (`whatsapp-outreach.md`)

Methods are in **`tbo_smart.api.hub_whatsapp`**. The hub owns the Chatwoot connection; the ERP owns
the lead. The first five are the **integration user's own calls** (no `acting_user`; anyone else
gets 403). The last two run as the acting user (§1).

**Sending is pulled by the hub.** `approve_outreach(..., send_whatsapp=1)` queues an approved
WhatsApp message (`chat_status` Queued); without it, approving stays manual (§2.17). The hub
fetches the queue every minute (and right after an approval), sends each message through Chatwoot
and reports the result.

```
Queued = {name, lead, to ("+" and digits), body, lead_name, first_name, company_name, why_call,
  product, product_intro, contact_id, conversation_id, window_open}
WhatsApp (on an outbox Message) = {via_chat, chat_status (null | Queued | Sending | Sent |
  Delivered | Read | Failed), template, window_open, first_name, company_name, why_call}
LeadWhatsApp (get_lead's lead.whatsapp) = {to, conversation_id, contact_id, last_in,
  window_open, window_ends, blocked}; get_lead_whatsapp adds can_write
```

`window_open` is true for 24 hours after the lead's last WhatsApp message to us. WhatsApp allows
free text only then; the first message needs an approved template, chosen and filled by the hub
from `first_name`, `company_name` and `why_call`. A lead with a product (§2.20) also carries
`product` and `product_intro` (null: the message is from TBO); the hub may fill the template's
name and intro with them.

| Method | Does |
|---|---|
| `get_whatsapp_queue(country_code?, limit?)` | Queued messages, **during the send window only**, oldest approval first (default 20). Each is claimed (`chat_status` Sending) so it is handed out once. `country_code` (digits, default 91) is added to 10-digit numbers. A lead that unsubscribed or was lost meanwhile has its message Cancelled; one with no usable number Failed. Returns `[Queued]`. |
| `report_whatsapp(name, state, text?, contact_id?, conversation_id?, message_id?, template?, error?)` POST | `state` `sent`: the message is Sent (`sent_on`, `template`, `chat_message_id`), `text` (what really went: the template filled in, or the free text) is filed on the lead, the ids are kept on the lead. `failed`: Failed with `error`. `manual`: TBO Chat is not ready (no inbox or template); the message stays Approved and the BD sends it by hand. Only a Queued or Sending message may be reported. Returns Result. |
| `receive_whatsapp(phone, text, conversation_id?, contact_id?, message_id?, at?, sender_name?)` POST | A WhatsApp message a contact sent. The lead is the one holding `conversation_id`, else the most recently changed lead whose mobile, WhatsApp or phone number ends in the same 10 digits. Matched: filed as a Communication (medium Chat, received) on the lead, once per `message_id`; `tbo_whatsapp_last_in` opens the 24-hour window; with Outreach on it is queued for the reply sorter (`prospecting.md` §11). A reply that is only "stop" or "unsubscribe" is an Unsubscribe without the AI. Returns `{matched, lead, communication}`; not matched: not a lead's chat. |
| `whatsapp_status(message_id, status, error?)` POST | A delivery receipt: `sent`, `delivered`, `read` or `failed`. An older receipt never undoes a newer one; `failed` makes the message Failed with `error`. Returns `{matched, status}`. |
| `get_lead_whatsapp(lead, acting_user)` | `LeadWhatsApp` for the reply box. `blocked` says why nothing may be sent (unsubscribed, lost, no number). |
| `log_lead_whatsapp(lead, text, message_id?, contact_id?, conversation_id?, acting_user)` POST | The acting user sent `text` from the lead page through TBO Chat: filed on the lead as theirs. Needs write permission; refused (417) while `blocked`. Returns `{communication}`. |

- **`get_lead`** returns `lead.whatsapp` (`LeadWhatsApp`) and timeline entries
  `{kind: "whatsapp", direction: "incoming" | "outgoing", at, who, text, ref}` (the last 100 messages).
- **Lead fields** (read-only custom fields): `tbo_chat_contact_id`, `tbo_chat_conversation_id`,
  `tbo_whatsapp_last_in`. **Outreach message fields:** `via_chat`, `chat_status`,
  `chat_message_id`, `template`.
- **Phone numbers** are normalised to E.164 with the country code the hub passes (default 91).
- **A message left in Sending** (the hub crashed between fetching and reporting) shows as Sending
  in the outbox; cancel it or confirm it sent by hand.

### 2.20 Products / brands under a department (`lead-departments.md` §10)

A department (GrowthX) lists the names its people call under (PhoebiX, BillaX, ...). A lead
carries one in `tbo_product`. A department with no products behaves as before.

```
Product = {product_name, is_active, intro_line (at most 140 characters), website}  (save also takes previous_name)
```

| Where | Change |
|---|---|
| `get_options` | `departments[].products`: the department's **active** `[Product]`. |
| `get_lead_departments`, `save_lead_department` | `products: [Product]`, all of them (switched-off ones too). `save_lead_department`'s `values.products` replaces the list (a row with `previous_name` renames that product: its leads and the campaign product follow); names are unique in a department ignoring case (ValidationError), a name is required. `values.campaign_product` ("Calls for product") must be an active product or blank. Managers only, as before. |
| `list_leads` | `product` (one value or a list, a JSON list in a GET). Rows carry `product`. |
| `get_lead` | `lead.product` and `lead.product_intro` (null without a product). |
| `create_lead`, `update_lead` | `data.product`: must be an active product of the lead's department (any case, stored as listed; "" clears; ValidationError otherwise; set the department first). Only a System Manager, a Sales Manager or a head of that department may set or change it (PermissionError for the lead's owner, who still sees it). Saving other fields never trips this. Moving a lead to a department that lacks its product is refused until the product is cleared. A product switched off later stays on its leads and does not block edits. |
| `bulk_set_lead_category` | `values.product` (alone or with the others). The other categories still need a System Manager or Sales Manager; `product` alone also a department head. A lead whose department lacks the product, or that the person does not manage, is in `skipped` (`reason`). |
| `get_category_counts` | `product`: `{value: leads}`. |
| `estimate_prospect_run` | `values.product`: an active product of the run's department; the run's new leads get it. Runs return `product`. |
| `get_next_batch` | `product` narrows this batch's new leads; blank: the department's `campaign_product`. |
| `get_my_calls` | Every Call carries `product`, `intro_line` (null without a product) and `calling_for`: the product, else the department's name ("Calling for: ..."). `batch.campaign_product` is the department's campaign product (null: none). |
| Outreach | First messages and drafts for a lead with a product are written for that product: the AI gets `selling: {name, intro}` and names it once. Without one they are from TBO as before. WhatsApp `Queued` rows carry `product` and `product_intro`. |

Department campaign rules: `campaign_product` joins `campaign_industry/state/district`.

### 2.21 Calling for: the BD's switch (`call-batches.md` §10)

```
Focus = {department, product | null, district | null}
FocusOption = {department, products: [{product_name, intro_line}], districts: [text],
  locked_product | null, locked_district | null}
```

| Where | Change |
|---|---|
| `get_my_calls` | `focus`: the person's valid choice or null; `focus_options`: `[FocusOption]`, one per department of theirs with call batches on (and `[]` when none: `enabled` is false). `locked_*` are the department's campaign product / district (a manager's limit): the only choice. |
| `set_calling_focus` | `department` (blank clears the choice), `product?` (an active product of it, any case; ValidationError otherwise), `district?` (free text; the locked one when the department sets one). Returns `{focus, label, message, next_batch: {created, reason, size?, slot?}}`; `message` is plain words for the screen. It then tries to start the next batch for the focus under the usual rules (nothing pending, overdue within the limit, under the cap, a slot open), so `created` false with a `reason` is normal. ValidationError for a department the person is not a member of with batches on. |
| `get_next_batch` | Without `department`, the focus department; the request's `product` / `district` fill what the focus and the department leave open. **The department's `campaign_*` now always wins** over a request (it is a limit). |

### 2.22 The Lead Bank (`lead-bank.md`)

Any staff member adds a lead from a photo and earns a referral when it is won and paid. These
methods are in **`tbo_smart.api.hub_lead_bank`** (the referral settings are in `hub_commission`).
They are called like the rest (§1) with one difference: **`acting_user` needs no sales role**. It
must be an enabled System User and not an integration user; otherwise 403. The call does not run
as that person (a designer has no CRM permissions); each method reads and writes only what the
Lead Bank needs. The leads API itself stays closed to anyone without a sales role.

| Method | Verb | Arguments | Returns |
|---|---|---|---|
| `get_options` | GET | `acting_user` | `{regions: [{region, prefix, country, rate, has_department}], cap, currency, is_manager, max_photos}` (regions `India`, `UAE`, `KSA`; `rate` in %; `cap` in the company currency, 0 none) |
| `detect_region` | GET | `phones?` (a number or a list), `latitude?`, `longitude?`, `acting_user` | `{region: "India" \| "UAE" \| "KSA" \| null, by: "phone" \| "gps" \| null}` |
| `check_duplicate` | GET | `email?`, `phone?`, `company?`, `acting_user` | `{duplicate, mine, message}`: nothing of the matching lead is returned |
| `submit_lead` | POST | `data`, `photos`, `acting_user` | `{created, duplicate, mine, lead, region, department, in_pool, message}` |
| `get_my_leads` | GET | `start` (0), `page_length` (50, at most 100), `acting_user` | `{rows: [{id, title, collected_on, region, status, earned, pending}], total, summary: {leads, won, earned, pending}, currency}` |
| `get_dashboard` | GET | `month?` (any date in it; default this month), `acting_user` | managers: `{month, currency, collected, won, conversion, by_region: [{region, department, collected, won}], collectors: [{user, full_name, collected, won}], referral: {earned, paid, due, by_collector: [{user, full_name, due}]}, pool: {count, leads: [{lead, title, region, collected_on, collected_by_name}]}}` |
| `reassign_credit` | POST | `lead`, `user`, `reason`, `acting_user` | managers: `{lead, collected_by, previous}` |
| `hub_commission.get_referral_settings` | GET | `acting_user` | managers: `{regions: [{region, rate, department, effective_department}], cap, currency, departments}` |
| `hub_commission.save_referral_settings` | POST | `values` `{regions: [{region, rate, department}], cap}`, `acting_user` | as `get_referral_settings` |

- **`data`** (`submit_lead`): `company_name`, `contact_name`, `mobile_no`, `phone`, `email_id`,
  `website`, `business_type`, `address`, `region` (blank: detected from the phones, then the GPS;
  ValidationError "Pick the region" when neither says), `state`, `latitude`, `longitude`, `note`.
  Anything else is refused (417). A name (company or contact) and a phone or an email are needed.
- **`photos`**: `[{filename, content (base64)}]`, 1 to 5, PNG, JPEG or WebP, each under 10 MB. The
  hub shrinks them first.
- **The lead** is made by the system, not the collector: stage New Lead, source **Staff referral**,
  `tbo_collected_by`, `tbo_collected_on`, `tbo_collected_region`, `tbo_business_type`, `tbo_address`,
  country by region, a Google Maps link and the coordinates when the phone gave a fix, the photos as
  lead photos (§2.11), the note as its first comment. Its department is the region's (§ `lead-bank.md`
  2): a round robin department hands it to its next member, any other keeps it unowned for its heads;
  a region with no department leaves it in the **pool** (`in_pool` true) and the managers get a
  notification.
- **Duplicates** use `find_duplicate_leads`' matching (email, phone, company), over every lead. A
  business we have is not made again: `created` false, `duplicate` true (`mine` true when the
  collector added it themselves), `lead` null, and the repeat is a comment on the existing lead. The
  first collector keeps the credit.
- **`status`** in `get_my_leads` is New, Being called, Meeting, Won or Not interested. `earned` is
  referral in Approved or Paid statements; `pending` the rest of what the lead has earned so far
  (drafts, and money not yet in a statement). Only the collector's own leads are returned.
- **Referral in commission** (`commission.md` §7): `get_commission` rows and statements carry
  `referral` (the part of `commission` that is referral); their `projects` lines have `kind`
  (`Sales` or `Referral`) and, for a referral line, `lead` and `plan` ("UAE 3%"); `plans` lists
  "Referral (Lead Bank)". `collected`, `target` and `achievement` stay the sales ones.
- **Errors:** 403 when `acting_user` is not an enabled System User here or, for the dashboard,
  `reassign_credit` and the settings, not a manager (Sales Manager, System Manager or TBO
  Management); 417 for the refusals above.

### 2.23 Call plans: assign leads per person (`call-batches.md` §11)

A manager or department head sets, per member of a department with call batches on, the product
they are handed and the calls of each slot (Morning 40, Afternoon 30, Evening 30).

```
Plans = {date, departments: [Department]}
Department = {department, slots: [{label, start_time "HH:MM", end_time, size}],
  products: [{product_name, intro_line}],      # the active ones
  campaign_product | null, campaign_district | null, pull_unassigned (0/1),
  unassigned: {product ("" for none): leads},  # open, unassigned, with a phone: what batches can pull
  industries: [name], unassigned_industries: {industry ("" for none): leads},
  districts: [name],                           # the districts its open leads are in
  unassigned_districts: {district ("" for none): {products: {product: leads}, industries: {industry: leads}}},
  members: [{user, full_name, product | null, industry | null, district | null,
             sizes: {slot label: calls}, total, has_plan,
             today: {size, called}, leads}]}  # sizes: the plan's, else the slot's size; leads: open leads they own
```

| Method | Verb | Arguments | Returns |
|---|---|---|---|
| `get_call_plans` | GET | `acting_user` | `Plans`: every department with batches on for a Sales Manager / System Manager, the ones they head for a head; 403 for anyone else |
| `get_department_leads` | GET | `department`, `owner?` (a member; blank: the unassigned leads), `product?`, `industry?` or `district?` (`-` for none), `search?` (name, company, phone), `start`, `page_length` (≤ 100), `acting_user` | `{rows: [{name, lead_name, company_name, product, industry, district, stage, temperature, phone, modified}], total, products: {product: leads}, industries: {industry: leads}, districts: {district: leads}}`: open leads only; the counts ignore `product`, `industry`, `district` and `search` |
| `assign_leads` | POST | `department`, `user` (a member), `leads?` (names; one owned by someone else moves), or `count` with `product?`, `industry?` and `district?` (the next unassigned open leads with a phone, hot then newest), `acting_user` | `{assigned, skipped: [{name, reason}]}`; at most 500; 417 for a non-member, an unknown product or industry, or a district other than the department's campaign district |
| `unassign_leads` | POST | `department`, `leads`, `acting_user` | `{unassigned, skipped}`: back to the department's unassigned pool |
| `save_call_plan` | POST | `department`, `users` (members of it), `product?` (an active product of it; blank: any), `industry?` (an Industry Type, for a department without products; blank: any), `district?` (blank: any), `sizes?` (`{slot label: calls}`, 0 to 500, only the department's slots), `clear?` (1 removes their plan), `acting_user` | that `Department`; 403 for a department the person may not plan; 417 for a non-member, an unknown or switched-off product, a product other than the department's campaign product, a district other than its campaign district, an unknown slot or a number out of range |

What a plan changes: the slot's batch holds the member's number of calls (0: no batch in that
slot), the plan's product, industry and district narrow new leads after the department's campaign
and before the request and the person's focus (`set_calling_focus` then offers only that product
and district), the day's cap
is at least the plan's total, and the clock opens the department's batches for a planned member
even when their focus is another department. Batches already handed out stay. Saving a
department's members keeps each member's plan.

Assigning or unassigning moves the owner and the ToDo assignment, and takes the lead out of the
previous owner's running batch if they had not called it yet (`call-batches.md` §12). Same people
as `get_call_plans` (403 otherwise).

### 2.24 Contact numbers (`lead-numbers.md`)

A lead keeps every number it was given. `get_lead` returns them as `lead.numbers`, the main
number first, then the ones in use, then Old and Wrong numbers.

```
Number = {id, number (as typed), e164 ("+919847012345"), label, person, designation,
  is_primary (0/1), status, note, source, added_by, added_by_name, added_on, tel, whatsapp}
label  = Mobile | Office | WhatsApp | Decision-maker | Other
status = Active | Not reachable | Wrong number | Old
source = Import | Lead Bank | Added on a call | Manual
```

`get_lead`'s lead also has `can_remove_numbers` (a manager). The lead's `mobile_no` is always its
main number.

| Method | Verb | Arguments | Returns |
|---|---|---|---|
| `add_lead_number` | POST | `lead`, `number`, `label?` (Mobile), `person?`, `designation?`, `make_primary?` (0/1: the old main number stays, marked Old), `note?`, `on_call?` (0/1: source "Added on a call"), `acting_user` | `{numbers: [Number]}`; 409 `DuplicateEntryError` for a number another lead has (one lead per number, §2.9); 417 for something that isn't a phone number or a number the lead uses already; 403 without write on the lead |
| `update_lead_number` | POST | `lead`, `number_id` (`Number.id`), `status?`, `label?`, `person?`, `designation?`, `note?`, `make_primary?`, `acting_user` | `{numbers}`; a main number that stops being usable (Wrong number, Old) hands over to the next Active or Not reachable one |
| `remove_lead_number` | POST | `lead`, `number_id`, `acting_user` | `{numbers}`; System Manager and Sales Manager only (403 otherwise: a salesperson marks it Old or Wrong number) |

Changed methods:

- `find_duplicate_leads` (and the refusal in `create_lead`) match the phone against every number
  row of every lead, whatever its status, and the lead's mobile, phone and WhatsApp fields.
- `log_call` and `record_call_outcome` take `number?`: the lead's number that was called, as
  typed or in any format (default: its main number). It is recorded on the call
  (`customer_number`, "Call to <number>"). A **Wrong number** outcome marks that number Wrong
  number, and the lead becomes the Wrong Number stage only when no Active or Not reachable number
  is left; with another number to try the stage stays and the response's `stage` is null.
  417 for a number the lead doesn't have.
- `get_my_calls` summaries: `phone` is the main number (the first usable one if the main is not),
  `other_numbers` the other usable ones `[{id, number, label, person, is_primary}]`.
- `click_to_call` (`telephony-myoperator.md`) takes any of the lead's numbers.
- MyOperator, WhatsApp and Lead Bank lookups by number match any number of a lead.

Every change leaves a note on the lead's timeline: "Number added: X by <user>", "Primary changed
from X to Y by <user>", "X marked Wrong number by <user>", "Number removed: X by <user>".

### 2.25 Calling performance and Lead quality (the overview's analysis)

`get_lead_analysis` (GET; `period?`, `from_date?`, `to_date?`, `owner?`, `acting_user`; `period` and
the sales scope exactly as `get_overview`: a salesperson sees their own, a head their team, a
manager everyone; 403 for an `owner` outside it). One response holds every grouping, so the hub's
switch needs no second request.

```
{from_date, to_date, min_called,
 calling: [{user, full_name, given, called, completion, reached, reach_rate, overdue,
            calls, missed, talk_seconds}],
 quality: {source|industry|district|department|product:
           {rows: [{value (null: not set), leads, called, called_rate, reached, reach_rate,
                    wrong_rate, not_interested_rate, moved_on, moved_rate, won}],
            best, worst}}}
```

- **calling**, per person with batches or calls in the range, most `called` first. `given`,
  `called`, `reached` count the batch items (`is_repeat` = 0) of batches with `batch_date` in the
  range; `called` has an outcome, `reached` an outcome that is Connected, Call back or Not
  interested (`ANSWERED`); `completion` is called of given and `reach_rate` reached of called, in
  %. `overdue` is the items still without an outcome in a batch that has ended (as the managers'
  report counts them). `calls`, `missed` and `talk_seconds` come from the TBO Call log (`agent`,
  `started_at` in the range): all calls, those with status Missed, and the sum of `talk_seconds`.
- **quality** takes the leads **created** in the range (and in scope), grouped by the Lead's
  source, `industry`, `tbo_district`, `tbo_department` or `tbo_product`; a grouping whose field
  the site does not have is left out. `called` is leads that have a batch item with an outcome,
  `reached` those whose item was answered (so `reach_rate` is of the called). `wrong_rate` (stage
  Wrong Number or `tbo_wrong_number`) and `not_interested_rate` (stage Not Interested) are % of
  the leads. `moved_on` is leads whose stage log reached First Meeting Completed or later (the
  funnel's meeting step), `won` those now Closed - Won. Rows are the 50 largest groups.
  `best` and `worst` name the groups with the highest and lowest `reach_rate` among those with
  at least `min_called` (10) leads called, else null (also null with fewer than two such groups).

### 2.26 Merging duplicate leads

Two leads that are the same enquiry become one. `get_lead`'s lead has `can_merge` (1 for a Sales
Manager, System Manager or a head of the lead's department): the hub shows **Merge** only then.

```
Summary = {name, lead_name, company_name, email_id, mobile_no, tbo_stage, lead_owner, owner_name,
  creation, numbers: [Number] (§2.24), new_numbers (numbers the other lead lacks),
  counts: {notes, calls, follow_ups, meetings, proposals, quotations, deals, files}}
```

- `get_merge_preview(first, second)` returns both summaries, for the dialog. `merge_leads(keep,
  merge)` merges `merge` into `keep` and returns `keep` as `get_lead` does. Both need write on both
  leads. 417 for the same lead twice; 403 when the acting user is not a manager nor the head of
  `keep`'s department, or when the leads are in different departments (a manager may; `keep`'s
  department stays). The merged lead is deleted on the strength of that check, so a head, who has
  no delete right on Lead of their own, can merge.
- **Moves to `keep`:** the merged lead's numbers `keep` lacks (by digits; not primary, with their
  status, label, person and who added them), its values for fields `keep` has empty (company,
  email, phone, WhatsApp, job title, website, territory, industry, city, state, country,
  customer, organisation size, revenue, district, segment, place fields, source), unsubscribed
  (carried: an opt-out is never lost) and its later last call and last reply. Never overwritten:
  anything `keep` already has; its owner, department, product and stage.
- **Re-pointed by Frappe's merge-rename:** every Link and Dynamic Link to the lead, in child tables
  too (call batch items, event participants, deal and quotation parties, `tbo_lead`), notes,
  communications, files, follow-ups (ToDos), assignments, versions and TBO calls, meetings,
  proposals, outreach messages, commission records. The copied lead name on meetings, proposals,
  outreach messages and triage rows is refreshed.
- **Does not move:** the merged lead's stage history (its own timeline of stage changes) and its
  chat ids; a call batch holding both leads keeps two items. A deal on each lead leaves two
  deals on `keep`.
- `keep`'s timeline gets "Merged LD-… (Ashraf, Amana UAE) into this lead by <user>. 12 notes,
  4 calls, …". All in one transaction.

### 2.32 The change feed and bulk read for the hub's lead mirror (`tbo_smart.api.hub_lead_mirror`)

The ERP stays the master of every lead. The hub keeps a read-only copy (hub `docs/lead-mirror.md`)
and keeps it current from these four methods. **Only the integration user** may call them (403
for anyone else, a Sales Manager and System Manager included); they take no `acting_user`,
read and write nothing, and answer for every lead whatever the caller's own scope, because the
hub filters its copy per person afterwards (below).

**The change log.** `TBO Lead Change` (lead, change_type, at) gets one row, written **after the
transaction commits** (a rolled-back save leaves none; a failure to write is logged and never
breaks the save), whenever something the hub shows of a lead changes: `Lead` (its fields,
numbers and stage log, on insert and update), `Note` (a Comment of type Comment), `Call` (a TBO
Call, or a call Event), `Event` (other Events with the lead as participant), `Follow-up` (a ToDo,
which is also an assignment), `File`, `Batch` (a TBO Call Batch holding the lead), `Quotation`,
`Proposal`, `Meeting`, `Deal` (the Opportunity's `tbo_lead`) and `Deleted` (the lead was deleted;
a rename or merge logs the old name as `Deleted` and the new name as `Lead`). Rows older than 30
days are deleted daily. Not logged: emails (Communication) and WhatsApp messages: the hub's
nightly comparison and opening a lead pick those up.

| Method | HTTP | Arguments → answer |
| --- | --- | --- |
| `get_lead_changes` | GET | `since?` (a datetime, the `next_since` of the page before; none: from the start), `limit?` (default 500, at most 1000) → `{changes: [{lead, at, deleted}], next_since, more, now}`: each lead changed after `since` **once**, with the time of its latest change, oldest first. Ask again from `next_since` while `more`. `deleted` is a hint (the lead's own delete was its last change); a lead that is not found by `get_leads_bulk` is gone whatever `deleted` says. `at` is the ERP's clock; keep the hub's `since` a little (a minute) behind the last `next_since` so a change committed out of order is still read; reading a lead twice is harmless |
| `get_lead_index` | GET | `after?` (the last name of the page before), `limit?` (default 1000, at most 2000) → `{leads: [{name, modified}], next_after, more, total, now}`: every lead by name; `total` (the count of all leads) on the first page only. For the hub's first copy and its nightly comparison |
| `get_leads_bulk` | GET/POST | `names` (a list, or a JSON list; at most 100, else 417) → `{leads: [{name, row, payload, owner, created_by, assignees, department, modified}], missing: [name], errors: {name: why}}`. `row` is the list row (`list_leads`), `payload` is `get_lead`'s answer without what is about the person asking (`lead.can_write`, `can_call`, `can_remove_numbers`, `can_merge` and `allowed_stages`: the hub works those out for the person), `owner` is the lead owner, `created_by` who created it (reads it, never writes it), `assignees` the users of its ToDos that are not cancelled (open or closed), `department` its lead department. `missing`: not leads (deleted or merged away). `errors`: leads that could not be read; the rest still come back |
| `get_mirror_scopes` | GET | → `{scopes: {user: {all, team, departments}}, now}` for every enabled System User with a sales role who is not an integration user (the people the hub may name): `all` when they see every lead (System Manager, Administrator, a Sales Manager outside the sales hierarchy); else `team` (themselves, and everyone below them while they are in the sales hierarchy) and `departments` (the active lead departments they head) |

**Who sees a lead.** Whether a person sees a lead is TBO's rule in `tbo_smart.sales.permissions`
(owner or assignee in the person's team, or a head of the lead's department, or everything).
A list of users per lead would go stale whenever a team or a department head changes, because the
lead itself did not change, so the lead carries its own facts (`owner`, `assignees`, `department`)
and the people's scopes travel apart (`get_mirror_scopes`); the hub's rule is exactly:

```
visible = scope.all
       or lead.owner in scope.team
       or any(a in scope.team for a in lead.assignees)
       or (lead.department and lead.department in scope.departments)
```

`tests/test_lead_mirror_feed.py` checks this against `frappe.has_permission("Lead", "read",
lead, user=…)` for a salesperson, one in the hierarchy, a team leader, a department head and a
Sales Manager, over owned, assigned and department leads.

### 2.33 The call box (`tbo_smart.api.telephony`)

The hub's call box (hub `docs/leads.md`) follows a click-to-call through two reads, both with
`acting_user` and both GET:

- `get_call_status(reference_id)`: `reference_id` is what `click_to_call` returned. Returns
  `{state, call, lead, number, answered, duration, talk_seconds}`; `state` is
  `calling` (placed, nothing heard yet), `ringing` (their phone), `ringing_lead` (they picked up, the lead's phone rings), `on_call` or `ended`. `answered`, `duration`
  and `talk_seconds` are set once `ended`. 403 unless the call
  is the acting user's. Live states need CallerDesk's Live Call event (`telephony-callerdesk.md`
  §3.1); without it a call goes from `calling` straight to `ended` when the report arrives.
- `get_incoming_call()`: `{enabled, call: {call, number, lead, lead_name, state} | null}`, the call
  coming in to the acting user now (a Live Call event in the last minute); `enabled` is false when
  they aren't set up for calling, and the hub stops asking.

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

`tbo_smart/tests/test_lead_mirror_feed.py`: a change row for each kind of change, none for a
rolled-back save or an unrelated record, a failed write never breaking the save, deletes and
merges, the 30-day prune; the feed (each lead once, in order, paging); the index; the bulk read
(row, payload, owner, assignees, department, missing, the 100-name limit); the scopes against the
ERP's own permission; integration user only.

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

`tbo_smart/tests/test_lead_bank.py`: see `lead-bank.md` §9.

## Changelog

- **v1.30 (2026-11-14):** the Small Business stage in `get_options` (Lost, gray); `set_stage`
  to it needs no `note` (§2.16).

- **v1.29 (2026-11-14):** the call box (§2.33): `get_call_status`, `get_incoming_call`.

- **v1.28 (2026-11-13):** one lead per number (§2.9, `lead-departments.md` §6): `create_lead`'s
  `allow_duplicate` no longer gets past a number another lead has; `update_lead` and
  `add_lead_number` refuse such a number (409); `add_lead_number` returns `{numbers}` without
  `duplicates`. TBO Smart Settings: *Numbers many businesses share*.
- **v1.27 (2026-11-10):** the lead mirror's change feed (§2.32): `TBO Lead Change`,
  `get_lead_changes`, `get_lead_index`, `get_leads_bulk`, `get_mirror_scopes` (integration user only).
- **v1.26 (2026-11-09):** merging duplicate leads (§2.26): `get_merge_preview`, `merge_leads`;
  `can_merge` on `get_lead`'s lead.
- **v1.25 (2026-11-07):** `get_lead_analysis` (§2.25): calling performance per person and lead
  quality per source, industry, district, department and product, for the overview.
- **v1.24 (2026-11-06):** contact numbers (§2.24): `lead.numbers` and `can_remove_numbers` on
  `get_lead`; `add_lead_number`, `update_lead_number`, `remove_lead_number`; `number` on `log_call`
  and `record_call_outcome`; `other_numbers` in `get_my_calls`; the duplicate check and number
  lookups cover every number.
- **v1.23 (2026-11-05):** call plans (§2.23): `get_call_plans` (members carry `leads`), `save_call_plan`,
  `get_department_leads`, `assign_leads`, `unassign_leads`; the member row's
  `call_product` and `slot_sizes`; the clock, the next batch, the cap and the focus follow them.
- **v1.22 (2026-11-04):** the Lead Bank (§2.22): `hub_lead_bank.get_options`, `detect_region`,
  `check_duplicate`, `submit_lead`, `get_my_leads`, `get_dashboard`, `reassign_credit`;
  `hub_commission.get_referral_settings`, `save_referral_settings`; commission rows carry
  `referral` and their `projects` lines `kind` and `lead`; the Lead's `tbo_collected_by`,
  `tbo_collected_on`, `tbo_collected_region`, `tbo_business_type` and `tbo_address`.
- **v1.21 (2026-11-04):** the BD's calling-for switch (§2.21): `set_calling_focus`, `get_my_calls`'
  `focus` and `focus_options`; the batch filler and the slot clock follow the focus; a
  department's campaign filter is now a limit a request cannot widen.

- **v1.20 (2026-11-03):** products / brands under a department (§2.20): `get_options`'
  `departments[].products`; `products` and `campaign_product` on `get_lead_departments` /
  `save_lead_department`; `product` on `list_leads`, its rows, `get_lead`, `create_lead`,
  `update_lead`, `bulk_set_lead_category`, `get_category_counts`, prospect runs and
  `get_next_batch`; `get_my_calls`' `product`, `intro_line`, `calling_for` and
  `batch.campaign_product`; `Queued` WhatsApp rows' `product` and `product_intro`.
- **v1.19 (2026-11-02):** WhatsApp through TBO Chat (§2.19): `hub_whatsapp.get_whatsapp_queue`,
  `report_whatsapp`, `receive_whatsapp`, `whatsapp_status`, `get_lead_whatsapp`,
  `log_lead_whatsapp`; `approve_outreach`' `send_whatsapp`; outbox rows' `whatsapp`; `get_lead`'s
  `lead.whatsapp` and `whatsapp` timeline entries.
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
