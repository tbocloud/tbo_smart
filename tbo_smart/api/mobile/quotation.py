"""TBO Smart quotation approvals: a thin layer over tbo_smart.api.quotation_adjustment.

The CRM's Approvals page and these methods call the same functions, so the app and the web
never disagree about who may decide what. The app's Approvals screen is for the levels that
decide requests (BDM, Sales & Marketing Head, Director, CEO: `approve_quotations`); the trail
is open to anyone who may read the request, so a requester's push opens it too.
Contract: docs/tbo-smart-api-ess-crm.md §5.2.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.api import quotation_adjustment as adjustments
from tbo_smart.api.mobile.capabilities import require
from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	get_settings,
)

TABS = ("waiting", "mine")
APPROVER_ONLY = _("Only BDM, Sales & Marketing Head, Director and CEO decide quotation approvals.")


@frappe.whitelist()
def get_limits() -> dict:
	"""My level and limits (quotation_adjustment.get_adjustment_limits, unchanged)."""
	return adjustments.get_adjustment_limits()


@frappe.whitelist()
def get_approvals(tab: str = "waiting") -> list[dict]:
	"""`waiting`: open requests I may decide, oldest first (approvers only). `mine`: the
	requests I made, newest first. Bounded at 200, so a bare array."""
	if tab not in TABS:
		frappe.throw(_("Tab must be one of {0}.").format(", ".join(TABS)))
	if tab == "mine":
		return adjustments.get_adjustment_requests("mine")
	require("approve_quotations", APPROVER_ONLY)
	return adjustments.get_pending_approvals()


@frappe.whitelist()
def get_trail(name: str) -> dict:
	"""One request with its steps, oldest first, and the actions open to me on it now."""
	data = adjustments.get_adjustment_trail(name)
	data["currency"] = adjustments.get_currencies({data["quotation"]}).get(data["quotation"])
	data["actions"] = allowed_actions(frappe.get_doc("TBO Quotation Adjustment", name))
	return data


@frappe.whitelist(methods=["POST"])
def decide(name: str, action: str, percent: float | str | None = None, remarks: str | None = None) -> dict:
	"""approve (in full or a lower %), reject (remarks required), forward, or override (no-limit
	level, reason required). Returns the request as `get_trail` does."""
	require("approve_quotations", APPROVER_ONLY)
	adjustments.decide_adjustment(name, action, percent, remarks)
	return get_trail(name)


def allowed_actions(adjustment) -> dict:
	"""What I may do with the request now, by the same checks decide_adjustment makes, so the
	app shows only buttons the server will accept. `approve_max` is the highest % I may
	approve (the requested % or my limit, whichever is lower); None when I can't approve."""
	settings = get_settings()
	level = settings.get_user_level()
	limit = settings.get_limit(level, adjustment.kind) if level else 0
	requested = adjustment.requested_percent

	def can(action: str) -> bool:
		try:
			adjustment.check_can_decide(level, action)
		except (frappe.PermissionError, frappe.ValidationError):
			return False
		return True

	can_decide = can("approve")
	approve_max = None
	if can_decide and (limit is None or limit > 0):
		approve_max = requested if settings.is_within(limit, requested) else limit
	next_level = (
		settings.get_next_level(max(cint(adjustment.current_rank), cint(level.rank))) if level else None
	)
	return {
		"approve": approve_max is not None,
		"approve_max": approve_max,
		"reject": can_decide and settings.is_within(limit, requested),
		"forward": can_decide and bool(next_level),
		"forward_to": next_level.level if can_decide and next_level else None,
		"override": can("override"),
	}
