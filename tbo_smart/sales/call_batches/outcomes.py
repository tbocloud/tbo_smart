"""Recording a call on a batch item (docs/call-batches.md §5).

`record` is the person's answer; `sync_calls` counts a call logged any other way (MyOperator,
the Log call dialog) for the item it belongs to. Both end in `apply`, which is the one place an
item gets its outcome: it clears the item from the queue, then schedules what follows (a retry
in two hours, a call back, a follow-up, the wrong-number flag).
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime

from tbo_smart.lead_pipeline.call_outcomes import apply_call_outcome, schedule_call_back
from tbo_smart.sales.activities import OPEN, add_note
from tbo_smart.sales.call_batches import (
	ACTIVE,
	BATCH,
	BUSY,
	CALL_BACK,
	CONNECTED,
	ITEM,
	NOT_ANSWERED,
	NOT_INTERESTED,
	OUTCOMES,
	OVERDUE_DAYS,
	RETRY_AFTER,
	UNREACHED,
	WRONG_NUMBER,
)
from tbo_smart.telephony import ANSWERED as CALL_ANSWERED
from tbo_smart.telephony.calls import log_manual_call

# what the call log calls each outcome
LOGGED_AS = {
	CONNECTED: "Connected",
	CALL_BACK: "Connected",
	NOT_INTERESTED: "Connected",
	NOT_ANSWERED: "No answer",
	BUSY: "Busy",
	WRONG_NUMBER: "No answer",
}
SAME_DAY_ATTEMPTS = 2


def record(batch_item: str, outcome: str, note: str | None = None, callback_at=None) -> dict:
	"""The session user's answer for one item. Logs the call (a TBO Call by them, its Event on
	the lead) and applies the outcome. Returns {item, outcome, retry_at}."""
	if outcome not in OUTCOMES:
		frappe.throw(_("outcome must be one of {0}.").format(", ".join(OUTCOMES)))
	parent = frappe.db.get_value(ITEM, batch_item, "parent")
	if not parent:
		frappe.throw(_("{0} is not a call in a batch.").format(batch_item), frappe.DoesNotExistError)
	batch = frappe.get_doc(BATCH, parent, for_update=True)
	if batch.user != frappe.session.user:
		frappe.throw(_("This call is on someone else's list."), frappe.PermissionError)
	row = next(row for row in batch.items if row.name == batch_item)
	if row.outcome:
		frappe.throw(_("This call is already recorded as {0}.").format(_(row.outcome)))
	callback = get_datetime(callback_at) if callback_at else None
	if outcome == CALL_BACK and not callback:
		frappe.throw(_("Say when to call back."))
	if callback and callback < add_to_date(now_datetime(), minutes=-1):
		frappe.throw(_("The call back time is in the past."))
	text = ": ".join(
		part
		for part in (
			_(outcome) if outcome in (CALL_BACK, NOT_INTERESTED, WRONG_NUMBER) else "",
			(note or "").strip(),
		)
		if part
	)
	log_manual_call(row.lead, LOGGED_AS[outcome], text or None)
	retry_at = apply(batch, row, outcome, (note or "").strip(), callback, now_datetime())
	return {"item": row.name, "outcome": outcome, "retry_at": retry_at}


def apply(batch, row, outcome: str, note: str, callback, called_at):
	"""Give `row` its outcome and schedule what follows; saves the batch once. Returns when the
	lead comes back to this batch's queue (a retry or a call back), else None."""
	row.outcome, row.called_at, row.note = outcome, called_at, note or None
	row.attempts = (row.attempts or 0) + 1
	lead = row.lead
	comes_back = None
	if outcome != NOT_ANSWERED and outcome != BUSY:
		close_due_follow_ups(lead)
	if outcome in UNREACHED:
		comes_back = retry(batch, row, called_at)
	elif outcome == CALL_BACK or (outcome == CONNECTED and callback):
		comes_back = set_callback(batch, row, callback, repeat=outcome == CALL_BACK)
	else:
		frappe.db.set_value("Lead", lead, "tbo_callback_at", None, update_modified=False)
	if outcome == WRONG_NUMBER:
		frappe.db.set_value("Lead", lead, "tbo_wrong_number", 1, update_modified=False)
		add_note("Lead", lead, _("Wrong number, reported from the daily calls."))
	apply_call_outcome(lead, outcome, called_at)
	batch.save(ignore_permissions=True)
	return comes_back


