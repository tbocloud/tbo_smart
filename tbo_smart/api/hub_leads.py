"""Leads for the TBO Support hub (docs/hub-leads-api.md).

The hub calls these with one API key, its integration user's (TBO Smart Settings → Hub
integration role, "Hub Integration" by default). Every call names the hub user it acts for in
`acting_user` (their email); that user must exist here, be enabled and hold a sales role, and the
call runs as them: lists are their `frappe.get_list`, a lead opens only when they may read it,
and every write is theirs (owner, comments, the lead's default owner). Anyone else calling these
acts as themselves and may not name another user. Writes are POST.
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.query_builder.functions import Min
from frappe.utils import cint, flt, get_datetime, getdate

from tbo_smart.api.mobile.capabilities import CRM_ROLES, get_capabilities
from tbo_smart.api.mobile.crm import check_assignee, get_crm_users
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.lead_pipeline import rate_card, require_quotations, setting
from tbo_smart.lead_pipeline.dashboard import people, scope_owners
from tbo_smart.lead_pipeline.meetings import DOCTYPE as MEETING
from tbo_smart.lead_pipeline.overview import LeadOverview, SalesActivity, period_range
from tbo_smart.lead_pipeline.quotations import lead_quotations
from tbo_smart.lead_pipeline.setup import HUB_ROLE, SEGMENTS
from tbo_smart.lead_pipeline.statuses import STATUS_NAMES, stage_options, status_type
from tbo_smart.sales import erpnext_installed
from tbo_smart.sales.activities import (
	OPEN,
	add_follow_up_task,
	events_of,
	next_follow_ups,
	notes_of,
	plain_text,
)
from tbo_smart.sales.activities import add_note as add_comment_note
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.sales.stages import set_lead_stage
from tbo_smart.smart_leads import FOLLOW_UP_TYPE, call_today_size, scoring_enabled
from tbo_smart.smart_leads.call_today import build_call_today

VIEWS = ("all", "mine", "call_today", "follow_up")
TEMPERATURES = ("Hot", "Warm", "Cold")
MAX_PAGE_LENGTH = 100
# what the hub may write on a lead, by the key it sends
LEAD_FIELDS = {
	"first_name": "first_name",
	"last_name": "last_name",
	"company_name": "company_name",
	"email_id": "email_id",
	"mobile_no": "mobile_no",
	"phone": "phone",
	"whatsapp_no": "whatsapp_no",
	"job_title": "job_title",
	"website": "website",
	"source": "source",
	"territory": "territory",
	"industry": "industry",
	"segment": "tbo_segment",
	"lead_owner": "lead_owner",
}
ROW_FIELDS = [
	"name",
	"lead_name",
	"company_name",
	"email_id",
	"mobile_no",
	"tbo_stage",
	"tbo_segment as segment",
	"lead_owner",
	"tbo_score as score",
	"modified",
]
LEAD_DETAIL_FIELDS = [
	"name",
	"lead_name",
	"first_name",
	"last_name",
	"company_name",
	"email_id",
	"mobile_no",
	"phone",
	"whatsapp_no",
	"job_title",
	"website",
	"source",
	"territory",
	"industry",
	"tbo_segment",
	"tbo_stage",
	"status",
	"lead_owner",
	"tbo_score",
	"tbo_temperature",
	"tbo_score_reason",
	"tbo_lost_reason",
	"tbo_lost_notes",
	"creation",
	"modified",
]
QUOTATION_FIELDS = [
	"name",
	"docstatus",
	"tbo_quote_status",
	"transaction_date",
	"valid_till",
	"currency",
	"grand_total",
	"tbo_revision_no",
	"tbo_is_latest",
]


# ---------- who is acting ----------


def integration_role() -> str:
	return setting("hub_integration_role") or HUB_ROLE


def resolve_acting_user(acting_user: str | None) -> str:
	"""The user the call runs as. The integration user must name a hub user; anyone else acts as
	themselves (naming yourself is allowed)."""
	caller = frappe.session.user
	acting_user = (acting_user or "").strip()
	is_integration = integration_role() in frappe.get_roles(caller)
	if not is_integration:
		if acting_user and acting_user != caller:
			frappe.throw(
				_("Only the hub's integration user may act for someone else."), frappe.PermissionError
			)
		return caller
	if not acting_user:
		frappe.throw(_("Name the hub user this call is for (acting_user)."), frappe.PermissionError)
	user = frappe.db.get_value("User", acting_user, ["name", "enabled", "user_type"], as_dict=True)
	if not user or not user.enabled or user.user_type != "System User":
		frappe.throw(_("{0} is not an enabled user here.").format(acting_user), frappe.PermissionError)
	roles = set(frappe.get_roles(user.name))
	if not roles & CRM_ROLES:
		frappe.throw(_("{0} has no sales role here.").format(acting_user), frappe.PermissionError)
	if integration_role() in roles:
		# a call must be a person's, never the integration user's own wider view
		frappe.throw(
			_("{0} is an integration user, not a person.").format(acting_user), frappe.PermissionError
		)
	return user.name


@contextmanager
def acting_as(acting_user: str | None):
	"""Run the block as the acting user, then give the session back to the caller."""
	if not erpnext_installed():
		frappe.throw(_("Leads need ERPNext on this site."))
	caller = frappe.session.user
	user = resolve_acting_user(acting_user)
	if user == caller:
		yield user
		return
	frappe.set_user(user)  # the integration user acts for a hub user checked above - nosemgrep
	try:
		yield user
	finally:
		frappe.set_user(caller)  # back to the integration user - nosemgrep


# ---------- options ----------


@frappe.whitelist()
def get_options(acting_user: str | None = None) -> dict:
	"""Pick lists for the lead page: stages (in pipeline order), sources, segments, owners (the
	sales users), territories and industries."""
	with acting_as(acting_user):
		return {
			"stages": stage_options(),
			"sources": frappe.get_all("Lead Source", pluck="name", order_by="name asc"),
			"segments": list(SEGMENTS),
			"owners": [{"email": row.user, "full_name": row.full_name} for row in get_crm_users(None)],
			"territories": frappe.get_all("Territory", pluck="name", order_by="name asc"),
			"industries": frappe.get_all("Industry Type", pluck="name", order_by="name asc"),
		}


# ---------- the list ----------


@frappe.whitelist()
def list_leads(
	search: str | None = None,
	stage: str | None = None,
	owner: str | None = None,
	segment: str | None = None,
	view: str = "all",
	start: int = 0,
	page_length: int = 20,
	source: str | None = None,
	temperature: str | None = None,
	metric: str | None = None,
	period: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""The leads the acting user may see, newest change first: {rows, total}. `view`: all, mine
	(they own it), call_today (their Call today list, in its order) or follow_up (an open
	follow-up due by today, earliest first). `metric` (with `period`, or `from_date` and
	`to_date`) keeps the leads one number of the overview counts (get_overview)."""
	if view not in VIEWS:
		frappe.throw(_("view must be one of {0}.").format(", ".join(VIEWS)))
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length) or 20, 1), MAX_PAGE_LENGTH)
	with acting_as(acting_user) as user:
		filters = lead_filters(stage, owner, segment)
		if source:
			filters["source"] = source
		if temperature:
			if temperature not in TEMPERATURES:
				frappe.throw(_("temperature must be one of {0}.").format(", ".join(TEMPERATURES)))
			filters["tbo_temperature"] = temperature
		if metric:
			# dates without a period are a custom range
			period = period or ("custom" if (from_date or to_date) else "month")
			board, _allowed = overview_board(period, from_date, to_date, owner)
			filters["name"] = ("in", board.metric_leads(metric) or [""])
		or_filters = search_filters(search)
		order_by = "modified desc"
		if view == "mine":
			filters["lead_owner"] = user
		elif view == "call_today":
			names = call_today_names(user)
			filters["name"] = ("in", within(filters, names) or [""])
		elif view == "follow_up":
			due = due_follow_ups()
			filters["name"] = ("in", within(filters, list(due)) or [""])
		rows = frappe.get_list(
			"Lead",
			filters=filters,
			or_filters=or_filters,
			fields=ROW_FIELDS,
			order_by=order_by,
			limit_page_length=0 if view in ("call_today", "follow_up") else page_length,
			limit_start=0 if view in ("call_today", "follow_up") else start,
		)
		if view == "call_today":
			order = {name: index for index, name in enumerate(names)}
			rows.sort(key=lambda row: order[row.name])
		elif view == "follow_up":
			rows.sort(key=lambda row: (due[row.name], row.name))
		total = len(rows) if view in ("call_today", "follow_up") else count_leads(filters, or_filters)
		if view in ("call_today", "follow_up"):
			rows = rows[start : start + page_length]
		return {"rows": serialize_rows(rows), "total": total}


