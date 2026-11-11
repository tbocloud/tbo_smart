"""A department's call-batch rules, as the hub's settings dialog reads and saves them."""

from itertools import pairwise

import frappe
from frappe import _
from frappe.utils import cint, format_time, get_time

from tbo_smart.sales.call_batches import DEFAULT_CAP, DEFAULT_OVERDUE_LIMIT, SLOT, slots_of

SAVE_FIELDS = {
	"call_batches_enabled",
	"daily_call_cap",
	"pull_unassigned",
	"overdue_block_limit",
	"campaign_industry",
	"campaign_state",
	"campaign_district",
	"campaign_product",
	"call_slots",
	"member_caps",
	"auto_assign",
	"auto_assign_at",
	"auto_assign_skip_sunday",
	"auto_assign_skip_saturdays",
}


def serialize(department) -> dict:
	"""{call_batches_enabled, daily_call_cap, pull_unassigned, overdue_block_limit, campaign_* (industry,
	state, district, product),
	call_slots: [{label, start_time "HH:MM", end_time, size}], member_caps: {user: cap}}. The
	slots are the defaults while the department has none of its own."""
	return {
		"call_batches_enabled": cint(department.call_batches_enabled),
		"daily_call_cap": cint(department.daily_call_cap) or DEFAULT_CAP,
		"pull_unassigned": cint(department.pull_unassigned),
		"overdue_block_limit": DEFAULT_OVERDUE_LIMIT
		if department.overdue_block_limit is None
		else cint(department.overdue_block_limit),
		"campaign_industry": department.campaign_industry or "",
		"campaign_state": department.campaign_state or "",
		"campaign_district": department.campaign_district or "",
		"campaign_product": department.campaign_product or "",
		"call_slots": [
			{
				"label": slot.label,
				"start_time": format_time(slot.start, "HH:mm"),
				"end_time": format_time(slot.end, "HH:mm"),
				"size": slot.size,
			}
			for slot in slots_of(department)
		],
		"member_caps": {
			row.user: cint(row.daily_call_cap) for row in department.members if cint(row.daily_call_cap)
		},
		**auto_assign_rules(department),
	}


def auto_assign_rules(department) -> dict:
	"""{auto_assign, auto_assign_at "HH:MM", auto_assign_skip_sunday, auto_assign_skip_saturdays,
	auto_assigned_on} (docs/call-batches.md §14); empty values read as the defaults."""
	from tbo_smart.sales.call_batches.auto_assign import DEFAULT_SATURDAYS, DEFAULT_TIME

	return {
		"auto_assign": cint(department.get("auto_assign")),
		"auto_assign_at": format_time(department.get("auto_assign_at") or DEFAULT_TIME, "HH:mm"),
		"auto_assign_skip_sunday": cint(department.get("auto_assign_skip_sunday")),
		"auto_assign_skip_saturdays": department.get("auto_assign_skip_saturdays") or DEFAULT_SATURDAYS,
		"auto_assigned_on": str(department.get("auto_assigned_on") or "") or None,
	}


def apply(department, values: dict) -> None:
	"""Set the rules in `values` on the department (the caller saves it)."""
	for field in ("call_batches_enabled", "pull_unassigned", "auto_assign", "auto_assign_skip_sunday"):
		if field in values:
			department.set(field, cint(values[field]))
	if "auto_assign_at" in values:
		department.set("auto_assign_at", clean_time(values["auto_assign_at"]))
	if "auto_assign_skip_saturdays" in values:
		from tbo_smart.sales.call_batches.auto_assign import SATURDAYS

		saturdays = values["auto_assign_skip_saturdays"] or "None"
		if saturdays not in SATURDAYS:
			frappe.throw(_("Saturdays to skip must be one of {0}.").format(", ".join(SATURDAYS)))
		department.set("auto_assign_skip_saturdays", saturdays)
	for field in ("daily_call_cap", "overdue_block_limit"):
		if field in values:
			department.set(field, max(cint(values[field]), 0))
	for field in ("campaign_industry", "campaign_state", "campaign_district", "campaign_product"):
		if field in values:
			department.set(field, (values[field] or "").strip())
	if "call_slots" in values:
		department.set("call_slots", [])
		for slot in values["call_slots"] or []:
			department.append(
				"call_slots",
				{
					"label": slot.get("label"),
					"start_time": slot.get("start_time"),
					"end_time": slot.get("end_time"),
					"size": slot.get("size"),
				},
			)
	if "member_caps" in values:
		caps = values["member_caps"] or {}
		for row in department.members:
			row.daily_call_cap = max(cint(caps.get(row.user)), 0)


def clean_time(value) -> str:
	"""A time of day as "HH:MM:SS", or an error in words."""
	try:
		return get_time(str(value or "").strip()).strftime("%H:%M:%S")
	except Exception:
		frappe.throw(_("Give the auto-assign time as HH:MM, e.g. 11:00."))


def validate_slots(department) -> None:
	"""Controller validate: every slot has a label and size, ends after it starts, and none overlap."""
	slots = sorted(slots_of(department), key=lambda slot: slot.start)
	for slot in department.get("call_slots") or []:
		if not (slot.label or "").strip() or cint(slot.size) <= 0:
			frappe.throw(_("Each call slot needs a label and a size above zero."))
		if get_time(slot.end_time) <= get_time(slot.start_time):
			frappe.throw(_("The {0} slot must end after it starts.").format(slot.label))
	for earlier, later in pairwise(slots):
		if later.start < earlier.end:
			frappe.throw(_("The {0} and {1} slots overlap.").format(earlier.label, later.label))
