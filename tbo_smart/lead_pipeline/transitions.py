"""Automatic lead statuses (docs/lead-pipeline.md §2.2).

Progress moves a lead along: a meeting scheduled → Meeting Scheduled; the Nth meeting's MOM
submitted → Nth Meeting Completed (and later, quotations sent, negotiated and accepted). An
automatic move only ever goes forward (`PROGRESS`) and never touches a closed lead, so a status a
person set by hand is never pulled back. People can still change the status by hand at any
time. Each move is saved as the lead's own stage change, so its stage log (`tbo_stage_log`) keeps
the history, with a timeline comment saying why.
"""

import frappe
from frappe import _

from tbo_smart.lead_pipeline import pipeline_enabled
from tbo_smart.lead_pipeline.statuses import PROGRESS, is_closed
from tbo_smart.sales.stages import system_save


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
	# read under the row lock, held until the save, so a status someone sets meanwhile wins
	current = frappe.db.get_value("Lead", lead, "tbo_stage", for_update=True)
	if not moves_forward(current, target):
		return False
	set_lead_status(lead, target, reason)
	return True


def set_lead_status(lead: str, status: str, reason: str) -> None:
	"""Save the stage as a system change (whoever triggered it may not be the lead's owner), so
	its stage log keeps it, and say why on the lead's timeline."""
	doc = frappe.get_doc("Lead", lead)
	doc.tbo_stage = status
	doc.flags.tbo_auto_status = True
	system_save(doc, ignore_permissions=True)
	doc.add_comment("Info", _("Status set to {0}: {1}").format(status, reason))