def within(filters: dict, names: list[str]) -> list[str]:
	"""`names` kept to the leads a metric already picked, in their order."""
	if "name" not in filters:
		return names
	picked = set(filters["name"][1])
	return [name for name in names if name in picked]


def lead_filters(stage: str | None, owner: str | None, segment: str | None) -> dict:
	filters = {}
	if stage:
		if stage not in STATUS_NAMES:
			frappe.throw(_("{0} is not a lead stage.").format(stage))
		filters["tbo_stage"] = stage
	if owner:
		filters["lead_owner"] = owner
	if segment:
		if segment not in SEGMENTS:
			frappe.throw(_("{0} is not a segment.").format(segment))
		filters["tbo_segment"] = segment
	return filters


def search_filters(search: str | None) -> dict | None:
	search = (search or "").strip()
	if not search:
		return None
	return {
		field: ("like", f"%{search}%") for field in ("lead_name", "company_name", "email_id", "mobile_no")
	}


def count_leads(filters: dict, or_filters: dict | None) -> int:
	return cint(
		frappe.get_list("Lead", filters=filters, or_filters=or_filters, fields=["count(name) as total"])[
			0
		].total
	)


def call_today_names(user: str) -> list[str]:
	"""The user's Call today list, in its order; empty while lead scoring is off."""
	if not scoring_enabled():
		return []
	result = build_call_today(user, size=call_today_size())
	return [row["id"] for row in result["data"]]


