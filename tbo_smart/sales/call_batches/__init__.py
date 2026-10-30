"""Daily call batches (docs/call-batches.md): the leads one salesperson must call in a time slot.

A TBO Lead Department with `call_batches_enabled` hands each member a TBO Call Batch at the
start of every slot (`clock`), filled in priority order (`fill`); a call is recorded as one of
six outcomes (`outcomes`) and the hub reads the queue and the managers' report (`reads`).
This module holds what they share: names, the department's rules and who works today.
"""

from datetime import datetime, time, timedelta

import frappe
from frappe.utils import cint, get_datetime, get_time, getdate

BATCH = "TBO Call Batch"
ITEM = "TBO Call Batch Item"
SLOT = "TBO Call Slot"
ACTIVE, CLOSED = "Active", "Closed"

CONNECTED, NOT_ANSWERED, BUSY = "Connected", "Not answered", "Busy"
CALL_BACK, NOT_INTERESTED, WRONG_NUMBER = "Call back", "Not interested", "Wrong number"
OUTCOMES = (CONNECTED, NOT_ANSWERED, BUSY, CALL_BACK, NOT_INTERESTED, WRONG_NUMBER)
UNREACHED = (NOT_ANSWERED, BUSY)
# the person at the other end picked up
ANSWERED = (CONNECTED, CALL_BACK, NOT_INTERESTED)

# why a lead is in a batch, best first
FOLLOW_UP_DUE, RETRY_YESTERDAY, NEVER_CALLED, GONE_QUIET, PULLED = 1, 2, 3, 4, 5
REASONS = {
	FOLLOW_UP_DUE: "Follow-up due",
	RETRY_YESTERDAY: "Retry from yesterday",
	NEVER_CALLED: "Never called",
	GONE_QUIET: "Gone quiet",
	PULLED: "New lead for you",
}
RETRY_AFTER = timedelta(hours=2)
OVERDUE_DAYS = 7
DEFAULT_CAP = 100
DEFAULT_OVERDUE_LIMIT = 10
DEFAULT_SLOTS = (
	{"label": "Morning", "start_time": time(10), "end_time": time(13), "size": 50},
	{"label": "Afternoon", "start_time": time(14), "end_time": time(18), "size": 50},
)
# the department's campaign filter and the Lead column each narrows (lead-categories adds the
# last one; a column that is not there yet is skipped)
CAMPAIGN_COLUMNS = {"industry": "industry", "state": "state", "district": "tbo_district"}


def slots_of(department) -> list[frappe._dict]:
	"""The department's slots by start time: {label, start (time), end (time), size}; the default
	two when it has none."""
	rows = [
		frappe._dict(
			label=row.label, start=get_time(row.start_time), end=get_time(row.end_time), size=cint(row.size)
		)
		for row in department.get("call_slots") or []
	] or [
		frappe._dict(label=row["label"], start=row["start_time"], end=row["end_time"], size=row["size"])
		for row in DEFAULT_SLOTS
	]
	return sorted(rows, key=lambda slot: slot.start)


def slot_window(slot, day) -> tuple[datetime, datetime]:
	day = getdate(day)
	return datetime.combine(day, slot.start), datetime.combine(day, slot.end)


def daily_cap(department, user: str) -> int:
	"""A member's own cap when set, else the department's."""
	for row in department.get("members") or []:
		if row.user == user and cint(row.get("daily_call_cap")):
			return cint(row.daily_call_cap)
	return cint(department.daily_call_cap) or DEFAULT_CAP


def overdue_limit(department) -> int:
	value = department.overdue_block_limit
	return DEFAULT_OVERDUE_LIMIT if value is None else cint(value)


def batch_departments(user: str | None = None) -> list:
	"""Active departments that hand out batches (those `user` is a member of, when named)."""
	names = frappe.get_all(
		"TBO Lead Department", filters={"is_active": 1, "call_batches_enabled": 1}, pluck="name"
	)
	departments = [frappe.get_doc("TBO Lead Department", name) for name in names]
	if user:
		departments = [d for d in departments if user in (row.user for row in d.members)]
	return departments


def is_off_today(user: str, day) -> str | None:
	"""Why `user` does not work on `day` (a holiday, a weekly off or approved leave), else None.
	Without an employee record or a holiday list only Sundays are off."""
	day = getdate(day)
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
	if not employee:
		return "weekly off" if day.weekday() == 6 else None
	if frappe.db.exists("DocType", "Leave Application") and frappe.db.exists(
		"Leave Application",
		{
			"employee": employee,
			"docstatus": 1,
			"status": "Approved",
			"from_date": ("<=", day),
			"to_date": (">=", day),
			"half_day": 0,
		},
	):
		return "on leave"
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return "weekly off" if day.weekday() == 6 else None
	if frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": day}):
		return "holiday"
	return None


def when(value) -> datetime | None:
	return get_datetime(value) if value else None
