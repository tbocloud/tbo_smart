"""Automatic lead statuses (docs/lead-pipeline.md §2.2).

Progress moves a lead along: a meeting scheduled → Meeting Scheduled; the Nth meeting's MOM
submitted → Nth Meeting Completed (and later, quotations sent, negotiated and accepted). An
automatic move only ever goes forward (`PROGRESS`) and never touches a closed lead, so a status a
person set by hand is never pulled back. People can still change the status by hand at any
time. Each move is saved as the lead's own status change, so CRM's Status Change Log keeps the
history, with a timeline comment saying why.
"""

import frappe
from frappe import _

from tbo_smart.lead_pipeline import pipeline_enabled
from tbo_smart.lead_pipeline.statuses import PROGRESS, is_closed


def moves_forward(current: str | None, target: str) -> bool:
	"""Whether an automatic move from `current` to `target` is allowed."""
	if not target or current == target or is_closed(current):
		return False
	return PROGRESS.get(target, -1) > PROGRESS.get(current, 0)


def advance_lead(lead: str, target: str, reason: str) -> bool:
	"""Move the lead on to `target` when the pipeline is on and it is a move forward.
	Returns whether the status changed."""
	if not pipeline_enabled():
		return False
	current = frappe.db.get_value("CRM Lead", lead, "status")
	if not moves_forward(current, target):
		return False
	set_lead_status(lead, target, reason)
	return True


def set_lead_status(lead: str, status: str, reason: str) -> None:
	"""Save the status as a system change (whoever triggered it may not be the lead's owner),
	so CRM logs it, and say why on the lead's timeline."""
	doc = frappe.get_doc("CRM Lead", lead)
	doc.status = status
	doc.flags.tbo_auto_status = True
	doc.save(ignore_permissions=True)
	doc.add_comment("Info", _("Status set to {0}: {1}").format(status, reason))