def due_follow_ups() -> dict:
	"""{lead: its earliest open follow-up's day} for follow-ups due by today."""
	ToDo = frappe.qb.DocType("ToDo")
	return dict(
		(
			frappe.qb.from_(ToDo)
			.select(ToDo.reference_name, Min(ToDo.date))
			.where(
				(ToDo.reference_type == "Lead")
				& (ToDo.status == OPEN)
				& ToDo.date.isnotnull()
				& (ToDo.date <= frappe.utils.today())
			)
			.groupby(ToDo.reference_name)
		).run()
	)


def serialize_rows(rows: list) -> list[dict]:
	names = [row.name for row in rows]
	follow_ups = next_follow_ups("Lead", names)
	owners = get_full_names({row.lead_owner for row in rows if row.lead_owner})
	return [
		{
			"name": row.name,
			"lead_name": row.lead_name,
			"company_name": row.company_name,
			"email_id": row.email_id,
			"mobile_no": row.mobile_no,
			"tbo_stage": row.tbo_stage,
			"segment": row.segment,
			"lead_owner": row.lead_owner,
			"owner_name": owners.get(row.lead_owner),
			"score": cint(row.score),
			"next_follow_up": follow_ups.get(row.name),
			"modified": row.modified,
		}
		for row in rows
	]


# ---------- the overview and the sales scoreboard ----------


def overview_board(period: str, from_date, to_date, owner: str | None) -> tuple[LeadOverview, list | None]:
	"""(the overview's figures, the scope's owners): the session user's sales scope (their own
	leads, their team's, or everyone's: None), narrowed to `owner` when that person is in it."""
	allowed = scope_owners()
	owners = allowed
	if owner:
		if allowed is not None and owner not in allowed:
			frappe.throw(_("{0} is not in your team.").format(owner), frappe.PermissionError)
		owners = [owner]
	start, end = period_range(period, from_date, to_date)
	return LeadOverview(start, end, owners), allowed


