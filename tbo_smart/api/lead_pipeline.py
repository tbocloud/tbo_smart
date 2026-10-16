"""The lead pipeline: the lead's summary, its meetings and their minutes
(docs/lead-pipeline.md §7).

Every method needs TBO Smart Settings → Lead pipeline, and checks the lead under the sales rules
(owner, assignee, sales hierarchy, Sales Manager: tbo_smart.sales.permissions) before reading or
writing. Writes are POST and run as the session user.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_fullname, getdate, now_datetime

from tbo_smart import ai
from tbo_smart.lead_pipeline import meetings as m
from tbo_smart.lead_pipeline import pipeline_enabled, require_pipeline
from tbo_smart.lead_pipeline.mom import (
	apply_mom,
	mark_submitted,
	mom_summary,
	validate_submit,
)
from tbo_smart.lead_pipeline.mom import (
	draft_mom as ai_draft_mom,
)
from tbo_smart.lead_pipeline.statuses import NEW_LEAD, status_type
from tbo_smart.sales.activities import OPEN, task_title
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.sales.stages import set_lead_stage
from tbo_smart.smart_leads import notes_enabled

SCHEDULE_FIELDS = (
	"meeting_type",
	"meeting_link",
	"location",
	"scheduled_at",
	"duration_minutes",
	"participants",
	"agenda",
	"bde",
)
HELD = (m.IN_PROGRESS, m.COMPLETED, m.MOM_PENDING, m.MOM_SUBMITTED, m.FOLLOW_UP_REQUIRED)


@frappe.whitelist()
def get_access() -> dict:
	"""{enabled, ai}: whether the lead page shows the pipeline, and the AI MOM draft."""
	if not pipeline_enabled():
		return {"enabled": False, "ai": False}
	readable = frappe.has_permission("Lead", "read")
	return {"enabled": bool(readable), "ai": bool(readable) and ai_available()}


def ai_available() -> bool:
	return notes_enabled() and ai.ai_ready()


# ---------- the lead ----------


@frappe.whitelist()
def get_lead_pipeline(lead: str) -> dict:
	"""The lead page's pipeline: status, the summary line (last meeting, next activity, meetings
	completed, needs immediate follow-up), the meeting cards and the deal once Won."""
	require_lead(lead, "read")
	doc = frappe.db.get_value("Lead", lead, ["name", "lead_name", "tbo_stage"], as_dict=True)
	deal = lead_opportunity(lead)
	rows = frappe.get_all(
		m.DOCTYPE, filters={"lead": lead}, fields=["*"], order_by="stage asc, scheduled_at asc"
	)
	now = now_datetime()
	cards = [meeting_card(row) for row in rows]
	held = [row for row in rows if row.status in HELD]
	upcoming = [row for row in rows if row.status in m.UPCOMING and get_datetime(row.scheduled_at) >= now]
	task = next_task(lead)
	return {
		"lead": lead,
		"status": doc.tbo_stage,
		"status_type": status_type(doc.tbo_stage),
		"deal": deal,
		"can_write": bool(frappe.has_permission("Lead", "write", lead)),
		"ai": ai_available(),
		"summary": {
			"last_meeting_on": max((row.scheduled_at for row in held), default=None),
			"next_activity": next_activity(upcoming, task),
			"meetings_completed": sum(row.status == m.MOM_SUBMITTED for row in rows),
			"needs_follow_up": needs_follow_up(doc, rows, upcoming, task, bool(deal)),
		},
		"meetings": cards,
	}


def next_task(lead: str):
	"""The lead's earliest open task with a due day: {name, title, due_date}."""
	task = frappe.db.get_value(
		"ToDo",
		{"reference_type": "Lead", "reference_name": lead, "status": OPEN, "date": ("is", "set")},
		["name", "description", "date as due_date"],
		as_dict=True,
		order_by="date asc",
	)
	if task:
		task.title = task_title(task.pop("description"))
	return task


def next_activity(upcoming: list, task) -> dict | None:
	"""The earliest of the next meeting and the next open follow-up."""
	options = []
	if upcoming:
		row = min(upcoming, key=lambda row: get_datetime(row.scheduled_at))
		options.append(
			{"kind": "meeting", "at": row.scheduled_at, "label": row.stage_label, "meeting": row.name}
		)
	if task:
		options.append({"kind": "follow_up", "at": task.due_date, "label": task.title, "task": task.name})
	return min(options, key=lambda option: get_datetime(option["at"]), default=None)


