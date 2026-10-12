"""Quotation discount and commission approvals for the CRM and the Quotation form.

Every method acts as the session user. Who may request, approve, forward or override is
decided here from TBO Quotation Approval Settings (the user's level comes from their roles),
never from anything the caller sends.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.quotation_control import (
	get_covering,
	get_list_value,
	get_percent,
	new_adjustment,
)
from tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment.tbo_quotation_adjustment import OPEN_STATUSES
from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	format_limit,
	get_settings,
)

PENDING_LIMIT = 200
LIST_FIELDS = [
	"name",
	"quotation",
	"customer",
	"deal",
	"kind",
	"status",
	"voided",
	"within_limit",
	"previous_percent",
	"requested_percent",
	"approved_percent",
	"quotation_value",
	"requested_by",
	"approved_by",
	"current_level",
	"current_rank",
	"reason",
	"remarks",
	"creation",
	"modified",
]


@frappe.whitelist()
def get_adjustment_limits() -> dict:
	"""The session user's level and limits. A limit of None means no limit; 0 means not set,
	so every non-zero adjustment needs approval."""
	settings = get_settings()
	level = settings.get_user_level()
	kinds = settings.controlled_kinds()
	return {
		"enabled": bool(kinds),
		"applies_to": settings.applies_to,
		"kinds": kinds,
		"level": serialize_level(level),
		"limits": {kind: settings.get_limit(level, kind) for kind in kinds},
		"limit_labels": {kind: format_limit(settings.get_limit(level, kind)) for kind in kinds},
	}


@frappe.whitelist()
def get_quotation_adjustment_status(quotation: str) -> dict:
	"""For each limited kind on a quotation: its percentage, my limit, whether it is covered and
	the newest adjustment (request or decision)."""
	doc = frappe.get_doc("Quotation", quotation)
	doc.check_permission("read")
	settings = get_settings()
	level = settings.get_user_level()
	value = get_list_value(doc)
	kinds = []
	for kind in settings.controlled_kinds():
		percent = get_percent(doc, kind)
		limit = settings.get_limit(level, kind)
		latest = frappe.get_all(
			"TBO Quotation Adjustment",
			filters={"quotation": doc.name, "kind": kind},
			fields=LIST_FIELDS,
			order_by="creation desc",
			limit_page_length=1,
		)
		kinds.append(
			{
				"kind": kind,
				"percent": percent,
				"limit": limit,
				"limit_label": format_limit(limit),
				"within_limit": settings.is_within(limit, percent),
				"approved": percent <= 0 or bool(get_covering(doc.name, kind, percent, value)),
				"latest": serialize_adjustment(latest[0]) if latest else None,
			}
		)
	return {
		"quotation": doc.name,
		"docstatus": doc.docstatus,
		"level": serialize_level(level),
		"kinds": kinds,
	}


@frappe.whitelist(methods=["POST"])
def request_adjustment(quotation: str, kind: str, percent: float | str, reason: str) -> dict:
	"""Ask the next level up to approve `percent` for `kind` on a draft quotation I can edit."""
	settings = get_settings()
	if kind not in settings.controlled_kinds():
		frappe.throw(_("{0} adjustments are not limited, so no approval is needed.").format(_(kind)))
	doc = frappe.get_doc("Quotation", quotation)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft quotation can get an adjustment approved."))
	percent = flt(percent, 2)
	if not 0 < percent <= 100:
		frappe.throw(_("Percent must be more than 0 and at most 100."))
	if not (reason or "").strip():
		frappe.throw(_("Give a reason for the request."))

	level = settings.get_user_level()
	if not level:
		frappe.throw(
			_("You have no quotation approval level. Ask HR to give you your designation's role."),
			frappe.PermissionError,
		)
	limit = settings.get_limit(level, kind)
	if settings.is_within(limit, percent):
		frappe.throw(
			_("{0}% is within your limit ({1}); set it on the quotation.").format(
				percent, format_limit(limit)
			)
		)
	open_request = frappe.db.get_value(
		"TBO Quotation Adjustment",
		{"quotation": doc.name, "kind": kind, "status": ("in", OPEN_STATUSES)},
		"name",
	)
	if open_request:
		frappe.throw(_("{0} is already waiting for a decision on this quotation.").format(open_request))
	next_level = settings.get_next_level(level.rank)
	if not next_level:
		frappe.throw(_("There is no higher level to approve this."))

	adjustment = new_adjustment(doc, kind, percent, get_list_value(doc))
	adjustment.update(
		{
			"status": "Pending",
			"reason": reason.strip(),
			"current_level": next_level.level,
			"current_rank": cint(next_level.rank),
		}
	)
	adjustment.add_step("Requested", level.level, percent, reason.strip())
	adjustment.insert(ignore_permissions=True)
	adjustment.notify_level(next_level)
	return serialize_adjustment(adjustment)


@frappe.whitelist(methods=["POST"])
def decide_adjustment(
	name: str, action: str, percent: float | str | None = None, remarks: str | None = None
) -> dict:
	"""approve (optionally a lower percent), reject (with remarks), forward to the next level, or
	override (no-limit level only, with a reason). The requester can never decide their own."""
	adjustment = frappe.get_doc("TBO Quotation Adjustment", name)
	settings = get_settings()
	level = settings.get_user_level()
	adjustment.check_can_decide(level, action)
	remarks = (remarks or "").strip() or None
	percent = None if percent in (None, "") else flt(percent, 2)
	value = get_list_value(frappe.get_doc("Quotation", adjustment.quotation))

	if action == "approve":
		adjustment.approve(level, percent, remarks, value)
	elif action == "reject":
		adjustment.reject(level, remarks)
	elif action == "forward":
		adjustment.forward(level, remarks)
	else:
		adjustment.override(level, percent, remarks, value)
	adjustment.save(ignore_permissions=True)

	if action == "forward":
		adjustment.notify_level(settings.get_level(adjustment.current_level))
	else:
		adjustment.notify_requester()
	return serialize_adjustment(adjustment)


@frappe.whitelist()
def get_pending_approvals() -> list[dict]:
	"""Open requests I may decide: waiting at my level, or below it when my level approves
	directly. My own requests are left out."""
	level = get_settings().get_user_level()
	if not level:
		return []
	Adjustment = frappe.qb.DocType("TBO Quotation Adjustment")
	waiting = Adjustment.current_level == level.level
	if cint(level.approves_directly) or cint(level.unlimited):
		waiting = waiting | (Adjustment.current_rank < cint(level.rank))
	rows = (
		frappe.qb.from_(Adjustment)
		.select(*[Adjustment[field] for field in LIST_FIELDS])
		.where(
			Adjustment.status.isin(OPEN_STATUSES) & (Adjustment.requested_by != frappe.session.user) & waiting
		)
		.orderby(Adjustment.creation)
		.limit(PENDING_LIMIT)
		.run(as_dict=True)
	)
	names = get_full_names({row.requested_by for row in rows})
	settings = get_settings()
	pending = []
	for row in rows:
		item = serialize_adjustment(row, names)
		item["can_approve_in_full"] = settings.is_within(
			settings.get_limit(level, row.kind), row.requested_percent
		)
		pending.append(item)
	return pending


def serialize_level(level) -> dict | None:
	if not level:
		return None
	return {
		"level": level.level,
		"role": level.role,
		"rank": cint(level.rank),
		"approves_directly": bool(cint(level.approves_directly)),
		"unlimited": bool(cint(level.unlimited)),
	}


def serialize_adjustment(row, names: dict | None = None) -> dict:
	data = {field: row.get(field) for field in LIST_FIELDS}
	for field in ("previous_percent", "requested_percent", "approved_percent", "quotation_value"):
		data[field] = flt(data[field], 2)
	data["voided"] = bool(cint(data["voided"]))
	data["within_limit"] = bool(cint(data["within_limit"]))
	data["current_rank"] = cint(data["current_rank"])
	if names is not None:
		data["requested_by_name"] = names.get(row.requested_by, row.requested_by)
	return data
