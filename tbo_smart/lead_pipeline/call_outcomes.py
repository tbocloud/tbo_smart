"""What a call does to the lead (docs/lead-pipeline.md §2.3).

One helper, `apply_call_outcome`, is used by every way a call is recorded: the hub's Log call,
a call batch item, a MyOperator call and the batch sync. It keeps the lead's last call result
(`tbo_last_call_result`, `tbo_last_call_on`) and moves the stage, so a BD can tell a lead that
did not pick up from one that declined, without writing a note to say so.
"""

import frappe
from frappe.utils import cint, get_datetime, getdate

from tbo_smart.lead_pipeline import pipeline_enabled
from tbo_smart.lead_pipeline.lead_category import as_list
from tbo_smart.lead_pipeline.statuses import (
	CONTACTED,
	FOLLOW_UP_REQUIRED,
	NEW_LEAD,
	NOT_INTERESTED,
	NOT_RESPONDING,
	PROGRESS,
	WRONG_NUMBER,
	is_closed,
)
from tbo_smart.lead_pipeline.transitions import set_lead_status
from tbo_smart.query import aggregate
from tbo_smart.sales.activities import add_follow_up_task, next_follow_ups
from tbo_smart.sales.call_batches import (
	BUSY,
	CALL_BACK,
	CONNECTED,
	NOT_ANSWERED,
	OUTCOMES,
)
from tbo_smart.sales.call_batches import NOT_INTERESTED as DECLINED
from tbo_smart.sales.call_batches import WRONG_NUMBER as INVALID
from tbo_smart.smart_leads import FOLLOW_UP_TYPE
from tbo_smart.telephony import ANSWERED, MISSED, VOICEMAIL

# a call logged by hand or synced from MyOperator, as the outcome it means
RESULT_OF = {
	"Connected": CONNECTED,
	"No answer": NOT_ANSWERED,
	"Voicemail": NOT_ANSWERED,
	ANSWERED: CONNECTED,
	MISSED: NOT_ANSWERED,
	VOICEMAIL: NOT_ANSWERED,
	**{outcome: outcome for outcome in OUTCOMES},
}
# outcome -> (stage it sets, stages it may move the lead out of). A lead at a meeting or later is
# left where it is: the BD closes it by hand with a reason.
EARLY = (NEW_LEAD, CONTACTED, NOT_RESPONDING)
MOVES = {
	NOT_ANSWERED: (NOT_RESPONDING, EARLY),
	BUSY: (NOT_RESPONDING, EARLY),
	CONNECTED: (CONTACTED, (NEW_LEAD, NOT_RESPONDING)),
	CALL_BACK: (FOLLOW_UP_REQUIRED, EARLY),
	DECLINED: (NOT_INTERESTED, None),
	INVALID: (WRONG_NUMBER, None),
}
BEFORE_MEETING = max(PROGRESS[stage] for stage in EARLY)


def result_of(outcome: str | None) -> str | None:
	"""The outcome a call log entry (`Connected`, `No answer`, MyOperator's `Missed`, …) means."""
	return RESULT_OF.get(outcome or "")


def target_stage(current: str | None, result: str) -> str | None:
	"""The stage a call with `result` moves a lead at `current` to, else None."""
	if not current or is_closed(current) or result not in MOVES:
		return None
	target, sources = MOVES[result]
	if current == target:
		return None
	if sources is None:
		# declined or wrong number: any stage before the first meeting
		return target if PROGRESS.get(current, 0) <= BEFORE_MEETING else None
	return target if current in sources else None


def apply_call_outcome(lead: str, outcome: str, called_at=None, callback_at=None) -> str | None:
	"""Record `outcome` (anything `result_of` knows) as the lead's last call and move its stage.
	A call older than the lead's last call changes nothing, so a call counted twice (logged by
	hand, then synced) or arriving late cannot undo a newer one. Returns the stage it moved
	the lead to, else None."""
	result = result_of(outcome)
	if not result or not frappe.db.exists("Lead", lead):
		return None
	called_at = get_datetime(called_at) if called_at else frappe.utils.now_datetime()
	current, last_on = frappe.db.get_value("Lead", lead, ["tbo_stage", "tbo_last_call_on"], for_update=True)
	if last_on and called_at <= get_datetime(last_on):
		return None
	frappe.db.set_value(
		"Lead", lead, {"tbo_last_call_result": result, "tbo_last_call_on": called_at}, update_modified=False
	)
	target = target_stage(current, result) if pipeline_enabled() else None
	if result == CALL_BACK and callback_at:
		schedule_call_back(lead, callback_at)
	if not target:
		return None
	set_lead_status(lead, target, frappe._("{0} call").format(frappe._(result)))
	return target


def schedule_call_back(lead: str, callback) -> None:
	"""The lead's follow-up becomes `callback`: its call back time, and a Follow-up task for that
	day unless one is already due then."""
	callback = get_datetime(callback)
	frappe.db.set_value("Lead", lead, "tbo_callback_at", callback, update_modified=False)
	if next_follow_ups("Lead", [lead]).get(lead) != getdate(callback):
		owner = frappe.db.get_value("Lead", lead, "lead_owner") or frappe.session.user
		add_follow_up_task(
			"Lead",
			lead,
			callback,
			title=frappe._("Call back {0}").format(frappe.db.get_value("Lead", lead, "lead_name") or lead),
			assigned_to=owner,
			task_type=FOLLOW_UP_TYPE,
		)


def result_filter(values) -> list[str]:
	"""A `last_call_result` filter (a value, a list or a JSON list) as a list of known outcomes."""
	results = as_list(values)
	unknown = [value for value in results if value not in OUTCOMES]
	if unknown:
		frappe.throw(frappe._("last_call_result must be one of {0}.").format(", ".join(OUTCOMES)))
	return results


def get_counts() -> dict:
	"""{result: leads} over the leads the acting user may see (leads never called are left out)."""
	rows = frappe.get_list(
		"Lead",
		filters={"tbo_last_call_result": ("is", "set")},
		fields=["tbo_last_call_result", aggregate("count", "name", "total")],
		group_by="tbo_last_call_result",
	)
	return {row.tbo_last_call_result: cint(row.total) for row in rows}
