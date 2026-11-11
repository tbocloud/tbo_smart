# Daily call batches

**Status:** built 2026-10-30. Salespeople used to see all their leads at once and cherry-picked.
Now each person is handed a **batch** per time slot and must call it within the slot. The hub's
**Today's calls** tab (tbocloud/helpdesk) shows it through `docs/hub-leads-api.md` §2.13.

## 1. Rules, per TBO Lead Department

Set on the department (hub: Settings, CRM, Lead departments):

| Field | Default | Meaning |
| --- | --- | --- |
| `call_batches_enabled` | off | Hand out batches. Off until the owner switches a department on. |
| `call_slots` | Morning 10:00-13:00 (40), Afternoon 14:00-16:00 (30), Evening 16:00-18:00 (30) | Rows of label, start, end, size. Blank table: these three (100 a day, the daily cap). No overlaps. |
| `daily_call_cap` | 100 | Leads handed to one person in a day, retries not counted. A member's own `daily_call_cap` (Members table) replaces it. |
| `pull_unassigned` | on | When the batch has room, pull the department's unassigned open leads. |
| `overdue_block_limit` | 10 | Overdue calls above this block the next batch and, at slot end, tell the heads. |
| `auto_assign` | on | At `auto_assign_at`, top up the planned members nobody handed leads to by hand today (§14). |
| `auto_assign_at` | 11:00 | The time of the auto-assign (site time); blank reads as 11:00. |
| `auto_assign_skip_sunday` | on | No auto-assign on Sundays. |
| `auto_assign_skip_saturdays` | 2nd | Saturdays of the month without auto-assign: None, 2nd, 2nd and 4th or All. |
| `campaign_industry/state/district/product` | blank | Narrow new leads (priority 3-5, not follow-ups) to this industry, state, district or product (`lead-departments.md` §10; the product must be one of the department's active products). A column Lead does not have yet (`tbo_district` arrives with lead categories) is skipped. |

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

1. The person's leads with a follow-up (an open ToDo) due today or earlier, set by a person (a
   call back, a date they chose). A follow-up the AI scheduled ("AI: …", smart-leads.md §11)
   does not count here: it came after every call and brought each called lead back first, ahead
   of the leads never called. A lead with a call back later than this slot's end waits for the
   slot it falls in.
2. Their last working day's Not answered and Busy (the last outcome of the day wins).
3. Their leads never called (no call Event), hot first, then New Lead, then newest.
4. Their leads quiet for longest (the newest activity from `sales/stale.py`).
5. If `pull_unassigned`: unassigned leads of the department, hot then newest. They are given to
   the person (owner and a ToDo assignment). The rows are read `FOR UPDATE SKIP LOCKED`, so two
   people being handed batches at once never get the same lead.

