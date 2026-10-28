"""Leads nobody has touched (docs/lead-departments.md §7): the hub's "stale" list.

A lead is stale when it is open (not Closed – Won, Closed – Lost or On Hold) and its newest
activity is at least `days` old. Activity is the latest of: a note (Comment), an email
(Communication), a ToDo, an Event (calls and meetings logged by hand or by MyOperator), a TBO
Call, a stage change, or the lead's creation. A department's `stale_after_days`, when set,
replaces `days` for its leads. One grouped query per source, none per lead.
"""

import frappe
from frappe.query_builder.functions import Max
from frappe.utils import cint, get_datetime, now_datetime

from tbo_smart.lead_pipeline.statuses import ON_HOLD, OPEN_STATUSES
from tbo_smart.sales.departments import DEPARTMENT

STALE_STAGES = tuple(stage for stage in OPEN_STATUSES if stage != ON_HOLD)
LEAD_FIELDS = ["name", "lead_name", "company_name", "lead_owner", "tbo_department", "tbo_stage", "creation"]


def latest_activity(names: list[str]) -> dict[str, object]:
	"""{lead: the newest of its activities} for the leads that have any."""
	Comment = frappe.qb.DocType("Comment")
	Communication = frappe.qb.DocType("Communication")
	ToDo = frappe.qb.DocType("ToDo")
	Event = frappe.qb.DocType("Event")
	Participant = frappe.qb.DocType("Event Participants")
	Call = frappe.qb.DocType("TBO Call")
	Stage = frappe.qb.DocType("TBO Stage Change")
	sources = [
		frappe.qb.from_(Comment)
		.select(Comment.reference_name, Max(Comment.creation))
		.where(
			(Comment.reference_doctype == "Lead")
			& (Comment.comment_type == "Comment")
			& Comment.reference_name.isin(names)
		)
		.groupby(Comment.reference_name),
		frappe.qb.from_(Communication)
		.select(Communication.reference_name, Max(Communication.creation))
		.where((Communication.reference_doctype == "Lead") & Communication.reference_name.isin(names))
		.groupby(Communication.reference_name),
		frappe.qb.from_(ToDo)
		.select(ToDo.reference_name, Max(ToDo.modified))
		.where((ToDo.reference_type == "Lead") & ToDo.reference_name.isin(names))
		.groupby(ToDo.reference_name),
		frappe.qb.from_(Event)
		.join(Participant)
		.on((Participant.parent == Event.name) & (Participant.parenttype == "Event"))
		.select(Participant.reference_docname, Max(Event.creation))
		.where((Participant.reference_doctype == "Lead") & Participant.reference_docname.isin(names))
		.groupby(Participant.reference_docname),
		frappe.qb.from_(Call)
		.select(Call.lead, Max(Call.started_at))
		.where(Call.lead.isin(names))
		.groupby(Call.lead),
		frappe.qb.from_(Stage)
		.select(Stage.parent, Max(Stage.changed_on))
		.where((Stage.parenttype == "Lead") & Stage.parent.isin(names))
		.groupby(Stage.parent),
	]
	newest: dict[str, object] = {}
	for query in sources:
		for name, at in query.run():
			if at and (name not in newest or get_datetime(at) > get_datetime(newest[name])):
				newest[name] = at
	return newest


def stale_leads(days: int = 3) -> list[dict]:
	"""The open leads the session user may see that have been quiet for `days` (or their
	department's own number of days), longest quiet first: [{name, lead_name, company_name,
	lead_owner, tbo_department, tbo_stage, last_activity_on, idle_days}]."""
	leads = frappe.get_list(
		"Lead",
		filters={"tbo_stage": ("in", STALE_STAGES)},
		fields=LEAD_FIELDS,
		limit_page_length=0,
	)
	if not leads:
		return []
	own_days = dict(
		frappe.get_all(
			DEPARTMENT,
			filters={"stale_after_days": (">", 0)},
			fields=["name", "stale_after_days"],
			as_list=True,
		)
	)
	newest = latest_activity([lead.name for lead in leads])
	now = now_datetime()
	rows = []
	for lead in leads:
		last = max(get_datetime(lead.creation), get_datetime(newest.get(lead.name) or lead.creation))
		idle = (now - last).days
		if idle >= cint(own_days.get(lead.tbo_department) or days):
			rows.append(
				{
					"name": lead.name,
					"lead_name": lead.lead_name,
					"company_name": lead.company_name,
					"lead_owner": lead.lead_owner,
					"tbo_department": lead.tbo_department,
					"tbo_stage": lead.tbo_stage,
					"last_activity_on": last,
					"idle_days": idle,
				}
			)
	rows.sort(key=lambda row: (-row["idle_days"], row["name"]))
	return rows