def retry(batch, row, called_at):
	"""Not answered or Busy: one more try two hours later in this batch, if the batch is still
	running then; else the lead comes back in tomorrow's batch."""
	retry_at = add_to_date(called_at, hours=RETRY_AFTER.total_seconds() / 3600)
	if row.attempts >= SAME_DAY_ATTEMPTS or retry_at >= get_datetime(batch.ends_at):
		return None
	batch.append(
		"items",
		{
			"lead": row.lead,
			"priority": row.priority,
			"priority_reason": _("Retry"),
			"is_repeat": 1,
			"retry_at": retry_at,
			"attempts": row.attempts,
		},
	)
	return retry_at


def set_callback(batch, row, callback, repeat: bool = True):
	"""The lead's follow-up becomes `callback`; inside this batch it also comes back to the
	queue at that time."""
	lead = row.lead
	schedule_call_back(lead, callback)
	if not repeat or callback >= get_datetime(batch.ends_at):
		return None
	batch.append(
		"items",
		{
			"lead": lead,
			"priority": row.priority,
			"priority_reason": _("Call back"),
			"is_repeat": 1,
			"retry_at": callback,
			"attempts": row.attempts,
		},
	)


def close_due_follow_ups(lead: str) -> None:
	"""The calls batch is where a due follow-up gets done, so a conversation closes it; the
	follow-up that follows (a call back) is made after."""
	for name in frappe.get_all(
		"ToDo",
		filters={"reference_type": "Lead", "reference_name": lead, "status": OPEN, "date": ("<=", getdate())},
		pluck="name",
	):
		frappe.db.set_value("ToDo", name, "status", "Closed")


# ---------- calls logged any other way ----------


def sync_calls(user: str) -> int:
	"""Items of `user` still open whose lead they called since (MyOperator, the Log call dialog,
	a call in the app) get that call's outcome. Returns how many were closed."""
	since = add_to_date(getdate(), days=-OVERDUE_DAYS)
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	open_rows = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(Item.name, Item.parent, Item.lead, Batch.starts_at)
		.where(
			(Batch.user == user)
			& (Batch.batch_date >= since)
			& (Item.outcome.isnull() | (Item.outcome == ""))
		)
	).run(as_dict=True)
	if not open_rows:
		return 0
	calls = frappe.get_all(
		"TBO Call",
		filters={
			"agent": user,
			"direction": "Outgoing",
			"lead": ("in", list({row.lead for row in open_rows})),
			"started_at": (">=", min(get_datetime(row.starts_at) for row in open_rows)),
		},
		fields=["lead", "status", "started_at"],
		order_by="started_at asc",
	)
	closed = 0
	open_names = {row.name for row in open_rows}
	for parent in dict.fromkeys(row.parent for row in open_rows):
		batch = frappe.get_doc(BATCH, parent, for_update=True)
		# a retry or call back counts only a call made after the lead's last recorded one
		# (a child row's `creation` is its batch's, so it cannot say when the row was added)
		done = {}
		for row in batch.items:
			if row.outcome and row.called_at:
				done[row.lead] = max(
					done.get(row.lead, get_datetime(row.called_at)), get_datetime(row.called_at)
				)
		since = get_datetime(batch.starts_at)
		for row in list(batch.items):
			if row.outcome or row.name not in open_names:
				continue
			after = max(since, done.get(row.lead, since))
			call = next(
				(call for call in calls if call.lead == row.lead and get_datetime(call.started_at) > after),
				None,
			)
			if call:
				outcome = CONNECTED if call.status == CALL_ANSWERED else NOT_ANSWERED
				apply(batch, row, outcome, "", None, call.started_at)
				done[row.lead] = get_datetime(call.started_at)
				closed += 1
	if closed:
		# the hub reads the queue with a GET, which Frappe would roll back
		frappe.flags.commit = True
	return closed