def needs_follow_up(lead, rows: list, upcoming: list, task, converted: bool = False) -> dict:
	"""{flag, reasons}: MOM pending, a meeting that asked for a follow-up, an overdue follow-up,
	or an open lead past New Lead with nothing planned."""
	reasons = [
		_("MOM pending for the {0}").format(row.stage_label) for row in rows if row.status == m.MOM_PENDING
	]
	reasons += [
		_("Follow-up required after the {0}").format(row.stage_label)
		for row in rows
		if row.status == m.FOLLOW_UP_REQUIRED
	]
	if task and getdate(task.due_date) < getdate():
		reasons.append(_("Follow-up overdue since {0}").format(frappe.utils.formatdate(task.due_date)))
	open_lead = status_type(lead.tbo_stage) not in ("Won", "Lost") and not converted
	if open_lead and lead.tbo_stage != NEW_LEAD and not upcoming and not task:
		reasons.append(_("No next step planned"))
	return {"flag": bool(reasons) and open_lead, "reasons": reasons if open_lead else []}


def meeting_card(row) -> dict:
	actions = frappe.get_all(
		"TBO Lead Meeting Action", filters={"parent": row.name, "parenttype": m.DOCTYPE}, pluck="done"
	)
	return {
		"name": row.name,
		"stage": row.stage,
		"stage_label": row.stage_label,
		"status": row.status,
		"meeting_type": row.meeting_type,
		"meeting_link": row.meeting_link,
		"location": row.location,
		"scheduled_at": row.scheduled_at,
		"ends_at": m.ends_at(row),
		"duration_minutes": row.duration_minutes,
		"participants": row.participants,
		"bde": row.bde,
		"bde_name": get_fullname(row.bde) if row.bde else None,
		"summary": mom_summary(row),
		"client_interest": row.client_interest,
		"next_follow_up_on": row.next_follow_up_on,
		"actions_open": sum(not cint(done) for done in actions),
		"actions_total": len(actions),
		"submitted_on": row.submitted_on,
		"prepared_by_name": get_fullname(row.prepared_by) if row.prepared_by else None,
		"mom_pending_since": row.mom_pending_since,
		"escalation_level": cint(row.escalation_level),
		"follow_up_task": row.follow_up_task,
		"next_meeting": row.next_meeting,
	}


# ---------- one meeting ----------


@frappe.whitelist()
def get_meeting(meeting: str) -> dict:
	"""The full meeting: schedule, MOM fields, action items and attachments."""
	doc = get_meeting_doc(meeting, "read")
	return serialize_meeting(doc)


def serialize_meeting(doc) -> dict:
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": m.DOCTYPE, "attached_to_name": doc.name},
		fields=["name", "file_name", "file_url", "is_private"],
		order_by="creation asc",
	)
	return {
		**meeting_card(doc),
		"lead": doc.lead,
		"lead_name": doc.lead_name,
		**{
			field: doc.get(field)
			for field in (
				"agenda",
				"discussion_points",
				"client_requirements",
				"decisions",
				"objections",
				"quotation_feedback",
				"next_meeting_details",
			)
		},
		"action_items": [
			{
				"item": row.item,
				"responsible": row.responsible,
				"due_date": row.due_date,
				"done": cint(row.done),
			}
			for row in doc.action_items
		],
		"attachments": files,
		"can_write": bool(doc.has_permission("write")),
	}


@frappe.whitelist(methods=["POST"])
def schedule_meeting(lead: str, values: dict | str) -> dict:
	"""A new meeting on the lead (its number comes next; the lead moves to Meeting Scheduled)."""
	require_lead(lead, "write")
	doc = create_meeting(lead, parse(values))
	return serialize_meeting(doc)


def create_meeting(lead: str, values: dict):
	doc = frappe.new_doc(m.DOCTYPE)
	doc.lead = lead
	doc.update({field: values.get(field) for field in SCHEDULE_FIELDS if values.get(field) not in (None, "")})
	if doc.bde and doc.bde != frappe.session.user:
		check_bde(doc.bde)
	if not doc.scheduled_at:
		frappe.throw(_("Choose when the meeting is."))
	if get_datetime(doc.scheduled_at) < now_datetime():
		frappe.throw(_("A new meeting can't be in the past."))
	doc.insert()
	return doc


def check_bde(user: str) -> None:
	if not frappe.db.get_value("User", {"name": user, "enabled": 1, "user_type": "System User"}):
		frappe.throw(_("{0} is not an active user.").format(user))


