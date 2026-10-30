"""The next batch on request (docs/call-batches.md §7): only when the person is up to date."""

import frappe
from frappe import _
from frappe.utils import format_time, getdate

from tbo_smart.sales.call_batches import (
	BATCH,
	batch_departments,
	is_off_today,
	overdue_limit,
	slot_window,
	slots_of,
)
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of, room_for
from tbo_smart.sales.call_batches.state import CallQueue


def pick_department(user: str, queue: CallQueue, name: str | None):
	departments = batch_departments(user)
	if name:
		departments = [d for d in departments if d.name == name]
	if not departments:
		return None
	running = {row.department for row in queue.running}
	return next((d for d in departments if d.name in running), departments[0])


def target_slot(department, now):
	"""The slot in progress or, between slots, the next one; None when the day's slots are over."""
	for slot in slots_of(department):
		if now < slot_window(slot, now)[1]:
			return slot
	return None


def check_next_batch(user: str, queue: CallQueue, department_name: str | None = None) -> dict:
	"""{allowed, reason, department, slot, next_slot}: whether the next batch may be handed out
	now and, when not, why in words the person can act on."""
	department = pick_department(user, queue, department_name)
	if not department:
		return {"allowed": False, "reason": _("Daily call batches are not switched on for your department.")}
	slots = slots_of(department)
	now = queue.now
	next_slot = next(
		(
			{"label": slot.label, "starts_at": slot_window(slot, now)[0]}
			for slot in slots
			if slot_window(slot, now)[0] > now
		),
		None,
	)

	def deny(reason: str) -> dict:
		return {"allowed": False, "reason": reason, "next_slot": next_slot}

	off = is_off_today(user, now)
	if off:
		return deny(_("You are not working today ({0}).").format(_(off)))
	slot = target_slot(department, now)
	if not slot:
		return deny(
			_("Today's slots are over. Tomorrow's first batch starts at {0}.").format(
				format_time(slots[0].start, "HH:mm")
			)
		)
	if now < slot_window(slot, now)[0] and not queue.today():
		return deny(_("The first batch starts at {0}.").format(format_time(slot.start, "HH:mm")))
	if queue.pending:
		return deny(_("Finish the {0} calls still waiting first.").format(len(queue.pending)))
	limit = overdue_limit(department)
	if len(queue.overdue) > limit:
		return deny(
			_("You have {0} overdue calls, more than the {1} allowed. Call them first.").format(
				len(queue.overdue), limit
			)
		)
	if room_for(department, user, now, slot.size) <= 0:
		return deny(_("You have reached today's limit of calls."))
	return {"allowed": True, "reason": None, "department": department, "slot": slot, "next_slot": next_slot}


def create_next_batch(user: str, queue: CallQueue, department_name: str | None, request: dict) -> dict:
	decision = check_next_batch(user, queue, department_name)
	if not decision["allowed"]:
		return {"created": False, "reason": decision["reason"]}
	department, slot, now = decision["department"], decision["slot"], queue.now
	starts, ends = slot_window(slot, now)
	label = slot.label
	taken = frappe.get_all(
		BATCH,
		filters={
			"user": user,
			"department": department.name,
			"batch_date": getdate(now),
			"slot": ("like", f"{slot.label}%"),
		},
		pluck="slot",
	)
	if taken:
		label = f"{slot.label} +{len(taken) + 1}"
	batch = build_batch(
		department,
		user,
		label,
		max(now, starts),
		ends,
		slot.size,
		campaign_of(department, request),
		allow_empty=False,
	)
	if not batch:
		return {"created": False, "reason": _("There are no more leads to call right now.")}
	return {"created": True, "reason": None, "batch": batch.name, "slot": label, "size": len(batch.items)}
