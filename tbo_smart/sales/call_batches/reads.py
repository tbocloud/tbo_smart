"""What the hub reads (docs/call-batches.md §8): a person's queue, the next batch and the
managers' report."""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime

from tbo_smart.sales import departments as lead_departments
from tbo_smart.sales import numbers, products
from tbo_smart.sales.activities import notes_of, plain_text
from tbo_smart.sales.call_batches import (
	ACTIVE,
	BATCH,
	CAMPAIGN_COLUMNS,
	batch_departments,
	is_off_today,
	overdue_limit,
	slot_window,
	slots_of,
)
from tbo_smart.sales.call_batches import focus as bd_focus
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of, room_for
from tbo_smart.sales.call_batches.outcomes import sync_calls
from tbo_smart.sales.call_batches.state import CallQueue, item_rows, reached_leads
from tbo_smart.telephony.calls import can_call

NOTE_CHARS = 240


def main_number(lead, usable: list | None) -> str | None:
	"""The number to call: the main number, else the first usable one, else what the lead's own
	fields hold."""
	if usable:
		return usable[0]["number"]
	return lead.mobile_no or lead.phone or lead.whatsapp_no


def lead_summaries(names: list[str]) -> dict[str, dict]:
	"""{lead: what the caller needs on screen}: name, company, industry, district, phone, stage,
	temperature, the last note and the AI's next step."""
	if not names:
		return {}
	district = "tbo_district" if frappe.db.has_column("Lead", "tbo_district") else None
	fields = [
		"name",
		"lead_name",
		"company_name",
		"industry",
		"mobile_no",
		"phone",
		"whatsapp_no",
		"tbo_stage",
		"tbo_temperature",
		"tbo_ai_next_action",
		"tbo_wrong_number",
		*([district] if district else []),
	]
	leads = {row.name: row for row in frappe.get_all("Lead", filters={"name": ("in", names)}, fields=fields)}
	notes = notes_of("Lead", list(leads))
	usable = numbers.usable_by_lead(list(leads))
	summaries = {}
	for name, lead in leads.items():
		last = (notes.get(name) or [None])[0]
		summaries[name] = {
			"name": name,
			"lead_name": lead.lead_name,
			"company_name": lead.company_name,
			"industry": lead.industry,
			"district": lead.get(district) if district else None,
			"phone": main_number(lead, usable.get(name)),
			"other_numbers": [row for row in usable.get(name) or [] if not row["is_primary"]],
			"stage": lead.tbo_stage,
			"temperature": lead.tbo_temperature,
			"last_note": plain_text(last.content)[:NOTE_CHARS] if last else None,
			"next_step": lead.tbo_ai_next_action or None,
			"wrong_number": cint(lead.tbo_wrong_number),
		}
	return summaries


def serialize_row(row, summaries: dict, calling: dict) -> dict:
	"""One call. `product` and `intro_line` are the lead's product (None without one);
	`calling_for` is what the caller says they are calling under: the product, else the department."""
	return {
		"item": row.name,
		"batch": row.batch,
		"slot": row.slot,
		"lead": row.lead,
		"priority": row.priority,
		"reason": row.priority_reason,
		"is_repeat": cint(row.is_repeat),
		"retry_at": row.retry_at,
		"attempts": cint(row.attempts),
		"batch_ends_at": row.ends_at,
		"summary": summaries.get(row.lead) or {"name": row.lead},
		**{
			"product": None,
			"intro_line": None,
			"calling_for": row.department,
			**{key: value for key, value in (calling.get(row.lead) or {}).items() if value},
		},
	}


def get_my_calls() -> dict:
	"""The session user's queue. {enabled, date, batch, counts, today, pending, scheduled,
	overdue, can_call, next_batch: {allowed, reason}, next_slot}. `enabled` False: none of their
	departments hands out batches. batch is the newest running one (its sibling batches' leads
	are in the lists too); counts are over all running batches."""
	from tbo_smart.sales.call_batches.next_batch import check_next_batch

	user = frappe.session.user
	if not batch_departments(user):
		return {
			"enabled": False,
			"pending": [],
			"scheduled": [],
			"overdue": [],
			"focus": None,
			"focus_options": [],
			"reason": _("Daily call batches are not switched on for your department."),
		}
	sync_calls(user)
	queue = CallQueue(user)
	names = list(dict.fromkeys(row.lead for row in queue.pending + queue.scheduled + queue.overdue))
	summaries = lead_summaries(names)
	calling = products.calling_for(names)
	running = queue.running
	newest = max(running, key=lambda row: get_datetime(row.starts_at)) if running else None
	counts = queue.totals(running)
	counts.update(pending=len(queue.pending), scheduled=len(queue.scheduled), overdue=len(queue.overdue))
	decision = check_next_batch(user, queue)
	return {
		"enabled": True,
		"date": str(getdate()),
		"batch": {
			"name": newest.batch,
			"slot": newest.slot,
			"department": newest.department,
			"starts_at": newest.starts_at,
			"ends_at": newest.ends_at,
			"seconds_left": max(int((get_datetime(newest.ends_at) - queue.now).total_seconds()), 0),
			"batches": queue.running_batches,
			"campaign_product": frappe.db.get_value(
				lead_departments.DEPARTMENT, newest.department, "campaign_product"
			)
			or None,
		}
		if newest
		else None,
		"counts": counts,
		# the retries too: a lead not answered first and reached on its retry counts contacted
		"today": queue.totals(queue.today(repeats=True)),
		"pending": [serialize_row(row, summaries, calling) for row in queue.pending],
		"scheduled": [serialize_row(row, summaries, calling) for row in queue.scheduled],
		"overdue": [serialize_row(row, summaries, calling) for row in queue.overdue],
		"can_call": can_call(user),
		"next_batch": {"allowed": decision["allowed"], "reason": decision["reason"]},
		"next_slot": decision.get("next_slot"),
		"focus": bd_focus.get_focus(user),
		"focus_options": bd_focus.options_for(user),
	}