@frappe.whitelist()
def get_overview(
	period: str = "month",
	from_date: str | None = None,
	to_date: str | None = None,
	owner: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""The Leads overview for the acting user's sales scope: totals, the pipeline by stage with
	its value, the funnel, lost reasons, hot leads, and figures by owner, segment and source.
	`period`: week, month, quarter or custom (with `from_date` and `to_date`)."""
	with acting_as(acting_user):
		board, allowed = overview_board(period, from_date, to_date, owner)
		result = board.build_overview()
		result["period"] = period
		result["scope"] = (
			"all" if allowed is None else ("own" if allowed == [frappe.session.user] else "team")
		)
		result["people"] = people(allowed)
		return result


@frappe.whitelist()
def get_sales_activity(
	from_date: str | None = None, to_date: str | None = None, acting_user: str | None = None
) -> dict:
	"""Each salesperson's CRM activity from `from_date` to `to_date` (counts and totals only),
	for the hub's Sales scoreboard. The hub's integration user may ask without `acting_user`:
	the Scoreboard is open to the whole team and closes its periods at night with nobody signed
	in. Anyone else gets their own sales scope."""
	if not (acting_user or "").strip() and integration_role() in frappe.get_roles():
		if not erpnext_installed():
			frappe.throw(_("Leads need ERPNext on this site."))
		return SalesActivity(from_date, to_date).build_activity()
	with acting_as(acting_user):
		return SalesActivity(from_date, to_date, scope_owners()).build_activity()


# ---------- one lead ----------


@frappe.whitelist()
def get_lead(name: str, acting_user: str | None = None) -> dict:
	"""{lead, timeline, quotations, allowed_stages}: the lead's fields, everything that happened
	on it newest first (notes, calls and logged activity, meetings, stage changes, quotations),
	its quotations, and the stages the acting user may move it to."""
	with acting_as(acting_user):
		return lead_payload(name)


def lead_payload(name: str) -> dict:
	frappe.has_permission("Lead", "read", name, throw=True)
	lead = frappe.get_all("Lead", filters={"name": name}, fields=LEAD_DETAIL_FIELDS)[0]
	lead.segment = lead.pop("tbo_segment")
	lead.owner_name = frappe.utils.get_fullname(lead.lead_owner) if lead.lead_owner else None
	lead.deal = lead_opportunity(name)
	lead.next_follow_up = next_follow_ups("Lead", [name]).get(name)
	lead.can_write = bool(frappe.has_permission("Lead", "write", name))  # a flag for the screen - nosemgrep
	quotations = lead_quotations(name, QUOTATION_FIELDS)
	return {
		"lead": lead,
		"timeline": timeline(name, quotations),
		"quotations": [{key: row.get(key) for key in QUOTATION_FIELDS} for row in quotations],
		"allowed_stages": [stage for stage in STATUS_NAMES if stage != lead.tbo_stage]
		if lead.can_write
		else [],
	}


def timeline(name: str, quotations: list) -> list[dict]:
	"""Notes, calls and logged activity, meetings, stage changes and quotations, newest first."""
	items = []
	for note in notes_of("Lead", [name]).get(name, []):
		items.append(
			{
				"kind": "note",
				"at": note.creation,
				"who": note.owner,
				"text": plain_text(note.content),
				"ref": note.name,
			}
		)
	query, Event, _participant = events_of("Lead", [name])
	for event in query.select(
		Event.name, Event.event_category, Event.subject, Event.description, Event.starts_on, Event.owner
	).run(as_dict=True):
		kind = "call" if event.event_category == "Call" else "activity"
		text = plain_text(event.description if kind == "call" else event.subject) or plain_text(event.subject)
		items.append(
			{"kind": kind, "at": event.starts_on, "who": event.owner, "text": text, "ref": event.name}
		)
	for meeting in frappe.get_all(
		MEETING,
		filters={"lead": name},
		fields=["name", "stage_label", "status", "scheduled_at", "bde", "discussion_points"],
	):
		items.append(
			{
				"kind": "meeting",
				"at": meeting.scheduled_at,
				"who": meeting.bde,
				"text": f"{meeting.stage_label}: {meeting.status}",
				"ref": meeting.name,
			}
		)
	for change in frappe.get_all(
		"TBO Stage Change",
		filters={"parenttype": "Lead", "parent": name},
		fields=["stage", "previous_stage", "changed_on", "changed_by", "name"],
	):
		items.append(
			{
				"kind": "stage",
				"at": change.changed_on,
				"who": change.changed_by,
				"text": f"{change.previous_stage} → {change.stage}"
				if change.previous_stage
				else change.stage,
				"ref": change.name,
			}
		)
	for quotation in quotations:
		items.append(
			{
				"kind": "quotation",
				"at": quotation.transaction_date,
				"who": None,
				"text": f"{quotation.name}: {quotation.tbo_quote_status or ''}".strip(": "),
				"ref": quotation.name,
			}
		)
	names = get_full_names({item["who"] for item in items if item["who"]})
	for item in items:
		item["who_name"] = names.get(item["who"]) if item["who"] else None
	items.sort(
		key=lambda item: get_datetime(item["at"]) if item["at"] else get_datetime("1900-01-01"), reverse=True
	)
	return items


# ---------- writes ----------


def parse_data(data) -> dict:
	data = frappe.parse_json(data) if isinstance(data, str) else data
	if not isinstance(data, dict):
		frappe.throw(_("data must be an object."))
	return data


def lead_values(data: dict) -> dict:
	"""The fields the hub sent, under the Lead's names; unknown keys are refused."""
	unknown = sorted(set(data) - set(LEAD_FIELDS) - {"lead_name", "note", "next_follow_up"})
	if unknown:
		frappe.throw(_("Unknown fields: {0}").format(", ".join(unknown)))
	values = {LEAD_FIELDS[key]: value for key, value in data.items() if key in LEAD_FIELDS}
	if data.get("lead_name") and not (values.get("first_name") or values.get("last_name")):
		first, _space, last = str(data["lead_name"]).strip().partition(" ")
		values.update({"first_name": first, "last_name": last})
	if values.get("tbo_segment") and values["tbo_segment"] not in SEGMENTS:
		frappe.throw(_("{0} is not a segment.").format(values["tbo_segment"]))
	return values


def schedule_follow_up(lead, due) -> None:
	"""A follow-up for the lead's owner on `due`, unless one is already open for that day."""
	if not due:
		return
	if next_follow_ups("Lead", [lead.name]).get(lead.name) == getdate(due):
		return
	add_follow_up_task(
		"Lead", lead.name, due, assigned_to=lead.lead_owner or frappe.session.user, task_type=FOLLOW_UP_TYPE
	)


def check_owner(owner: str | None) -> None:
	"""A lead may be given to someone else only as the app's Assign to allows (their team)."""
	if owner and owner != frappe.session.user:
		check_assignee(get_capabilities(), owner)


@frappe.whitelist(methods=["POST"])
def create_lead(data: dict | str, acting_user: str | None = None) -> dict:
	"""A new lead (owner: the acting user unless `lead_owner` names someone they may assign to),
	with `note` as its first note. Returns it as get_lead does."""
	with acting_as(acting_user) as user:
		data = parse_data(data)
		values = lead_values(data)
		note = data.get("note")
		if not (values.get("first_name") or values.get("company_name")):
			frappe.throw(_("Give the lead a name or a company."))
		check_owner(values.get("lead_owner"))
		lead = frappe.new_doc("Lead")
		lead.update({"lead_owner": user, **values})
		lead.insert()
		schedule_follow_up(lead, data.get("next_follow_up"))
		if (note or "").strip():
			add_comment_note("Lead", lead.name, note)
		return lead_payload(lead.name)


@frappe.whitelist(methods=["POST"])
def update_lead(name: str, data: dict | str, acting_user: str | None = None) -> dict:
	"""Change the lead's fields (not its stage: set_stage). Needs write on the lead."""
	with acting_as(acting_user):
		data = parse_data(data)
		if "note" in data:
			frappe.throw(_("Add notes with add_note."))
		values = lead_values(data)
		lead = frappe.get_doc("Lead", name)
		lead.check_permission("write")
		if "lead_owner" in values and values["lead_owner"] != lead.lead_owner:
			check_owner(values["lead_owner"])
		lead.update(values)
		lead.save()
		schedule_follow_up(lead, data.get("next_follow_up"))
		return lead_payload(name)


@frappe.whitelist(methods=["POST"])
def set_stage(name: str, stage: str, note: str | None = None, acting_user: str | None = None) -> dict:
	"""Move the lead to `stage`. Closed – Lost needs `note`, its lost reason (a lost reason's name,
	or free text, saved as "Other" with the text); for any other stage `note` is added as a
	note."""
	with acting_as(acting_user):
		if status_type(stage) == "Lost":
			set_lead_stage(name, stage, note)
		else:
			set_lead_stage(name, stage)
			if (note or "").strip():
				add_comment_note("Lead", name, note)
		return lead_payload(name)


@frappe.whitelist(methods=["POST"])
def add_note(name: str, note: str, acting_user: str | None = None) -> dict:
	"""A note on the lead by the acting user (who may read it). Returns the timeline entry."""
	with acting_as(acting_user) as user:
		if not (note or "").strip():
			frappe.throw(_("Write the note first."))
		frappe.has_permission("Lead", "read", name, throw=True)
		comment = add_comment_note("Lead", name, note)
		return {
			"kind": "note",
			"at": comment.creation,
			"who": user,
			"who_name": frappe.utils.get_fullname(user),
			"text": plain_text(comment.content),
			"ref": comment.name,
		}


# ---------- the segment rate card (docs/lead-pipeline.md §9.8) ----------


@frappe.whitelist()
def get_rate_card(acting_user: str | None = None) -> dict:
	"""Each segment's price list and discount band, and every service's rates; `can_edit` says
	whether the acting user may change them (the roles that edit presets and prices)."""
	with acting_as(acting_user):
		require_quotations()
		return rate_card.get_rate_card()


@frappe.whitelist(methods=["POST"])
def save_rate_card(rows: list | str, acting_user: str | None = None) -> dict:
	"""Replace the segments' price lists and bands; returns the rate card."""
	with acting_as(acting_user):
		require_quotations()
		rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
		return rate_card.save_rate_card(rows)


@frappe.whitelist(methods=["POST"])
def set_segment_rate(
	item_code: str, rate: float | str, segment: str | None = None, acting_user: str | None = None
) -> dict:
	"""A service's rate on the segment's own price list (no segment: the selling price list)."""
	with acting_as(acting_user):
		require_quotations()
		return {
			"item_code": item_code,
			"segment": segment or None,
			"rate": rate_card.set_segment_rate(item_code, flt(rate), segment or None),
		}