@frappe.whitelist(methods=["POST"])
def update_meeting(meeting: str, values: dict | str) -> dict:
	"""Change the schedule. A new time on a meeting that hasn't had its MOM makes it Rescheduled
	(its reminder and MOM clock start again)."""
	doc = get_meeting_doc(meeting, "write")
	if doc.status in m.FINAL:
		frappe.throw(_("This meeting is {0}; schedule a new one instead.").format(_(doc.status)))
	values = parse(values)
	before, bde = get_datetime(doc.scheduled_at), doc.bde
	doc.update({field: values.get(field) for field in SCHEDULE_FIELDS if field in values})
	if doc.bde != bde:
		check_bde(doc.bde)
	if get_datetime(doc.scheduled_at) != before:
		if get_datetime(doc.scheduled_at) < now_datetime():
			frappe.throw(_("Choose a time in the future to reschedule."))
		doc.update(
			{"status": m.RESCHEDULED, "reminded_at": None, "mom_pending_since": None, "escalation_level": 0}
		)
	doc.save()
	return serialize_meeting(doc)


@frappe.whitelist(methods=["POST"])
def set_meeting_status(meeting: str, status: str) -> dict:
	"""In progress, completed, follow-up required or cancelled, by hand."""
	if status not in m.MANUAL_STATUSES:
		frappe.throw(_("{0} can't be set by hand.").format(status))
	doc = get_meeting_doc(meeting, "write")
	doc.status = status
	doc.save()
	return serialize_meeting(doc)


@frappe.whitelist(methods=["POST"])
def save_mom(
	meeting: str,
	values: dict | str,
	submit: bool = False,
	lead_status: str | None = None,
	lost_reason: str | None = None,
	next_meeting: dict | str | None = None,
) -> dict:
	"""Save the MOM as a draft, or submit it (`submit`): the meeting turns MOM Submitted, the
	lead moves to Nth Meeting Completed, the next follow-up date becomes a Sales follow-up task,
	`next_meeting` (schedule values) books the next meeting, and `lead_status` (optional, with
	`lost_reason` for Closed – Lost) is set by hand after the automatic move."""
	doc = get_meeting_doc(meeting, "write")
	if doc.status in m.FINAL:
		frappe.throw(_("This meeting is {0}; its MOM can't change.").format(_(doc.status)))
	apply_mom(doc, parse(values))
	next_values = parse(next_meeting) if next_meeting else None
	if cint(submit):
		closing = status_type(lead_status) in ("Won", "Lost")
		if status_type(lead_status) == "Lost" and not (lost_reason or "").strip():
			frappe.throw(_("Give a reason for losing the lead."))
		validate_submit(doc, bool(next_values), closing)
		mark_submitted(doc)
	doc.save()
	if cint(submit):
		if next_values:
			doc.db_set("next_meeting", create_meeting(doc.lead, next_values).name)
		if lead_status and lead_status != frappe.db.get_value("Lead", doc.lead, "tbo_stage"):
			set_lead_stage(doc.lead, lead_status, lost_reason)
	return serialize_meeting(doc)


@frappe.whitelist(methods=["POST"])
def draft_mom(meeting: str, notes: str) -> dict:
	"""The AI's MOM from rough notes, for the BDE to check. Nothing is written."""
	doc = get_meeting_doc(meeting, "write")
	if not ai_available():
		frappe.throw(_("The AI MOM draft is not switched on."))
	return ai_draft_mom(doc, notes)


# ---------- helpers ----------


def require_lead(lead: str, ptype: str) -> None:
	require_pipeline()
	if not isinstance(lead, str) or not frappe.db.exists("Lead", lead):
		frappe.throw(_("Lead {0} not found.").format(lead), frappe.DoesNotExistError)
	frappe.has_permission("Lead", ptype, lead, throw=True)


def get_meeting_doc(meeting: str, ptype: str):
	require_pipeline()
	if not isinstance(meeting, str) or not frappe.db.exists(m.DOCTYPE, meeting):
		frappe.throw(_("Meeting {0} not found.").format(meeting), frappe.DoesNotExistError)
	doc = frappe.get_doc(m.DOCTYPE, meeting)
	doc.check_permission(ptype)
	return doc


def parse(values) -> dict:
	if isinstance(values, str):
		try:
			values = frappe.parse_json(values)
		except ValueError:
			values = None
	if not isinstance(values, dict):
		frappe.throw(_("values must be an object."))
	return frappe._dict(values)