The ToDo assignment a pulled or handed-out lead gets (§12) has **no date**. Frappe dates an
assignment today by default, and step 1 reads any dated open ToDo as a follow-up due: every
handed lead would have jumped all later batches from the next day on, ahead of the campaign
filter. The patch `undated_lead_assignments` cleared the date on the open assignments made before
("Pulled into your daily calls", "Assigned to you on Assign leads"); real follow-ups keep theirs.
Passing no date to the assignment is not enough (the ToDo's `date` defaults to Today on insert), so
`assign_undated` (`call_batches/__init__.py`) clears it right after; the patch runs again for the
ToDos dated before that fix.

Steps 3 to 5 need a phone number and follow the campaign filter (the department's campaign, a limit that
always wins, then the person's focus, §10). A lead is never in two active
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
| Not interested | Connected | Due follow-ups close. The lead becomes Not Interested (lost reason "Not interested"). |
| Wrong number | No answer | `tbo_wrong_number` is set, a note added and the lead becomes Wrong Number. |

A call logged on the lead by any other route (MyOperator, the Log call dialog, the app, the
customer calling back) counts too: `sync_calls` gives the person's open items (a retry or a call
back included) the outcome of their next later call (Answered: Connected; Missed or Voicemail: Not
answered). An incoming call counts only when it was answered. When the lead's last call of the day
was Not answered or Busy and nothing of it is left to call (no retry, e.g. the slot ended first),
a later answered call that day adds a "Called back" item with Connected. It runs whenever the
queue is read or a batch asked for.

**Counts** (Today's calls and the report): `size` and `called` are the batch's own items (retries,
call backs and called-back items are not counted again); a lead is `connected` when **any** call of
it in the batch was answered, so Not answered followed by a Connected retry, call back or the
customer calling back counts connected (`state.reached_leads`).

## 6. Reading and the next batch

`get_my_calls`: the running batches' counts, the pending, scheduled and overdue calls with a lead
summary, time left and whether the next batch is allowed. Every call also carries the lead's
`product`, its `intro_line` and `calling_for` (the product, else the department): "Calling for: PhoebiX". Overdue calls stay for 7 days and are
never removed; a lead is listed overdue once (its latest missed call), and not at all while it is
back in a running batch or once it was called on that day or later. The overdue count and the
`overdue_block_limit` count leads, not missed days.

`get_next_batch` hands out another batch of the current slot (labelled `<slot> +2`, ...) or, between
slots, the next slot early, only when: no call is pending (retries and call backs that are not yet
due do not count), overdue calls are within the limit, the day's cap is not reached, it is a working
day, a slot is open and there is a lead to call. Otherwise it returns `{created: false, reason}`.

`get_call_batches_report(date)`: per person and batch, size, called, connected (answered), overdue.
Sales Managers, System Managers and department heads (their departments).

## 10. Calling for: the BD's switch (`sales/call_batches/focus.py`)

A salesperson in more than one department, or in one with products (GrowthX: PhoebiX, BillaX),
chooses what they are calling for: a department, optionally one of its products and one
district. It is kept per person in a **TBO BD Focus** (named by the user: one row each).

- **Who may pick what:** only departments they are a member of that have call batches on, and
  that department's *active* products. A department's `campaign_product` / `campaign_district`
  (a manager's limit) is the only choice and always wins over the person's. Districts offered
  are those of the leads they could be handed.
- **What it changes:** the clock opens each slot's batch only for the focus department (no
  focus: every department, as before); new leads in a batch (steps 3 to 5, never follow-ups or
  retries) follow the focus product and district. Calls already handed out stay on the list until
  done.
- **Switching** (`set_calling_focus`) saves the choice and starts the next batch for it when the
  rules allow (nothing pending, overdue within the limit, under the daily cap, a slot open).
  When they do not, the answer says so in plain words and the next batch follows the switch.
- A choice that no longer holds (batches switched off, the person left the department, the
  product switched off) is ignored, part by part.

### Staff how to use (BDs)

1. Open **Leads**. You land on **Today's calls**: only your own calls, nobody else's.
2. At the top, **Calling for** shows what you call now (for example *Hotels (StayVeX)*). Tap it to
   switch to another department or product, and pick a district if you want. Tap **Start calling**.
3. Tap **Call** on a lead. When you come back, a sheet asks **how did it go?** Tap one big button:
   Connected, Not answered, Busy, Call back, Not interested, Wrong number.
4. If you forget, a banner says *You have N calls without a result*. Tap it to finish them one by
   one. If you did not really call, choose *I didn't call*.

Manager hint: turn on **daily call batches** for a department (Settings, CRM, Lead departments)
so its members can call from Today's calls and see it in the switch.

## 11. Call plans: assign leads per person (`sales/call_batches/plans.py`)

The department's slots hand every member the same number. A manager or a department head can
instead plan each member (hub: Leads, **Assign leads**): the **product** they are handed and the
**calls per slot** (for example PhoebiX, Morning 40, Afternoon 30, Evening 30). It is kept on the member's row
(`call_product`, `slot_sizes`: `{"Morning": 40, "Afternoon": 30, "Evening": 30}`).

- **The batches:** the clock and the next batch use the member's number for the slot (a slot
  left out uses its size; 0 hands out nothing in it). The day's cap is at least the plan's total.
- **The product:** new leads (steps 3 to 5) are only the plan's product. The department's
  campaign product still wins (a plan cannot name another one). The person's focus offers only
  the plan's product in that department, and the clock opens the department's batches for a
  planned member even when they focus on another department.
- **Who:** Sales Managers and System Managers for every department with batches on, heads for
  theirs. Only members can be planned. `save_call_plan` saves the department as the planner
  without needing write access to it.
- **When:** from the next slot or the next batch asked for; batches already handed out stay.
  Clearing a plan hands the department's sizes again.
- **Not enough leads:** a batch takes what there is (the person's own leads first, then
  unassigned ones of the product). `get_call_plans` counts the unassigned open leads per product
  so the planner sees it before saving.
- **By industry:** a department without products (TBO) plans by **industry** instead: the
  member's `call_industry` (an Industry Type; blank: any). New leads (steps 3 to 5) are only
  that industry (`fill.campaign_of`: the department's campaign industry still wins), hand-out by
  number (`assign_leads` with `industry`) and the auto-assign (§14) take that industry's
  unassigned leads, and `department_leads` filters and counts by industry. `get_call_plans`
  gives each member `industry` and the department `industries` (those its open leads are in)
  and `unassigned_industries` (unassigned open leads with a phone, per industry).

## 12. Handing leads by hand (`sales/call_batches/handout.py`)

On the same page a planner sees each member's open leads (and how many: `leads` in
`get_call_plans`) and the department's unassigned ones, by product, and moves them:

- **Assign:** picked leads go to a member (a lead someone else owned moves), or the next N
  unassigned open leads with a phone, of one product if chosen, hot first, then newest. Those rows
  are read `FOR UPDATE SKIP LOCKED`, as the batches pull them, so both never take the same lead.
- **Unassign:** picked leads go back to the department's unassigned pool, where the batches
  (`pull_unassigned`) or a planner hand them out again.
- **What moves with the lead:** the owner, the ToDo assignment (the previous owner's open one
  closes, the new owner gets one), and the previous owner's calls of it that were never made
  (waiting in a running batch, or overdue from an ended one). Calls already made stay.
- **Product:** the hub sets a lead's product with `bulk_set_lead_category` (`values.product`),
  which a head may already do for their department.
- **Who:** the planners (§11). At most 500 leads a call. Only leads of the department, and only to
  its members.

## 14. The daily auto-assign (`sales/call_batches/auto_assign.py`)

Managers hand leads out by hand on Assign leads (§12). Whoever they did not reach by the
department's time gets leads automatically.

- **When:** `run_auto_assign`, every 5 minutes with the clock (hooks). A department with
  `auto_assign` on runs once a day, on the first run at or after `auto_assign_at`, unless the day
  is a Sunday (`auto_assign_skip_sunday`) or a skipped Saturday (`auto_assign_skip_saturdays`; the
  2nd Saturday is the 8th to the 14th). `auto_assigned_on` records the day before it starts, so a
  failure is logged (Error Log "Auto-assign failed for …") and not retried every 5 minutes.
- **Who:** each member with a call plan of more than 0 calls (§11) who is at work (`is_off_today`:
  not on approved leave or a holiday of their list) and was not handed a lead of the department
  by hand today (a ToDo "Assigned to you on Assign leads" created today).
- **How many:** their plan's calls for the day minus the open leads of the plan's product they
  own, with a phone, never called (`tbo_last_call_on` empty). Nothing when that is 0 or less.
- **Which leads:** the next unassigned ones of the plan's product, as Assign leads picks them
  (`handout.next_unassigned`: hot, then newest, rows locked). Handed with `handout.hand` and the
  note "Assigned to you automatically: nobody handed you leads by the set time".
- **Settings:** with the other call rules (`settings.py`, the hub's Lead departments dialog); the
  Assign leads page shows them (`get_call_plans` → `auto_assign`).

## 7. Files

`tbo_smart/sales/call_batches/` (`__init__` rules, `fill`, `outcomes`, `state`, `next_batch`,
`reads`, `clock`, `settings`, `setup`, `focus`, `plans`, `handout`, `auto_assign`); doctypes under `tbo_smart/tbo_smart/doctype/tbo_call_*` and `tbo_bd_focus`;
endpoints in `api/hub_leads.py`; tests `tests/test_call_batches.py`, `test_bd_focus.py` and `test_call_plans.py`, helpers in `test_utils.py`.

## 8. Decisions

- Batches are off until a department switches them on, so deploying changes nothing by itself.
- The follow-up field in use is the lead's open ToDo (`next_follow_ups`); call-back time-of-day is
  kept in `tbo_callback_at` because a ToDo only has a date.
- The department's campaign is a limit, not a default: a request or a person's focus only fills
  what it leaves open (before, a request replaced it).
- Closing due follow-ups on a conversation keeps yesterday's finished work from coming back.

Every outcome also sets the lead's last call result and may move its stage
(`lead-pipeline.md` §2.3), through `apply_call_outcome`.

## 13. A lead with more than one number (`lead-numbers.md`)

An item shows the lead's main number (`summary.phone`) and its other usable numbers
(`summary.other_numbers`). `record_call_outcome` takes the `number` that was called; a Wrong number
marks that number, and only when none is usable the lead gets the `tbo_wrong_number` flag and the
Wrong Number stage.
