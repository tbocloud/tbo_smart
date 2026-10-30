"""What the hub reads (docs/call-batches.md §8): a person's queue, the next batch and the
managers' report."""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime

from tbo_smart.sales import departments as lead_departments
from tbo_smart.sales.activities import notes_of, plain_text
from tbo_smart.sales.call_batches import (
	ACTIVE,
	ANSWERED,
	BATCH,
	CAMPAIGN_COLUMNS,
	batch_departments,
	is_off_today,
	overdue_limit,
	slot_window,
	slots_of,
)
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of, room_for
from tbo_smart.sales.call_batches.outcomes import sync_calls
from tbo_smart.sales.call_batches.state import CallQueue, item_rows
from tbo_smart.telephony.calls import can_call

NOTE_CHARS = 240


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
	summaries = {}
	for name, lead in leads.items():
		last = (notes.get(name) or [None])[0]
		summaries[name] = {
			"name": name,
			"lead_name": lead.lead_name,
			"company_name": lead.company_name,
			"industry": lead.industry,
			"district": lead.get(district) if district else None,
			"phone": lead.mobile_no or lead.phone or lead.whatsapp_no,
			"stage": lead.tbo_stage,
			"temperature": lead.tbo_temperature,
			"last_note": plain_text(last.content)[:NOTE_CHARS] if last else None,
			"next_step": lead.tbo_ai_next_action or None,
			"wrong_number": cint(lead.tbo_wrong_number),
		}
	return summaries


def serialize_row(row, summaries: dict) -> dict:
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
			"reason": _("Daily call batches are not switched on for your department."),
		}
	sync_calls(user)
	queue = CallQueue(user)
	names = list(dict.fromkeys(row.lead for row in queue.pending + queue.scheduled + queue.overdue))
	summaries = lead_summaries(names)
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
		}
		if newest
		else None,
		"counts": counts,
		"today": queue.totals(queue.today()),
		"pending": [serialize_row(row, summaries) for row in queue.pending],
		"scheduled": [serialize_row(row, summaries) for row in queue.scheduled],
		"overdue": [serialize_row(row, summaries) for row in queue.overdue],
		"can_call": can_call(user),
		"next_batch": {"allowed": decision["allowed"], "reason": decision["reason"]},
		"next_slot": decision.get("next_slot"),
	}


def get_next_batch(department: str | None = None, industry=None, state=None, district=None) -> dict:
	"""Hand the session user their next batch when the rules allow it: {created, reason, batch,
	size}. Nothing is created (and `reason` says why) while calls are pending, overdue calls pass
	the department's limit, the daily cap is reached or no slot is open."""
	from tbo_smart.sales.call_batches.next_batch import create_next_batch

	user = frappe.session.user
	sync_calls(user)
	return create_next_batch(
		user, CallQueue(user), department, {"industry": industry, "state": state, "district": district}
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


def get_call_batches_report(date=None) -> dict:
	"""{date, people: [{user, user_name, department, size, called, connected, overdue, batches:
	[{name, slot, status, starts_at, ends_at, size, called, connected, overdue}]}], totals}.
	`connected` counts calls where someone answered (Connected, Call back, Not interested);
	`overdue` the calls of an ended slot nobody made."""
	day = getdate(date)
	allowed = report_departments()
	filters = {"batch_date": day}
	if allowed is not None:
		filters["department"] = allowed
	now = now_datetime()
	people: dict[tuple, dict] = {}
	batches: dict[str, dict] = {}
	for row in item_rows(filters):
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
		ended = row.status != ACTIVE or get_datetime(row.ends_at) <= now
		if not row.is_repeat:
			batch["size"] += 1
			batch["called"] += 1 if row.outcome else 0
			batch["connected"] += 1 if row.outcome in ANSWERED else 0
		if not row.outcome and ended:
			batch["overdue"] += 1
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
