"""The scheduler's side (docs/call-batches.md §4, §6): every five minutes, close the batches
whose slot is over (telling the heads when someone is far behind) and open the slot that is
running for every member who has no batch for it yet."""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import getdate, now_datetime

from tbo_smart.sales import erpnext_installed
from tbo_smart.sales.call_batches import (
	ACTIVE,
	BATCH,
	CLOSED,
	batch_departments,
	is_off_today,
	overdue_limit,
	slot_window,
	slots_of,
)
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of
from tbo_smart.sales.call_batches.outcomes import sync_calls
from tbo_smart.sales.call_batches.state import CallQueue


def run_call_batch_clock() -> None:
	"""cron, every five minutes. Idempotent: a slot's batch is made once per person and day, so a
	missed tick only makes it late."""
	if not erpnext_installed():
		return
	now = now_datetime()
	close_ended_batches(now)
	for department in batch_departments():
		open_running_slots(department, now)


def close_ended_batches(now) -> None:
	ended = frappe.get_all(
		BATCH, filters={"status": ACTIVE, "ends_at": ("<=", now)}, fields=["name", "user", "department"]
	)
	people = {(row.user, row.department) for row in ended}
	for row in ended:
		frappe.db.set_value(BATCH, row.name, "status", CLOSED, update_modified=False)
	for user, department in sorted(people):
		try:
			warn_heads(user, frappe.get_doc("TBO Lead Department", department), now)
		except Exception:
			frappe.log_error(title="Call batches: telling the heads failed")
	frappe.db.commit()  # nosemgrep


def warn_heads(user: str, department, now) -> None:
	"""The heads hear that `user` ended a slot with more overdue calls than the department allows."""
	sync_calls(user)
	overdue = len(CallQueue(user, now).overdue)
	limit = overdue_limit(department)
	heads = [row.user for row in department.heads]
	if overdue <= limit or not heads:
		return
	enqueue_create_notification(
		heads,
		{
			"type": "Alert",
			"document_type": "TBO Lead Department",
			"document_name": department.name,
			"subject": _("{0} has {1} overdue calls (limit {2}) in {3}").format(
				frappe.utils.get_fullname(user), overdue, limit, department.name
			),
			"from_user": user,
		},
	)


def open_running_slots(department, now) -> None:
	today = getdate(now)
	for slot in slots_of(department):
		starts, ends = slot_window(slot, now)
		if not starts <= now < ends:
			continue
		for member in department.members:
			if frappe.db.exists("User", {"name": member.user, "enabled": 1}) and not frappe.db.exists(
				BATCH,
				{"user": member.user, "department": department.name, "batch_date": today, "slot": slot.label},
			):
				open_batch(department, member.user, slot, starts, ends, today)


def open_batch(department, user: str, slot, starts, ends, today) -> None:
	try:
		if is_off_today(user, today):
			return
		build_batch(department, user, slot.label, starts, ends, slot.size, campaign_of(department))
		frappe.db.commit()  # nosemgrep
	except Exception:
		frappe.db.rollback()  # nosemgrep
		frappe.log_error(title=f"Call batches: {slot.label} for {user} failed")