def set_calling_focus(
	department: str | None = None, product: str | None = None, district: str | None = None
) -> dict:
	"""Save what the session user is calling for and start the next batch for it when the rules
	allow: {focus, label, message, next_batch: {created, reason, size?, slot?}}. `message` says in
	plain words what happens next. A blank department clears the choice."""
	from tbo_smart.sales.call_batches.next_batch import create_next_batch

	user = frappe.session.user
	sync_calls(user)
	chosen = bd_focus.set_focus(user, department, product, district)
	label = bd_focus.describe(chosen)
	if not chosen:
		return {
			"focus": None,
			"label": "",
			"message": _("Done. You will get calls from all your departments."),
			"next_batch": {"created": False, "reason": None},
		}
	result = create_next_batch(user, CallQueue(user), chosen["department"], {})
	if result["created"]:
		message = _("Calling for {0}. Your next {1} calls are ready.").format(label, result["size"])
	else:
		message = _("Calling for {0}. Your next batch will be for it. {1}").format(label, result["reason"])
	return {
		"focus": chosen,
		"label": label,
		"message": message,
		"next_batch": {key: result.get(key) for key in ("created", "reason", "size", "slot")},
	}


def get_next_batch(
	department: str | None = None, industry=None, state=None, district=None, product=None
) -> dict:
	"""Hand the session user their next batch when the rules allow it: {created, reason, batch,
	size}. Nothing is created (and `reason` says why) while calls are pending, overdue calls pass
	the department's limit, the daily cap is reached or no slot is open."""
	from tbo_smart.sales.call_batches.next_batch import create_next_batch

	user = frappe.session.user
	sync_calls(user)
	return create_next_batch(
		user,
		CallQueue(user),
		department,
		{"industry": industry, "state": state, "district": district, "product": product},
	)


def report_departments() -> list | None:
	"""Departments whose batches the session user may report on: None = all (Sales Manager or
	System Manager), else those they head."""
	if lead_departments.can_manage():
		return None
	headed = frappe.get_all(
		lead_departments.DEPARTMENT_USER,
		filters={
			"user": frappe.session.user,
			"parentfield": "heads",
			"parenttype": lead_departments.DEPARTMENT,
		},
		pluck="parent",
	)
	if not headed:
		frappe.throw(
			_("Only a department head or a Sales Manager may open this report."), frappe.PermissionError
		)
	return headed


def missed_rows(items: list, now) -> list:
	"""The day's overdue calls by CallQueue.missed's rule: per person, a lead once (its latest
	missed row), and not at all when it was called that day or is still waiting in a running
	batch."""
	called, queued, latest = set(), set(), {}
	for row in items:
		ended = row.status != ACTIVE or get_datetime(row.ends_at) <= now
		if row.outcome:
			called.add((row.user, row.lead))
		elif not ended:
			queued.add((row.user, row.lead))
	for row in items:  # by batch start, so the last row of a lead is its latest
		key = (row.user, row.lead)
		if not row.outcome and key not in called and key not in queued:
			latest[key] = row
	return list(latest.values())


def get_call_batches_report(date=None) -> dict:
	"""{date, people: [{user, user_name, department, size, called, connected, overdue, batches:
	[{name, slot, status, starts_at, ends_at, size, called, connected, overdue}]}], totals}.
	`connected` counts calls where someone answered (Connected, Call back, Not interested), on any
	call of the lead in the batch (a retry, a call back, the customer calling back);
	`overdue` the calls of an ended slot nobody made, counted as the queue counts them (missed_rows)."""
	day = getdate(date)
	allowed = report_departments()
	filters = {"batch_date": day}
	if allowed is not None:
		filters["department"] = allowed
	now = now_datetime()
	people: dict[tuple, dict] = {}
	batches: dict[str, dict] = {}
	items = item_rows(filters)
	reached = reached_leads(items)
	for row in items:
		person = people.setdefault(
			(row.user, row.department),
			{
				"user": row.user,
				"user_name": frappe.utils.get_fullname(row.user),
				"department": row.department,
				"size": 0,
				"called": 0,
				"connected": 0,
				"overdue": 0,
				"batches": [],
			},
		)
		batch = batches.get(row.batch)
		if not batch:
			batch = batches[row.batch] = {
				"name": row.batch,
				"slot": row.slot,
				"status": row.status,
				"starts_at": row.starts_at,
				"ends_at": row.ends_at,
				"size": 0,
				"called": 0,
				"connected": 0,
				"overdue": 0,
			}
			person["batches"].append(batch)
		if not row.is_repeat:
			batch["size"] += 1
			batch["called"] += 1 if row.outcome else 0
			batch["connected"] += 1 if (row.batch, row.lead) in reached else 0
	for row in missed_rows(items, now):
		batches[row.batch]["overdue"] += 1
	for person in people.values():
		for key in ("size", "called", "connected", "overdue"):
			person[key] = sum(batch[key] for batch in person["batches"])
	rows = sorted(people.values(), key=lambda person: (person["department"], person["user_name"]))
	return {
		"date": str(day),
		"people": rows,
		"totals": {
			key: sum(person[key] for person in rows) for key in ("size", "called", "connected", "overdue")
		},
	}
