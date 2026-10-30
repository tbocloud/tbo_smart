# Daily call batches

**Status:** built 2026-10-30. Salespeople used to see all their leads at once and cherry-picked.
Now each person is handed a **batch** per time slot and must call it within the slot. The hub's
**Today's calls** tab (tbocloud/helpdesk) shows it through `docs/hub-leads-api.md` §2.12.

## 1. Rules, per TBO Lead Department

Set on the department (hub: Settings, CRM, Lead departments):

| Field | Default | Meaning |
| --- | --- | --- |
| `call_batches_enabled` | off | Hand out batches. Off until the owner switches a department on. |
| `call_slots` | Morning 10:00-13:00 (50), Afternoon 14:00-18:00 (50) | Rows of label, start, end, size. Blank table: these two. No overlaps. |
| `daily_call_cap` | 100 | Leads handed to one person in a day, retries not counted. A member's own `daily_call_cap` (Members table) replaces it. |
| `pull_unassigned` | on | When the batch has room, pull the department's unassigned open leads. |
| `overdue_block_limit` | 10 | Overdue calls above this block the next batch and, at slot end, tell the heads. |
| `campaign_industry/state/district` | blank | Narrow new leads (priority 3-5, not follow-ups) to this industry, state or district. A column Lead does not have yet (`tbo_district` arrives with lead categories) is skipped. |

## 2. Doctypes

- **TBO Call Batch**: `user`, `department`, `batch_date`, `slot`, `starts_at`, `ends_at`, `status`
  (Active / Closed), `items`.
- **TBO Call Batch Item**: `lead`, `priority` (1-5), `priority_reason`, `outcome`, `called_at`,
  `retry_at`, `attempts`, `note`, `is_repeat` (a retry or call back row of a lead already in the
  batch; it does not count towards the batch size).
- **TBO Call Slot**: the department's `call_slots` rows.
- Lead fields (made on install and migrate, `sales/call_batches/setup.py`): `tbo_callback_at`
  (the agreed call-back time) and `tbo_wrong_number`.

## 3. Filling a batch (`sales/call_batches/fill.py`)

In order, up to the slot size and what is left of the day's cap. Only open leads (not Closed,
not On Hold) of the department:

1. The person's leads with a follow-up (an open ToDo) due today or earlier. A lead with a call
   back later than this slot's end waits for the slot it falls in.
2. Their last working day's Not answered and Busy (the last outcome of the day wins).
3. Their leads never called (no call Event), hot first, then New Lead, then newest.
4. Their leads quiet for longest (the newest activity from `sales/stale.py`).
5. If `pull_unassigned`: unassigned leads of the department, hot then newest. They are given to
   the person (owner and a ToDo assignment). The rows are read `FOR UPDATE SKIP LOCKED`, so two
   people being handed batches at once never get the same lead.

Steps 3 to 5 need a phone number and follow the campaign filter. A lead is never in two active
batches and never twice on one day. A lead owned by the person but outside any department is not
batched.

## 4. The clock (`sales/call_batches/clock.py`)

`run_call_batch_clock` runs every 5 minutes. It closes active batches whose slot is over (their
unfinished items become **overdue**) and, when a department's overdue count for a person is above
`overdue_block_limit`, notifies the heads. Then for every slot that is running it creates the batch
of every member who has none for that slot yet (one per person, slot and day, so a missed tick only
makes it late; a batch made after the slot began is not shortened). People are skipped on a
holiday (their Employee's holiday list), on approved leave (HRMS Leave Application, whole days) and
on weekly offs. Without an Employee record or holiday list only Sundays are off.

## 5. Outcomes (`outcomes.py`)

`record_call_outcome` logs the call exactly as `log_call` does (`log_manual_call`: a TBO Call and
its Event) and sets the item's outcome, which removes it from the queue:

| Outcome | Logged as | Then |
| --- | --- | --- |
| Connected | Connected | Due follow-ups of the lead close. A `callback_at` sets the next follow-up. The hub asks for a note and stage. |
| Not answered / Busy | No answer / Busy | One more try 2 hours later in the same batch, if the batch is still running then; after that, or when the slot ends first, the lead returns tomorrow as priority 2. |
| Call back | Connected | `callback_at` (required) becomes `tbo_callback_at` and a follow-up ToDo; inside this batch the lead returns to the queue at that time. |
| Not interested | Connected | Due follow-ups close. The hub offers Closed - Lost with a reason (`set_stage`). |
| Wrong number | No answer | `tbo_wrong_number` is set and a note added. |

A call logged on the lead by any other route (MyOperator, the Log call dialog, the app) counts
too: `sync_calls` gives the person's open items the outcome of their newest later call (Answered:
Connected; Missed or Voicemail: Not answered). It runs whenever the queue is read or a batch asked
for.

## 6. Reading and the next batch

`get_my_calls`: the running batches' counts, the pending, scheduled and overdue calls with a lead
summary, time left and whether the next batch is allowed. Overdue calls stay for 7 days and are
never removed; a lead that is back in a running batch is not listed overdue twice.

`get_next_batch` hands out another batch of the current slot (labelled `<slot> +2`, ...) or, between
slots, the next slot early, only when: no call is pending (retries and call backs that are not yet
due do not count), overdue calls are within the limit, the day's cap is not reached, it is a working
day, a slot is open and there is a lead to call. Otherwise it returns `{created: false, reason}`.

`get_call_batches_report(date)`: per person and batch, size, called, connected (answered), overdue.
Sales Managers, System Managers and department heads (their departments).

## 7. Files

`tbo_smart/sales/call_batches/` (`__init__` rules, `fill`, `outcomes`, `state`, `next_batch`,
`reads`, `clock`, `settings`, `setup`); doctypes under `tbo_smart/tbo_smart/doctype/tbo_call_*`;
endpoints in `api/hub_leads.py`; tests `tests/test_call_batches.py`, helpers in `test_utils.py`.

## 8. Decisions

- Batches are off until a department switches them on, so deploying changes nothing by itself.
- The follow-up field in use is the lead's open ToDo (`next_follow_ups`); call-back time-of-day is
  kept in `tbo_callback_at` because a ToDo only has a date.
- Closing due follow-ups on a conversation keeps yesterday's finished work from coming back.
