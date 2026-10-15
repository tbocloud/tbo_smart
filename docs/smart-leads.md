# Smart leads: scoring, Call today, follow-ups and AI notes (living spec, v1.0)

**Status:** built 2026-10-15, every feature off by default. This file is the living spec: it
describes what is built, and every change to the feature updates it in the same pull request.
The CRM screens are in tbocloud/crm, `docs/tbo-smart-leads.md`.

- **Site:** india.teambackoffice.com (tboindia): Frappe v15, ERPNext, HRMS, the Frappe CRM fork
  and tbo_smart.
- **Why:** 1,572 leads came over from the old ERPNext site on 2026-10-14 (New 848, Junk 371,
  Nurture 125, Qualified 85, Contacted 83, Unqualified 35, Converted 25). 94% have only a mobile
  number. No CRM Task or follow-up existed, two owners (Anjana, Dhanish) are disabled users, and
  nobody could tell which of the 848 New leads to call first.
- **Owner's direction:** "proceed, recommended way, no questions".

## 1. Switches

All in **TBO Smart Settings**, edited in the CRM at **Settings → TBO → TBO smart leads**
(System Managers). Each is off until the owner turns it on; on a site without the CRM app
(tbo-smart.tbocloud.in) every switch reads as off and nothing is set up
(`tbo_smart.smart_leads.switch` uses `tbo_smart.finance.crm_installed`, the lesson of
tbo_smart#14).

| Field | Label | Default | What it switches on |
| --- | --- | --- | --- |
| `enable_lead_scoring` | Lead scoring and Call today | 0 | Nightly and on-change scores, the Call today page and API, the dashboard card |
| `lead_scoring_use_ai` | AI reads notes to adjust scores | 0 | The AI adjustment (needs scoring and a provider) |
| `enable_lead_follow_ups` | Lead follow-ups and escalation | 0 | The rules, the escalation notices and the 09:30 digest |
| `enable_lead_notes` | AI call notes, lead brief and WhatsApp drafts | 0 | Drafts in the note box, the brief, WhatsApp drafts |
| `enable_lead_triage` | AI triage of stale leads | 0 | The Triage tab (Sales Manager / System Manager) |

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

### 3.1 Fields (created on install and every migrate, CRM sites only: `smart_leads/setup.py`)

CRM Lead's own **`lead_score`** (Int) and **`lead_temperature`** (Select Cold / Warm / Hot) are
reused for the score and the band. **Decision:** upstream Frappe CRM writes them only from its
automation engine (`crm/automation/scoring.py`, additive `adjust_lead_score`), which needs Frappe
v16 and does not run on tboindia (v15). If the site moves to v16 and someone builds scoring
automations, the nightly score would overwrite their adjustments: turn one of them off.

| CRM Lead field | Type | Purpose |
| --- | --- | --- |
| `tbo_score_reason` | Small Text, read-only | One line: why the lead has its score |
| `tbo_scored_at` | Datetime, hidden | When it was last written |
| `tbo_ai_adjust`, `tbo_ai_reason`, `tbo_ai_hash` | hidden | The last AI read and what it read (content hash) |
| `tbo_ai_brief`, `tbo_ai_brief_hash`, `tbo_ai_brief_at` | hidden | The cached three-line brief |

Scores are written with `frappe.db.set_value(..., update_modified=False)`: no Version row, no
`modified` bump, no CRM `on_update` (SLA, assignment).

**Follow-ups are CRM Tasks.** **Decision:** a CRM Task with the finance workflow's
`tbo_task_type` = **"Sales follow-up"** (Phase 2's field, reused; `setup_smart_leads` also
creates the finance fields), not a `tbo_next_follow_up` field on the lead. The CRM Tasks page,
the lead's Tasks tab, the mobile app's follow-ups (`get_my_follow_ups`) and CRM's own reminders
already use CRM Tasks; a second date field would disagree with them. The next follow-up is the
lead's earliest open task with a due date, whatever its type. Escalation reuses the task's
`tbo_escalation_level`.

### 3.2 The migration note (`smart_leads/migration.py`)

Each migrated lead has an FCRM Note titled "Migrated from tboindia-old <id>". The parser reads
"Label: value" lines, "Label - value" lines and two-cell table rows, with or without HTML, and
knows these labels: status (also "lead status", "original status"), source, owner ("lead
owner"), created ("created on", "creation"), remarks (several lines, until the next label),
priority, project type ("type of project"). Unknown labels are ignored.
**[verify on site]** The note's exact layout was not available offline; check one note before
switching scoring on, and add a label to `LABELS` if it differs. The migration note is never
counted as a touch, and its title keeps it out of the "notes since" the AI reads.

### 3.3 Facts (`smart_leads/facts.py`)

`LeadFacts(names)` loads, in a fixed number of queries: the lead fields, the migration notes,
notes and calls since, open tasks (earliest first), done tasks, status changes **after the
first log row** (the first row is the lead's insert, for the migrated leads the 2026-10-14
import), emails (Communication) and comments. **Last touch** = the newest call, note, done
follow-up, status change, email or comment. **Created** = the migration note's created date,
else the lead's creation. Scoring, Call today, the follow-up rules, the brief and the triage all
read these facts.

## 4. Scoring and Call today

### 4.1 The base score (0-100, `smart_leads/scoring.py`)

| Part | Points |
| --- | --- |
| Status | Qualified 30, Contacted 18, Nurture 12, New 10 |
| Status changed in the last 30 days | +8 forward, −3 back to an earlier status (CRM Lead Status position) |
| Source's conversion rate here | up to +12, against the best source (sources with 10+ leads; converted = `converted` or status Converted) |
| Project type's conversion rate (migration note) | up to +6, the same way |
| Last touch | ≤3 days +15, ≤7 +10, ≤14 +6, ≤30 +3 |
| Age (original created date) | ≤7 days +12, ≤30 +8, ≤90 +4, ≤365 +1 |
| Status before the migration | Interested / Opportunity / Quotation +10, Replied +6, Open +3, Lead +1, Lost Quotation −5, Do Not Contact −20 |
| Priority before the migration | High / Urgent +5, Medium +2 |
| Follow-up overdue | +5 |
| No phone and no email | −20 |

Closed leads (any status but New, Contacted, Nurture, Qualified, or converted) score 0, Cold,
"Closed (Junk)". Bands: **Hot ≥ 55, Warm ≥ 30, Cold** below. The reason line is the status,
then the parts that moved the score most, e.g. "Contacted · was Interested before the migration
· Website leads convert 9% · last touch 6 days ago". Conversion rates are cached 6 hours and
recomputed at the start of the nightly run.

### 4.2 The AI adjustment

With `lead_scoring_use_ai` on and a provider set up, the nightly run sends up to
`ai_daily_lead_limit` open leads **whose content hash changed** (status, notes, remarks, status
changes; not the passing days) in batches of 10. The AI returns `adjust` (clamped to ±15) and a
reason; a reason with an invented number is dropped with its adjustment; a failed batch is
skipped and retried the next night. The result is cached on the lead (`tbo_ai_*`), so a lead is
read again only after it changes. The reason line then starts with the AI's reason.

**When scores change:** daily (`scheduler_events.daily`, `score_all`), and as a background job
(deduplicated per lead) when a lead's status, owner or conversion changes, or a note, call or
task is added on it. The on-change score uses the cached AI adjustment; the AI itself runs only
at night, so cost stays capped.

### 4.3 Call today (`smart_leads/call_today.py`)

Built live from the facts for one person: their open leads that the session user may read
(`frappe.get_list`, so CRM's own permissions and hierarchy apply), in this order:

1. follow-up overdue, oldest first;
2. follow-up due today;
3. no follow-up and no touch today, best score first.

Leads with a follow-up on a later day are left out (they have a plan), and so are leads touched
today with no follow-up. The list holds `call_today_size` rows; `more` counts the rest. Each row:
score, band, reason, why it is listed, phone, `+91`-normalised E.164, `tel:` and `wa.me` links,
email, last touch (when, what, days) and the follow-up.

**Who sees whose list:** your own; your sales team's (CRM Sales Hierarchy) if you lead one;
anyone's with crm_scope all (System Manager, a Sales Manager outside the hierarchy) or as a
sales head (`is_sales_head`: System Manager, Sales Manager, Sales & Marketing Head role or the
"Head - Sales & Marketing" user). Anything else is a PermissionError.

**Phone normalisation** (`smart_leads/utils.normalise_phone`, mirrored in the CRM): digits only;
`+` or `00` starts an international number kept as it is; otherwise a trunk 0 before 10 digits
is dropped and 10 digits get **+91**; 8-15 digits or nothing.

### 4.4 Dashboard

`crm_dashboard_providers` / `crm_dashboard_layout` add two number cards
(`smart_leads/dashboard.py`): **Call today** (rows plus `more` for the dashboard's user, else me;
while scoring is on) and **Overdue lead follow-ups** (my open tasks on open leads due before
today; while follow-ups are on, lower is better).

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
| L1 | `lead_ladder_l1_days` (2) | the owner's BDM: their manager in the CRM Sales Hierarchy |
| L2 | `lead_ladder_l2_days` (4) | the sales head: "Head - Sales & Marketing" (Phase 2 setting), else every user with the Sales & Marketing Head role |
| L3 | `lead_ladder_l3_days` (7) | every user with `lead_manager_role` (Sales Manager) |

**Working days** come from the default company's holiday list (weekly offs and holidays,
`WorkCalendar`); without one every day counts. **Nothing is sent on a non-working day.**

### 5.3 Leave, disabled owners and Needs owner

- **Leave:** approved, submitted HRMS Leave Applications covering today (a half day doesn't
  count). Nobody on leave is told, at any level. When the assignee is away, the BDM covers
  (else the sales head), and the text ends "Shahana K is on leave until 17 Oct".
- **Disabled owners** (Anjana, Dhanish) are never told. Their leads go to
  `lead_fallback_owner` when set; otherwise nobody is reminded and the leads are listed under
  **Call today → Needs owner** for the sales head (`get_needs_owner`), who can reassign them
  (`reassign_leads`; CRM shares and assigns them as on any owner change). The sales head's
  digest says how many need an owner. **Decision:** the engine never changes `lead_owner` by
  itself.

### 5.4 Delivery

- **Escalation notices:** when an overdue follow-up reaches a higher level, one in-app
  notification (Notification Log, so also mobile push through `tbo_smart.push`) to everyone told
  at that level, and the task's `tbo_escalation_level` is written (lowered again when the
  follow-up moves). Once per level.
- **Daily digest, 09:30 site time** (cron `30 9 * * *`, working days): each person gets one
  message with Overdue (with its level), Due today, Re-engage and No follow-up, eight lines per
  group, best score first, and a link to Call today. **Decision:** "due today" reminders are the
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
2. **Apply** (`apply_triage`, Ready runs only): the dates are planned again from the next
   working day, then one "Sales follow-up" CRM Task per Call again lead ("Call again: <reason>",
   10:00, assigned to its person). Leads closed meanwhile are skipped.
3. **Suggest close** leads are only listed. A person ticks the ones they agree with and confirms
   **Close as Junk / Unqualified** (`close_triage_leads`): the status is saved under their own
   permissions with lost reason "Other" and the AI's reason in the lost notes. **The triage
   never closes a lead by itself.**

For the 848 New leads: the default 300 per run means three runs on consecutive days; each
skips the leads already triaged.

## 6. AI call notes, brief and WhatsApp (`smart_leads/notes.py`)

- **Note box** (lead page and Call today → Log call): the user types rough words (English,
  Malayalam or Manglish; the CRM offers dictation where the browser has it). **Draft with AI**
  (`draft_note`) returns a clean English note, call or note, the suggested next status (one of
  the CRM Lead Statuses, else the current one) and a next follow-up date (tomorrow to 90 days,
  else none) with a reason. **Nothing is written.** The user edits and confirms (`save_note`):
  an FCRM Note, or a CRM Call Log with its note (a call by someone with no mobile number on
  their profile is saved as a note "Call: …"); a call closes their own follow-ups on the lead
  due by now; the status change (Junk / Unqualified get lost reason "Other" with the note); and
  the next follow-up as a "Sales follow-up" task assigned to them. **Save as written** works
  without the AI, and the box works with AI notes off (it then just saves).
- **Lead brief:** three lines (who and what they want; what happened; what to do next) from the
  migration note, notes, calls and status changes, cached on the lead with the content hash.
  `get_lead_insight` returns it with `stale` when the lead changed since; the CRM then asks for a
  new one (`refresh_brief`, POST); a Refresh button does the same.
- **WhatsApp draft** (`draft_whatsapp(lead, language)`, `en` or `ml`): 2-4 sentences from the
  lead's context, signed with the user's first name; returns the message, the `+91` number and
  the `https://wa.me/<digits>?text=…` link. The CRM shows the draft for editing before it opens
  WhatsApp. A lead with no usable number is refused.

## 7. API (`tbo_smart.api.smart_leads`)

| Method | Verb | Needs | Returns / does |
| --- | --- | --- | --- |
| `get_access` | GET | – | `{enabled, scoring, follow_ups, notes, triage, ai_ready, is_sales_head, can_manage, call_today_size}`; `{enabled: false}` without the CRM |
| `get_call_today(user?)` | GET | scoring | §4.3 |
| `get_people` | GET | scoring | Whose list I may open: `[{user, full_name, image}]` |
| `get_follow_up_summary(user?)` | GET | follow-ups | `{overdue, due_today, re_engage, no_follow_up, escalated}` for the person's own leads |
| `get_needs_owner` | GET | sales head | Open leads whose owner is disabled or missing |
| `reassign_leads(leads, user)` | POST | sales head, write on each lead | `{moved}` |
| `get_lead_insight(lead)` | GET | read on the lead | Score, band, reason (scoring on), next follow-up, phone links, brief (notes on) |
| `refresh_brief(lead)` | POST | notes, read | The new brief |
| `draft_note(lead, text)` | POST | notes, read | The proposal (§6) |
| `save_note(lead, text, kind, status?, follow_up_on?, follow_up_reason?)` | POST | read (write for a status) | `{lead, kind, note, call?, status?, task?}` |
| `draft_whatsapp(lead, language)` | POST | notes, read | `{message, phone, url}` |
| `set_follow_up(lead, due, title?)` | POST | read | A Sales follow-up task for me |
| `set_status(lead, status, notes_text?)` | POST | write | `{status}` |
| `start_triage` / `get_triage(run?)` / `apply_triage(run)` / `close_triage_leads(run, leads, status)` | POST / GET / POST / POST | triage, Sales Manager or System Manager | §5.6 |

Every write is a POST, and every lead is checked with `frappe.has_permission` (CRM's own rules
and hierarchy) before anything is read or written.

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
confirmed call with status and follow-up, a call without my number, someone else's lead, the
brief cache, WhatsApp with +91, AI off); and the no-CRM guard.

`before_tests` lifts Frappe's 60-new-users-an-hour sign-up throttle for the test run: CRM inserts
commit, so test users stay, and the suite now creates more than 60 on a fresh site.

## 9. What the owner does after deploying

1. Deploy tbocloud/crm, then tbo_smart; `bench migrate` (fields, doctypes, the defaults patch).
2. CRM → **Settings → TBO → TBO smart leads** (System Manager):
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

## 10. Known limits

- The AI adjustment runs only at night; a note added today changes the AI part of the score
  tomorrow (the base score changes at once).
- No per-person digest time; 09:30 site time for everyone.
- The pause, failure status and alert keys live in Redis; a cache flush forgets a pause.
- Teams delivery needs the Power Automate workflow; there is no channel post here.

## Changelog

- **v1.0 (2026-10-15):** first build: AI providers, scoring and Call today, follow-ups with the
  ladder and digest, AI notes, brief and WhatsApp, the triage, mobile `get_call_today`.
