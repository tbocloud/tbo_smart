# Smart leads: scoring, Call today, follow-ups, AI notes and AI runs leads (living spec, v1.3)

**Status:** built 2026-10-15, every feature off by default; since v1.2 on **ERPNext's own Lead**
instead of Frappe CRM's CRM Lead (`erpnext-lead-cutover.md`). This file is the living spec: it
describes what is built, and every change to the feature updates it in the same pull request.
The screens are the TBO Support hub's lead page (`hub-leads-api.md`) and the TBO Smart app.

- **Site:** india.teambackoffice.com (tboindia): Frappe v15, ERPNext, HRMS and tbo_smart.
- **Why:** 1,572 leads came over from the old ERPNext site on 2026-10-14 (New 848, Junk 371,
  Nurture 125, Qualified 85, Contacted 83, Unqualified 35, Converted 25). 94% have only a mobile
  number. No CRM Task or follow-up existed, two owners (Anjana, Dhanish) are disabled users, and
  nobody could tell which of the 848 New leads to call first.
- **Owner's direction:** "proceed, recommended way, no questions".

## 1. Switches

All in **TBO Smart Settings** (System Managers; the desk form, or `api/smart_leads_settings.py`).
Each is off until the owner turns it on; on a site without ERPNext every switch reads as off and
nothing is set up (`tbo_smart.smart_leads.switch` uses `tbo_smart.sales.erpnext_installed`, the
lesson of tbo_smart#14).

| Field | Label | Default | What it switches on |
| --- | --- | --- | --- |
| `enable_lead_scoring` | Lead scoring and Call today | 0 | Nightly and on-change scores, the Call today page and API, the dashboard card |
| `lead_scoring_use_ai` | AI reads notes to adjust scores | 0 | The AI adjustment (needs scoring and a provider) |
| `enable_lead_follow_ups` | Lead follow-ups and escalation | 0 | The rules, the escalation notices and the 09:30 digest |
| `enable_lead_notes` | AI call notes, lead brief and WhatsApp drafts | 0 | Drafts in the note box, the brief, WhatsApp drafts |
| `enable_lead_triage` | AI triage of stale leads | 0 | The Triage tab (Sales Manager / System Manager) |
| `enable_ai_leads` | **AI runs leads** (the master switch) | 0 | The AI reads every open lead on change, plans its next step, suggests stages, drafts messages, the daily plan and the gone-quiet nudges (§11) |

Numbers: `call_today_size` (12, 5-30), `ai_daily_lead_limit` (200), `lead_stale_days` (21),
`lead_ladder_l1_days` / `_l2_` / `_l3_` (2 / 4 / 7, must go up), `lead_manager_role`
("Sales Manager"), `lead_fallback_owner` (blank), `triage_max_leads` (300, at most 1,000),
`triage_daily_capacity` (15). Blank numbers fall back to these defaults in the code; the patch
`tbo_smart.patches.v1.set_smart_lead_defaults` stores them on existing sites. The Phase 2
setting `escalation_sales_head` ("Head - Sales & Marketing") is reused as the sales head.

## 2. AI providers (`tbo_smart/ai.py`)

Modelled on the hub's `helpdesk.ai_engine` (tbocloud/helpdesk `docs/ai-providers.md`), without
an SDK: both providers are spoken through `requests`.

| Section | Fields | API |
| --- | --- | --- |
| OpenAI | `openai_base_url` (`https://api.openai.com/v1`), `openai_model` (`gpt-5.6-luna`), `openai_api_key` (Password), `openai_input_price` / `openai_output_price` (USD per 1M tokens) | Chat Completions, `max_completion_tokens`, JSON mode when the prompt says JSON, no temperature |
| Anthropic-compatible (Kimi) | `anthropic_base_url` (blank: Anthropic; Kimi `https://api.moonshot.ai/anthropic`), `anthropic_model`, `anthropic_api_key`, prices | `POST {base}/v1/messages`, `x-api-key`, `anthropic-version: 2023-06-01` |

- `ai_primary_provider` (OpenAI) is tried first; with `ai_failover` (on) the other one next.
  A section without a key and a model is left out.
- **Failover and pause** (as the hub): billing (402, or 400/403/429 saying balance, quota,
  credit…), auth (401/403) and config (404, unknown model) fail over **and pause the provider
  for 30 minutes**; rate limit, 5xx and timeouts fail over; any other 4xx is a rejected
  request and is raised, not failed over. Each attempt has 60 s, one call 150 s in all.
  Thinking models get 2,048 tokens, then 4,096 once when the first answer ran out.
- **Alert:** a provider's first failure notifies the enabled System Managers once per 6 hours
  (Notification Log, so also push), e.g. "AI provider OpenAI (gpt-5.6-luna) failing since 15
  Oct, 09:31: insufficient balance or quota. Using Anthropic-compatible (kimi-k2.6)."
- **Keys** never reach the browser, the log or an error: `redact` masks the key and anything
  shaped like `sk-…` in provider messages. The settings API returns `{set, masked}` only.
  Base URLs must be `https://`.
- **Usage log:** doctype **TBO AI Usage Log**, one row per attempt: provider, model, feature
  (Lead scoring, Lead note, Lead brief, WhatsApp draft, Lead triage, Test connection),
  reference, user, input/output tokens, **estimated cost (USD)** from the prices above (0 until
  the owner enters them), duration, answered or the failure kind. System Manager read/delete.
  Rows are written by a job queued at once, not in the request's transaction, so a request
  that fails after a billed attempt still leaves its rows.
- **Number guard:** `ai.fact_numbers(...)` collects every number in the facts sent (dates give
  their year, month and day; 07 and 7 are the same); `ai.guarded(text, known)` returns the text
  only when every number in it is one of them. The lead's own ID is left out of the facts it
  checks against (`LeadFacts.known_numbers`), so "CRM-LEAD-2026-00050" can't make "50 licences"
  pass. A guarded-out text is dropped: a score reason with its adjustment, a triage answer
  (counted as "without an answer"), a brief line, a WhatsApp draft (the user is asked to try
  again), or a note (the user's own words are kept, with a warning).
- **Test connection** (`ai.test_provider`): a 64-token call with the saved settings, returns the
  model, host and time, or the provider's own error. A success clears the pause.
  Saving the settings also clears every pause (`reset_provider_state`).

### 2.4 Settings API (`tbo_smart.api.smart_leads_settings`, System Manager)

| Method | Verb | Does |
| --- | --- | --- |
| `get_settings` | GET | `{settings, secrets: {openai_api_key, anthropic_api_key, teams_dm_workflow_url: {set, masked}}, status}` |
| `save_settings(values)` | POST | Saves the listed fields; a secret not sent is kept, `""` removes it |
| `test_connection(provider)` | POST | `ai.test_provider` |
| `send_test_digest` | POST | Today's follow-up digest for the caller, sent only to the caller |

Validation (`TBOSmartSettings.validate`): https base URLs; the Teams URL is a Power Automate
workflow URL with its `sig=` (a Teams message link or a cut-off URL is refused, the hub's
check); Call today size 5-30; the ladder days go up; no negative numbers; triage at most 1,000.

## 3. Data

### 3.1 Fields (created on install and every migrate, ERPNext sites only: `smart_leads/setup.py`)

| Lead field | Type | Purpose |
| --- | --- | --- |
| `tbo_score` | Int, read-only | The score, 0-100 (Frappe CRM's `lead_score`; the facts still call it `lead_score`) |
| `tbo_temperature` | Select Cold / Warm / Hot, read-only | The band (CRM's `lead_temperature`) |
| `tbo_score_reason` | Small Text, read-only | One line: why the lead has its score |
| `tbo_scored_at` | Datetime, hidden | When it was last written |
| `tbo_ai_adjust`, `tbo_ai_reason`, `tbo_ai_hash` | hidden | The last AI read and what it read (content hash) |
| `tbo_ai_brief`, `tbo_ai_brief_hash`, `tbo_ai_brief_at` | hidden | The cached three-line brief |
| `tbo_ai_insight` (JSON), `tbo_ai_insight_hash`, `tbo_ai_insight_at` | hidden | AI runs leads: the AI's last read of the lead and what it read (§11) |
| `tbo_ai_sentiment` (Positive / Neutral / Negative), `tbo_ai_intent` (High / Medium / Low / None), `tbo_ai_next_action`, `tbo_ai_next_due`, `tbo_ai_stage` | hidden | The same read's parts, as fields so lists and counts can use them |
| `tbo_ai_nudged_on` | hidden | When the owner was last told the lead had gone quiet |

Scores are written with `frappe.db.set_value(..., update_modified=False)`: no Version row, no
`modified` bump, no `on_update`.

**Follow-ups are ToDos.** **Decision:** an open ToDo on the lead with the finance workflow's
`tbo_task_type` = **"Sales follow-up"** (`setup_smart_leads` also creates the finance fields on
ToDo), dated by its `date` (a day; the CRM task's time of day is gone), for its `allocated_to`.
ERPNext's Lead form lists open ToDos under Activities, and a ToDo assigns the lead to its person.
The next follow-up is the lead's earliest open ToDo with a date, whatever its type. Escalation
reuses the ToDo's `tbo_escalation_level`.

### 3.2 The migration note (`smart_leads/migration.py`)

Each migrated lead has a note "Migrated from tboindia-old <id>": since the cutover a Comment
whose text starts with "Migrated from tboindia-old" (the CRM note's title becomes its first
paragraph unless the content already starts with it: `migration.note_html`,
`is_migration_note`). The parser reads
"Label: value" lines, "Label - value" lines and two-cell table rows, with or without HTML, and
knows these labels: status (also "lead status", "original status"), source ("original
source", "lead source"), owner ("original owner", "lead owner"), created ("created on old
site", "created on", "creation"), remarks (several lines, until the next label), priority,
project type ("type of project"). The note's other labels (Comment, WhatsApp,
City/State/Country, Department) are recognised so they end a multi-line remarks value; other
unknown labels are ignored. **Checked against the live layout** (2026-10-15): the migration
wrote one `<p><b>Label:</b> value</p>` per field, which `test_live_migration_note_layout`
covers. The migration note is never
counted as a touch, and its title keeps it out of the "notes since" the AI reads.

### 3.3 Facts (`smart_leads/facts.py`)

`LeadFacts(names)` loads, in a fixed number of queries: the lead fields, the migration notes,
notes (Comments) and calls (call Events, their description the call's note) since, open ToDos
(earliest first), done ToDos, stage changes **after the first stage log row** (the first row is
the lead's insert, for the migrated leads the CRM's first row) and emails (Communication with
medium Email). **Last touch** = the newest call, note, done follow-up, stage change or email. **Created** = the migration note's created date,
else the lead's creation. Scoring, Call today, the follow-up rules, the brief and the triage all
read these facts.

## 4. Scoring and Call today

### 4.1 The base score (0-100, `smart_leads/scoring.py`)

| Part | Points |
| --- | --- |
| Status | The pipeline statuses, further along scoring higher: New Lead 10, Contacted 18, Follow-up Required 16, Positive / Interested 22, On Hold 4, Meeting Scheduled 24, First / Second / Third Meeting Completed 26 / 28 / 30, Proposal Sent 32, Negotiation 34 (`lead-pipeline.md` §2) |
| Stage changed in the last 30 days | +8 forward, −3 back to an earlier stage (pipeline order) |
| Source's conversion rate here | up to +12, against the best source (sources with 10+ leads; converted = the lead has a deal or is Closed – Won, where the migrated Converted leads went) |
| Project type's conversion rate (migration note) | up to +6, the same way |
| Last touch | ≤3 days +15, ≤7 +10, ≤14 +6, ≤30 +3 |
| Age (original created date) | ≤7 days +12, ≤30 +8, ≤90 +4, ≤365 +1 |
| Status before the migration | Interested / Opportunity / Quotation +10, Replied +6, Open +3, Lead +1, Lost Quotation −5, Do Not Contact −20 |
| Priority before the migration | High / Urgent +5, Medium +2 |
| Follow-up overdue | +5 |
| No phone and no email | −20 |

Closed leads (Closed – Won, Closed – Lost) score 0, Cold, "Closed (Closed – Lost)"; the open statuses are `OPEN_STATUSES` in `tbo_smart/lead_pipeline/statuses.py`, the one
list scoring, Call today, the follow-up engine and the triage read. Bands: **Hot ≥ 55, Warm ≥ 30, Cold** below. The reason line is the status,
then the parts that moved the score most, e.g. "Contacted · was Interested before the migration
· Website leads convert 9% · last touch 6 days ago". Conversion rates are cached 6 hours and
recomputed at the start of the nightly run.

### 4.2 The AI adjustment

With `lead_scoring_use_ai` on and a provider set up, the nightly run sends up to
`ai_daily_lead_limit` open leads **whose content hash changed** (status, notes, remarks, status
changes; not the passing days) in batches of 10. The AI returns `adjust` (clamped to ±15) and a
reason; a reason with an invented number is dropped with its adjustment; a failed batch is
skipped and retried the next night. Every lead of a batch that got an answer counts as read
(`tbo_ai_hash`), even when its reason was dropped or missing (then adjust 0, no reason), so a
lead is read, and billed, again only after it changes. The reason line then starts with the AI's reason.

**When scores change:** daily (`scheduler_events.daily`, `score_all`), and as a background job
(deduplicated per lead) when a lead's stage or owner changes, or a note, call or task is added
on it (Lead `on_update`, Comment and Event `after_insert`, ToDo `on_update`); not during an
import (the cutover). The on-change score uses the cached AI adjustment; the AI itself runs only
at night, so cost stays capped.

### 4.3 Call today (`smart_leads/call_today.py`)

Built live from the facts for one person: their open leads that the session user may read
(`frappe.get_list`, so the sales rules and hierarchy apply: `tbo_smart.sales.permissions`), in
this order:

1. follow-up overdue, oldest first;
2. follow-up due today;
3. no follow-up and no touch today, best score first.

Leads with a follow-up on a later day are left out (they have a plan), and so are leads touched
today with no follow-up. The list holds `call_today_size` rows; `more` counts the rest.
**Bounded work:** one cheap query lists the person's open leads by last night's stored score
and one grouped query finds each one's earliest open follow-up; the full facts are loaded only
for the leads with a follow-up due by today and the best `max(5 × size, 60)` with none (the
live score reorders those). `more` is counted from the cheap queries. Each row:
score, band, reason, why it is listed, phone, `+91`-normalised E.164, `tel:` and `wa.me` links,
email, last touch (when, what, days) and the follow-up.

**Who sees whose list:** your own; your sales team's (TBO Sales Hierarchy) if you lead one;
anyone's with crm_scope all (System Manager, a Sales Manager outside the hierarchy) or as a
sales head (`is_sales_head`: System Manager, Sales Manager, Sales & Marketing Head role or the
"Head - Sales & Marketing" user). Anything else is a PermissionError.

**Phone normalisation** (`smart_leads/utils.normalise_phone`): digits only;
`+` or `00` starts an international number kept as it is; otherwise a trunk 0 before 10 digits
is dropped and 10 digits get **+91**; 8-15 digits or nothing.

### 4.4 Dashboard

Two number cards (`smart_leads/dashboard.py`, served by `tbo_smart.api.dashboard.get_cards`): **Call today** (rows plus `more` for the dashboard's user, else me;
while scoring is on) and **Overdue lead follow-ups** (the person's open tasks on open leads due
before today; while follow-ups are on, lower is better). Both take the dashboard's user only when
the session user may open that person's list (`check_person`); otherwise the card is left out.

### 4.5 Mobile API (contract v1.7)

`tbo_smart.api.mobile.crm.get_call_today(user=None)` (GET, `crm` capability, lead scoring on):
the same object as the web page; fixture `docs/fixtures/crm.get_call_today.json`. See
`docs/tbo-smart-api-ess-crm.md` §6.3.

## 5. Follow-ups (`smart_leads/follow_ups.py`)

### 5.1 Rules (open leads)

| Rule | When | Who |
| --- | --- | --- |
| No follow-up | No open task with a due date | the owner |
| Re-engage | No follow-up and no touch for `lead_stale_days` (never touched: counted from created) | the owner |
| Due today | The next follow-up is today | the task's assignee (else the owner) |
| Overdue | The next follow-up's day has passed | the ladder |

### 5.2 Ladder (working days overdue; each level adds people)

| Level | From | Adds |
| --- | --- | --- |
| L0 | day 1 | the assignee |
| L1 | `lead_ladder_l1_days` (2) | the owner's BDM: their manager in the TBO Sales Hierarchy |
| L2 | `lead_ladder_l2_days` (4) | the sales head: "Head - Sales & Marketing" (Phase 2 setting), else every user with the Sales & Marketing Head role |
| L3 | `lead_ladder_l3_days` (7) | every user with `lead_manager_role` (Sales Manager) |

**Working days** come from the default company's holiday list (weekly offs and holidays,
`WorkCalendar`); without one every day counts. **Nothing is sent on a non-working day.**

### 5.3 Leave, disabled owners and Needs owner

- **Leave:** approved, submitted HRMS Leave Applications covering today (a half day doesn't
  count). Nobody on leave is told, at any level. When the assignee is away, the BDM covers
  (else the sales head), and the text ends "Shahana K is on leave until 17 Oct". The cover
  people (BDMs, sales heads) are checked for leave too, so a BDM on leave hands over to the
  sales head.
- **Disabled owners** (Anjana, Dhanish) are never told. Their leads go to
  `lead_fallback_owner` when set; otherwise nobody is reminded and the leads are listed under
  **Call today → Needs owner** for the sales head (`get_needs_owner`), who can reassign them
  (`reassign_leads`, a plain change of `lead_owner` under their permissions). The sales head's
  digest says how many need an owner. **Decision:** the engine never changes `lead_owner` by
  itself.

### 5.4 Delivery

- **Escalation notices:** when an overdue follow-up reaches a higher level, one in-app
  notification (Notification Log, so also mobile push through `tbo_smart.push`) to everyone told
  at that level, and the task's `tbo_escalation_level` is written (lowered again when the
  follow-up moves). Once per level.
- **Daily digest, 09:30 site time** (cron `30 9 * * *`, working days): each person gets one
  message with Overdue (with its level), Due today, Re-engage and No follow-up, eight lines per
  group, best score first, and a link to Call today (TBO Smart Settings → Lead page URL with
  `?view=call_today`, the hub's page; blank: the desk). With the lead pipeline on, it also lists the
  person's **meetings today** and **MOM pending** (`lead-pipeline.md` §5.4), and a BDE with only
  meetings gets it too. **Decision:** "due today" reminders are the
  digest itself, not one notification per lead: with 848 New leads, per-lead notifications would
  bury people.
  - **Teams** through the **Teams DM workflow URL** (TBO Smart Settings, Password): the same
    Power Automate workflow as the hub's HD Chat Settings "Direct Message Workflow URL". It
    receives `{"recipient": <email>, "type": "message", "attachments": [Adaptive Card]}`.
  - **Email** when no URL is set or Teams refuses (`frappe.sendmail`).
  - One in-app summary ("Lead follow-ups: 2 overdue · 5 due today · 40 no follow-up"), which
    also reaches phones.
  - `lead_digest_sent_on` (hidden) is written and committed before anything is sent, so a
    failure halfway never sends anyone two digests; people on leave get none.
- The Call today page shows the same counts for the person (`get_follow_up_summary`).

### 5.6 One-time AI triage (`smart_leads/triage.py`)

An admin action in **Call today → Triage** (Sales Manager or System Manager, `enable_lead_triage`
on, a provider set up), recorded as a **TBO Lead Triage** with **TBO Lead Triage Row**s.

1. **Preview** (`start_triage`, a background job on the long queue): open leads with no open
   follow-up that are New or untouched for `lead_stale_days`, best score first, at most
   `triage_max_leads`, none triaged (Ready or Applied) in the last 30 days. Batches of 20; the AI
   answers **Call again** or **Suggest close** with a reason (number-guarded; no answer =
   "without an answer"). Call again rows get a planned date from the next working day, at most
   `triage_daily_capacity` follow-ups per person per day **including the ones they already
   have**, best score first. The owner is the lead owner, else the fallback owner; a lead with
   neither is listed "Needs an owner first". The run shows its estimated AI cost (sum of its
   usage log rows). **Nothing on any lead changes.**
2. **Apply** (`apply_triage`, Ready runs only; the run row is locked first, so a double tap or
   two managers can't create the follow-ups twice): the dates are planned again from the next
   working day, then one "Sales follow-up" ToDo per Call again lead ("Call again: <reason>",
   for its person). Leads closed meanwhile are skipped.
A run left Queued or Running for 2 hours (its job died) is marked Failed when someone starts a
new preview.
3. **Suggest close** leads are only listed. A person ticks the ones they agree with and confirms
   **Close as Closed – Lost** (`close_triage_leads`): the status is saved under their own
   permissions with lost reason "Other" and the AI's reason in the lost notes. **The triage
   never closes a lead by itself.**

For the 848 New leads: the default 300 per run means three runs on consecutive days; each
skips the leads already triaged.

## 6. AI call notes, brief and WhatsApp (`smart_leads/notes.py`)

- **Note box** (lead page and Call today → Log call): the user types rough words (English,
  Malayalam or Manglish; the screen may offer dictation where the browser has it). **Draft with AI**
  (`draft_note`) returns a clean English note, call or note, the suggested next status (one of
  the 13 pipeline stages, else the current one) and a next follow-up date (tomorrow to 90 days,
  else none) with a reason. **Nothing is written.** The user edits and confirms (`save_note`):
  a note (Comment), or a call (a completed call Event with the words as its description); a
  call closes their own follow-ups on the lead due by today; the stage change (Closed – Lost
  gets lost reason "Other" with the note); and the next follow-up as a "Sales follow-up" ToDo
  assigned to them. **Save as written** works
  without the AI, and the box works with AI notes off (it then just saves).
- **Lead brief:** three lines (who and what they want; what happened; what to do next) from the
  migration note, notes, calls and status changes, cached on the lead with the content hash.
  `get_lead_insight` returns it with `stale` when the lead changed since; the screen then asks for
  a new one (`refresh_brief`, POST); a Refresh button does the same.
- **WhatsApp draft** (`draft_whatsapp(lead, language)`, `en` or `ml`): 2-4 sentences from the
  lead's context, signed with the user's first name; returns the message, the `+91` number and
  the `https://wa.me/<digits>?text=…` link. The screen shows the draft for editing before it
  opens WhatsApp. A lead with no usable number is refused.

## 7. API (`tbo_smart.api.smart_leads`)

| Method | Verb | Needs | Returns / does |
| --- | --- | --- | --- |
| `get_access` | GET | – | `{enabled, scoring, follow_ups, notes, triage, ai_ready, is_sales_head, can_manage, call_today_size}`; `{enabled: false}` without ERPNext or for a user without a sales role |
| `get_call_today(user?)` | GET | scoring | §4.3 |
| `get_people` | GET | scoring | Whose list I may open: `[{user, full_name, image}]` |
| `get_follow_up_summary(user?)` | GET | follow-ups | `{overdue, due_today, re_engage, no_follow_up, escalated}` for the person's own leads that I may read (`frappe.get_list`) |
| `get_needs_owner` | GET | sales head | Open leads whose owner is disabled or missing |
| `reassign_leads(leads, user)` | POST | sales head, write on each lead | `{moved}` |
| `get_lead_insight(lead)` | GET | read on the lead | Score, band, reason (scoring on), next follow-up, phone links, brief (notes on) |
| `refresh_brief(lead)` | POST | notes, write (it writes the cached brief) | The new brief; read-only users still see the cached one |
| `draft_note(lead, text)` | POST | notes, read | The proposal (§6) |
| `save_note(lead, text, kind, status?, follow_up_on?, follow_up_reason?)` | POST | read (write for a status) | `{lead, kind, note? (the Comment) or call? (the Event), status?, task?}` |
| `draft_whatsapp(lead, language)` | POST | notes, read | `{message, phone, url}` |
| `set_follow_up(lead, due, title?)` | POST | read | A Sales follow-up task for me |
| `set_status(lead, status, notes_text?)` | POST | write | `{status}` |
| `start_triage` / `get_triage(run?)` / `apply_triage(run)` / `close_triage_leads(run, leads, status)` | POST / GET / POST / POST | triage, Sales Manager or System Manager | §5.6 |

Every write is a POST, and every lead is checked with `frappe.has_permission` (the sales rules
and hierarchy, `tbo_smart.sales.permissions`) before anything is read or written.

## 8. Tests

`tbo_smart/tests/test_smart_leads_ai.py` and `test_smart_leads.py` (helpers in
`tbo_smart/test_utils.py`, AI mocked at the HTTP level with `mock_ai`): providers (parse, usage
and cost, billing failover and pause, auth and 5xx failover, a rejected request not failing
over, no key in errors, the bigger budget, test connection), the number guard, phone
normalisation, the migration note parser, settings validation and the settings API (no key
returned, System Managers only); scoring (status history and migration, closed leads, source
conversion rates, the AI adjustment bounded and read once, an invented reason dropped,
re-scoring on a new note, dashboard cards); Call today (order, size and `more`, team
permission, the off switch, the mobile shape against the fixture); follow-ups (each rule and
ladder level, leave cover, half days, disabled owners with and without a fallback, Needs owner
and reassignment, escalation notices once per level, Teams then email, the daily job once and
only when on, the summary); working days; the triage (preview buckets, never closing, apply,
capacity, invented reasons, managers only); notes (draft without writing, guard and bad dates,
confirmed call (an Event) with stage and follow-up, a call without my number still a call,
someone else's lead, the brief cache, WhatsApp with +91, AI off); and the no-ERPNext guard.

`make_user` lifts Frappe's 60-new-users-an-hour sign-up throttle for the test run: some tests
commit, so test users stay, and the suite creates more than 60 on a fresh site.

## 9. What the owner does after deploying

1. Deploy tbo_smart; `bench migrate` (fields, doctypes, the defaults patch), and the cutover
   (`erpnext-lead-cutover.md` §5).
2. Desk → **TBO Smart Settings** (System Manager):
   1. **OpenAI:** check the model is the exact model ID in the OpenAI dashboard (default
      `gpt-5.6-luna`), **Add** the API key, enter the input and output price per 1M tokens,
      **Save**, then **Test connection**.
   2. Optional fallback: Kimi's base URL `https://api.moonshot.ai/anthropic`, model and key.
3. Check one lead's migration note against §3.2.
4. Switch on **Lead scoring and Call today**; the scores fill in that night (or at once for a
   lead that changes). Then **AI reads notes to adjust scores** if wanted.
5. **Follow-ups:** set **Head - Sales & Marketing**, a **Fallback owner** for Anjana's and
   Dhanish's leads (or leave it blank and reassign them under Call today → Needs owner), paste
   the **Teams DM workflow URL** (the same as the hub's), Save, **Send me a test digest**, then
   switch on **Lead follow-ups and escalation**.
6. Switch on **AI call notes, lead brief and WhatsApp drafts**.
7. **Triage:** switch it on, open **Call today → Triage**, **Run a preview**, read it, then
   **Create follow-ups**, and close the Suggest close leads you agree with. Repeat on the next
   days until the New leads are done.
8. Watch the cost in **TBO AI Usage Log** (desk, System Manager).
9. **AI runs leads** (§11): with a provider tested, switch it on. Each open lead is read when
   it next changes (and the quiet ones from 09:00 the next day); the hub's lead page shows
   "AI suggests" and the Leads overview the day's plan. Check `ai_daily_lead_limit` (200 reads
   a day) against the usage log.

## 10. Known limits

- The AI adjustment runs only at night; a note added today changes the AI part of the score
  tomorrow (the base score changes at once), with AI runs leads on too.
- AI runs leads reads a lead only when it changes, so leads nobody touches are read only by the
  gone-quiet nudge; the first switch-on doesn't read all open leads at once (cost).
- No per-person digest time; 09:30 site time for everyone.
- The pause, failure status and alert keys live in Redis; a cache flush forgets a pause.
- Teams delivery needs the Power Automate workflow; there is no channel post here.

## 11. AI runs leads (`smart_leads/autopilot.py`)

The owner (2026-10-16): "Leads fully controlled by AI", with people approving anything that goes
out. It does for a lead what the hub already does for a ticket (tbocloud/helpdesk
`ai_suggestion.py`, `customer_health.py`, `duplicates.py`): the AI reads and proposes, rules
judge health, a person acts. One master switch, **AI runs leads** (`enable_ai_leads`, off), and
a provider (§2). The other switches still decide their own parts (scoring, follow-ups, notes).

### 11.1 What the AI does by itself

| When | What | Written |
| --- | --- | --- |
| A lead is created, its stage or owner changes, or a note, a call or logged activity (Event) or an email (Communication) is added (a background job per lead, deduplicated, after commit; not during an import) | With lead scoring on: scores it (`score_leads`, the base score; the AI adjustment stays nightly, §4.2). Then reads it (`refresh_insight`) | the score; `tbo_ai_*` |
| The read | **Summary** (two sentences), **sentiment** and **buying intent** from the notes, calls and the three newest emails, up to four **signals** (phrases that show it), the **next best action** (what, why, channel: call / WhatsApp / email / meeting, by when: today to 30 days), a **stage to suggest** (an open stage only, with a reason) and a **message draft** (WhatsApp or email) for the next step | `tbo_ai_insight` and its fields |
| The lead has no open follow-up | The next action becomes the owner's **Sales follow-up** ("AI: Call to fix the demo", on its day, with the why). A person's own follow-up always wins: nothing is added when one is open | a ToDo |
| Every read | A line on the lead's timeline **by TBO AI**: "Read the lead: Positive sentiment, High intent. Suggests: … (by 21 Oct 2026). Scheduled it as the follow-up. Suggests moving it to …" | a Comment of type Info, `comment_by` "TBO AI" (not a note: it never counts as a touch and never wakes the AI again) |
| 09:00 every day (cron, before the 09:30 digest) | **Gone quiet** (`nudge_stale_leads`): open leads with an owner, no follow-up planned and no touch for `lead_stale_days` (never touched: counted from created), best score first, at most 5 per owner a day, each at most once a week: read again (with a re-engagement draft); each owner gets **one** notification ("4 of your leads have gone quiet. AI drafted what to send; check it and press Send.") | `tbo_ai_nudged_on`, Notification Log (also push) |
| The 09:30 follow-up digest (§5.4) | Each line names the AI's next action: "Anu Textiles: due today; AI suggests: Call to fix the demo" | – |

**Read once per change:** a read is skipped when nothing a person wrote or decided changed and
no email came in since the last one (`tbo_ai_insight_hash`: the facts' content hash plus the
emails), for a closed lead, and past the day's cap: at most `ai_daily_lead_limit` (200) reads
a day (one atomic Redis counter per day, so jobs side by side can't pass it). The nightly
scoring keeps its own cap (§4.2); together they are the AI's daily cost. A failed AI call writes nothing and is tried on the next
change. Feature "Lead insight" in the usage log; "Lead email draft" for email drafts.

**Number guard:** every text the AI writes (summary, signals, the action and its why, the stage
reason, the message) passes `ai.guarded` against the lead's facts, the emails, today and the
salesperson's name; a text with an invented number is dropped (a dropped message means no
draft; a dropped action means no follow-up).

### 11.2 What needs a person

- **Nothing goes to a customer without a person pressing Send.** Drafts are only stored or
  returned; the hub opens the person's own WhatsApp (`wa.me` with the text) or mail app, and
  `log_message_sent` records it on the lead as theirs afterwards. No email is sent from here.
- **Stage moves wait for Apply.** The AI never changes a stage; `apply_ai_stage` moves it as
  the person (their write permission, their name in the stage log) and clears the suggestion;
  `dismiss_ai_stage` just clears it. **Won and Lost are never suggested** (the prompt says so
  and only open stages are kept) and never applied here.
- Reassigning, closing and quotations stay where they were.

### 11.3 Health and duplicates (no AI)

- **Health** (`lead_health`), like the hub's customer health: **At risk** with any at-risk
  signal or two watch ones, **Watch** with one, else **Healthy**. Signals: no touch for
  `lead_stale_days` (at risk) or half of it (watch); no follow-up planned (watch); the
  follow-up 3+ days overdue (at risk) or overdue (watch); the AI read the customer as
  unhappy (watch); no phone and no email (at risk). Each with its words.
- **Duplicates** (`find_duplicates`): up to five other leads the person may see with the same
  mobile or phone (last ten digits after `normalise_phone`), the same email, or the same
  company name (four letters or more), each with what matched.

### 11.4 The daily plan

`daily_plan(user)`: the person's Call today list (lead scoring on; why: follow-up overdue,
due today, best score with nothing planned), else their follow-ups due by today, then up to
five of their Hot leads not listed yet; each with the AI's next action, its day and the
intent. The hub shows it on the Leads overview (`get_overview`'s `ai`), with the scope's
counts of high-intent leads, unhappy customers and stage suggestions waiting for a person.

### 11.5 API (`tbo_smart.api.hub_leads`, `hub-leads-api.md` §2.6)

`get_lead_ai`, `refresh_lead_ai` (POST, write on the lead, once a minute), `apply_ai_stage`
(POST), `dismiss_ai_stage` (POST), `draft_lead_message` (POST; AI runs leads or AI notes on),
`log_message_sent` (POST), and `get_overview`'s `ai`.

### 11.6 Tests

`tbo_smart/tests/test_ai_leads.py` (AI mocked at the HTTP level with `mock_ai`; helpers
`enable_ai_leads`, `ai_lead_insight`, `make_lead_email` in `test_utils.py`): off by default; a
note makes the AI read the lead, plan a follow-up for the owner and log it as TBO AI; a
person's own follow-up wins and an unchanged lead isn't read again; Won/Lost suggestions and
invented numbers dropped; emails read; apply (as the person, stage log) and dismiss; health
and duplicates within the person's view; drafts never send; the overview's plan; gone-quiet
nudges once; the digest line; the daily cap.

## Changelog

- **v1.3 (2026-10-19):** AI runs leads (§11): the master switch, the lead insight on change
  with the next action as the follow-up, stage suggestions applied by a person, health,
  duplicates, drafts, the daily plan, gone-quiet nudges, the digest's AI line.

- **v1.2 (2026-10-16):** on ERPNext's Lead (`erpnext-lead-cutover.md`): scores in `tbo_score` /
  `tbo_temperature`, follow-ups ToDos, notes Comments, calls Events, stage changes from the
  stage log; the dashboard cards behind `api/dashboard.py`; links to the hub's lead page.

- **v1.1 (2026-10-16):** the lead pipeline statuses (`lead-pipeline.md`): status points, the
  open statuses and the triage's Closed – Lost; the morning digest adds meetings today and
  pending minutes while the pipeline is on.
- **v1.0 (2026-10-15):** first build: AI providers, scoring and Call today, follow-ups with the
  ladder and digest, AI notes, brief and WhatsApp, the triage, mobile `get_call_today`.
