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

from tbo_smart import lead_source
from tbo_smart.api.mobile.capabilities import CRM_ROLES, get_capabilities
from tbo_smart.api.mobile.crm import check_assignee, get_crm_users
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.lead_pipeline import lead_place, rate_card, require_quotations, setting
from tbo_smart.lead_pipeline.business import build_business, can_view_business
from tbo_smart.lead_pipeline.client_documents import chain as document_chain
from tbo_smart.lead_pipeline.daily_report import DailyReport, week_start
from tbo_smart.lead_pipeline.dashboard import company_currency, people, scope_owners
from tbo_smart.lead_pipeline.meetings import DOCTYPE as MEETING
from tbo_smart.lead_pipeline.money import ProposalMoney
from tbo_smart.lead_pipeline.overview import LeadOverview, SalesActivity, period_range
from tbo_smart.lead_pipeline.proposal_media import decode
from tbo_smart.lead_pipeline.proposals import latest_proposals, lead_proposals, proposals_enabled
from tbo_smart.lead_pipeline.quotations import latest_quotations, lead_quotations
from tbo_smart.lead_pipeline.setup import HUB_ROLE, MANAGEMENT_ROLE, SEGMENTS
from tbo_smart.lead_pipeline.statuses import STATUS_NAMES, stage_options, status_type
from tbo_smart.sales import departments, duplicates, erpnext_installed, stale
from tbo_smart.sales.activities import (
	OPEN,
	add_follow_up_task,
	add_log,
	events_of,
	next_follow_ups,
	notes_of,
	plain_text,
)
from tbo_smart.sales.activities import add_note as add_comment_note
from tbo_smart.sales.assignment import is_round_robin
from tbo_smart.sales.call_batches import outcomes as call_outcomes
from tbo_smart.sales.call_batches import reads as call_reads
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.sales.stages import set_lead_stage
from tbo_smart.smart_leads import (
	FOLLOW_UP_TYPE,
	ai_leads_enabled,
	autopilot,
	call_today_size,
	notes_enabled,
	scoring_enabled,
)
from tbo_smart.smart_leads.call_today import build_call_today
from tbo_smart.telephony import MANUAL_OUTCOMES
from tbo_smart.telephony.calls import call_details, can_call, log_manual_call

VIEWS = ("all", "mine", "call_today", "follow_up")
TEMPERATURES = ("Hot", "Warm", "Cold")
SALES_USER = "Sales User"
MAX_PAGE_LENGTH = 100
MAX_INTAKE_DAYS = 62
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
	"department": "tbo_department",
	"tbo_department": "tbo_department",
	"lead_type": "tbo_lead_type",
	"tbo_lead_type": "tbo_lead_type",
	"google_profile_url": "tbo_google_profile_url",
	"maps_url": "tbo_maps_url",
	"latitude": "tbo_latitude",
	"longitude": "tbo_longitude",
	"location_note": "tbo_location_note",
}
ROW_FIELDS = [
	"name",
	"lead_name",
	"company_name",
	"email_id",
	"mobile_no",
	"tbo_stage",
	"tbo_segment as segment",
	"tbo_department",
	"tbo_lead_type",
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
	"tbo_department",
	"tbo_lead_type",
	"tbo_stage",
	"status",
	"lead_owner",
	"tbo_score",
	"tbo_temperature",
	"tbo_score_reason",
	"tbo_lost_reason",
	"tbo_lost_notes",
	"tbo_google_profile_url",
	"tbo_maps_url",
	"tbo_latitude",
	"tbo_longitude",
	"tbo_location_note",
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
	"tbo_proposal",
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
	if not roles & (CRM_ROLES | {MANAGEMENT_ROLE}):
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
	sales users), territories, industries, `departments` (the active lead departments:
	{name, lead_type, is_active, is_head, is_member} for the acting user) and `lead_types`."""
	with acting_as(acting_user):
		return {
			"stages": stage_options(),
			"sources": lead_source.names(),
			"segments": list(SEGMENTS),
			"departments": departments.active_departments_for(frappe.session.user),
			"lead_types": list(departments.LEAD_TYPES),
			"owners": [{"email": row.user, "full_name": row.full_name} for row in get_crm_users(None)],
			"territories": frappe.get_all("Territory", pluck="name", order_by="name asc"),
			"industries": frappe.get_all("Industry Type", pluck="name", order_by="name asc"),
			# MyOperator click-to-call for the acting user (docs/telephony-myoperator.md)
			"can_call": can_call(),
			# the Business overview is for System Manager and the TBO Management role only
			"can_view_business": can_view_business(),
		}


# ---------- the list ----------


@frappe.whitelist()
def list_leads(
	search: str | None = None,
	stage: str | None = None,
	owner: str | None = None,
	segment: str | None = None,
	department: str | None = None,
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
	`to_date`) keeps the leads one number of the overview counts (get_overview). `department`
	keeps one lead department's leads (the acting user still sees only what they may)."""
	if view not in VIEWS:
		frappe.throw(_("view must be one of {0}.").format(", ".join(VIEWS)))
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length) or 20, 1), MAX_PAGE_LENGTH)
	with acting_as(acting_user) as user:
		filters = lead_filters(stage, owner, segment)
		if source:
			filters[lead_source.field()] = source
		if department:
			filters["tbo_department"] = department
		if temperature:
			if temperature not in TEMPERATURES:
				frappe.throw(_("temperature must be one of {0}.").format(", ".join(TEMPERATURES)))
			filters["tbo_temperature"] = temperature
		if metric:
			board, _allowed = overview_board(
				resolve_period(period, from_date, to_date), from_date, to_date, owner
			)
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
	# where each lead stands with the client, for the list row: two queries for the page
	quotations = latest_quotations(names)
	proposals = latest_proposals(names) if proposals_enabled() else {}
	return [
		{
			"name": row.name,
			"lead_name": row.lead_name,
			"company_name": row.company_name,
			"email_id": row.email_id,
			"mobile_no": row.mobile_no,
			"tbo_stage": row.tbo_stage,
			"segment": row.segment,
			"tbo_department": row.tbo_department,
			"tbo_lead_type": row.tbo_lead_type,
			"lead_owner": row.lead_owner,
			"owner_name": owners.get(row.lead_owner),
			"score": cint(row.score),
			"next_follow_up": follow_ups.get(row.name),
			"modified": row.modified,
			"latest_quotation": quotations.get(row.name),
			"latest_proposal": proposals.get(row.name),
		}
		for row in rows
	]


# ---------- the overview and the sales scoreboard ----------


def resolve_period(period: str | None, from_date, to_date) -> str:
	"""Dates without a period are a custom range; neither is this month."""
	return period or ("custom" if (from_date or to_date) else "month")


def overview_board(
	period: str, from_date, to_date, owner: str | None, board: type[LeadOverview] = LeadOverview
) -> tuple[LeadOverview, list | None]:
	"""(the overview's figures, the scope's owners): the session user's sales scope (their own
	leads, their team's, or everyone's: None), narrowed to `owner` when that person is in it.
	`board`: LeadOverview or a figure set built on it (ProposalMoney)."""
	allowed = scope_owners()
	owners = allowed
	if owner:
		if allowed is not None and owner not in allowed:
			frappe.throw(_("{0} is not in your team.").format(owner), frappe.PermissionError)
		owners = [owner]
	start, end = period_range(period, from_date, to_date)
	return board(start, end, owners), allowed


@frappe.whitelist()
def get_overview(
	period: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	owner: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""The Leads overview for the acting user's sales scope: totals, the pipeline by stage with
	its value, the funnel, lost reasons, hot leads, and figures by owner, segment and source.
	`period`: week, month, quarter or custom (with `from_date` and `to_date`; dates alone are
	custom too)."""
	with acting_as(acting_user):
		period = resolve_period(period, from_date, to_date)
		board, allowed = overview_board(period, from_date, to_date, owner)
		result = board.build_overview()
		result["period"] = period
		result["scope"] = (
			"all" if allowed is None else ("own" if allowed == [frappe.session.user] else "team")
		)
		result["people"] = people(allowed)
		# the AI's part: the plan of the person picked (else the acting user's own)
		result["ai"] = autopilot.overview_ai(owners_of(owner, allowed), owner or frappe.session.user)
		return result


@frappe.whitelist()
def get_lead_intake(
	from_date: str | None = None,
	to_date: str | None = None,
	owner: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""New leads taken each day from `from_date` to `to_date` (default today, at most 62 days),
	and by whom, for the acting user's sales scope (narrowed to `owner`). Leads copied in from
	the old CRM (`crm_lead_ref` set) are left out: they weren't taken that day. `people` is the
	scope's salespeople (Sales User), so someone who took none still shows."""
	with acting_as(acting_user):
		day = frappe.utils.today()
		start, end = getdate(from_date or day), getdate(to_date or day)
		if (end - start).days >= MAX_INTAKE_DAYS:
			frappe.throw(_("Choose at most {0} days.").format(MAX_INTAKE_DAYS))
		board, allowed = overview_board("custom", start, end, owner)
		result = board.build_intake()
		result["people"] = [
			{"user": row.user, "full_name": row.full_name}
			for row in get_crm_users([owner] if owner else allowed, roles=(SALES_USER,))
		]
		return result


@frappe.whitelist()
def get_daily_report(
	date: str | None = None, owner: str | None = None, acting_user: str | None = None
) -> dict:
	"""The Daily Sales Performance Report for `date` (default today, site time): one row per
	salesperson in the acting user's sales scope, or only `owner` (who must be in it). Counts, and
	the leads and quotations behind the lists; the hub holds the targets and compares.

	Per person, for the day: `calls_total` (TBO Calls the person made or took, answered or not),
	`calls_connected` (answered ones); `fresh_leads` (distinct leads whose first contact, a first
	call or a first move out of New Lead, was that day, by this person) and `follow_up_leads`
	(distinct leads they contacted that day that had been contacted before: a call, a stage move
	or a completed follow-up), the two never overlapping; `positive_leads` (leads that entered
	Positive / Interested that day, listed); `new_quotations` (quotations sent that day, every
	revision, plus proposals sent with no quotation behind them: count, value and items);
	`meetings_held` (meetings scheduled that day that have ended, not cancelled), `follow_ups_done`
	(completed that day), `follow_ups_overdue` (open and past their day now); `new_leads` (created
	that day with the person as owner, old-CRM imports left out, listed). `week_to_date` is
	Monday to `date`: `leads` (new, as above), `meetings`, `proposals` (as `new_quotations`) and
	`follow_ups` (done)."""
	with acting_as(acting_user):
		allowed = scope_owners()
		if owner and allowed is not None and owner not in allowed:
			frappe.throw(_("{0} is not in your team.").format(owner), frappe.PermissionError)
		day = getdate(date or frappe.utils.today())
		users = get_crm_users([owner] if owner else allowed, roles=CRM_ROLES if owner else (SALES_USER,))
		names = [row.user for row in users]
		owners = [owner] if owner else allowed
		report = DailyReport(day, day, owners)
		figures = report.build_day(names)
		week = DailyReport(week_start(day), day, owners).build_week(names)
		return {
			"date": day,
			"week_from": week_start(day),
			"currency": company_currency(),
			"people": [
				{
					"user": row.user,
					"full_name": row.full_name,
					**figures[row.user],
					"week_to_date": week[row.user],
				}
				for row in users
			],
		}


@frappe.whitelist(methods=["POST"])
def log_call(lead: str, outcome: str, note: str | None = None, acting_user: str | None = None) -> dict:
	"""Log a call the acting user made outside MyOperator on `lead`: `outcome` is Connected, No
	answer, Busy or Voicemail. It is stored as an outgoing TBO Call by them, the single source of
	calls, and shows on the lead's timeline. Needs write permission on the lead."""
	with acting_as(acting_user):
		call = log_manual_call(lead, outcome, note)
		return {"name": call.name, "status": call.status, "outcomes": list(MANUAL_OUTCOMES)}


@frappe.whitelist()
def get_proposal_money(
	period: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	owner: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""Proposals and the money after them for the acting user's sales scope (narrowed to
	`owner`): in the period, the proposals sent and their value, the value won and the advances
	received; now, every payment split still to come in, with its status, and their total by
	month (docs/hub-leads-api.md §2.8). `period` as get_overview."""
	with acting_as(acting_user):
		period = resolve_period(period, from_date, to_date)
		board, _allowed = overview_board(period, from_date, to_date, owner, ProposalMoney)
		result = board.build_money()
		result["period"] = period
		return result


@frappe.whitelist()
def get_business_overview(
	from_date: str | None = None,
	to_date: str | None = None,
	department: str | None = None,
	include_profit: int | str | bool = 0,
	acting_user: str | None = None,
) -> dict:
	"""The management-only Business overview, company-wide: the pipeline, proposals given,
	onboardings, revenue (invoiced, collected, outstanding), the six-month trend, top clients and
	figures by department (docs/hub-leads-api.md §2.12). Only System Manager and the TBO
	Management role; anyone else is refused whatever their sales scope. `include_profit` adds
	income minus expense from the ledger."""
	with acting_as(acting_user):
		return build_business(from_date, to_date, department, bool(cint(include_profit)))


def owners_of(owner: str | None, allowed: list | None) -> list | None:
	return [owner] if owner else allowed


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
	lead = frappe.get_all("Lead", filters={"name": name}, fields=lead_source.fields(LEAD_DETAIL_FIELDS))[0]
	lead.segment = lead.pop("tbo_segment")
	lead.place = lead_place.place_of(lead)
	for key in (
		"tbo_google_profile_url",
		"tbo_maps_url",
		"tbo_latitude",
		"tbo_longitude",
		"tbo_location_note",
	):
		lead.pop(key)
	lead.photo_count = lead_place.photo_count(name)
	lead.owner_name = frappe.utils.get_fullname(lead.lead_owner) if lead.lead_owner else None
	lead.deal = lead_opportunity(name)
	lead.next_follow_up = next_follow_ups("Lead", [name]).get(name)
	lead.can_write = bool(frappe.has_permission("Lead", "write", name))  # a flag for the screen - nosemgrep
	lead.can_call = can_call()
	quotations = lead_quotations(name, QUOTATION_FIELDS)
	proposals = lead_proposals(name) if proposals_enabled() else []
	return {
		"lead": lead,
		"timeline": timeline(name, quotations, proposals),
		# staff only: the internal ERPNext Quotation, its desk link and the proposal it prices
		"quotations": [
			{
				**{key: row.get(key) for key in QUOTATION_FIELDS},
				"url": frappe.utils.get_url_to_form("Quotation", row.name),
			}
			for row in quotations
		],
		"proposals": proposals,
		"documents": document_chain(name) if proposals_enabled() else None,
		"allowed_stages": [stage for stage in STATUS_NAMES if stage != lead.tbo_stage]
		if lead.can_write
		else [],
	}


def timeline(name: str, quotations: list, proposals: list) -> list[dict]:
	"""Notes, calls and logged activity, meetings, stage changes, quotations and proposals,
	newest first."""
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
	events = query.select(
		Event.name, Event.event_category, Event.subject, Event.description, Event.starts_on, Event.owner
	).run(as_dict=True)
	# a call through MyOperator: its length, direction, status, recording and AI summary
	telephony = call_details([event.name for event in events if event.event_category == "Call"])
	for event in events:
		kind = "call" if event.event_category == "Call" else "activity"
		text = plain_text(event.description if kind == "call" else event.subject) or plain_text(event.subject)
		items.append(
			{
				"kind": kind,
				"at": event.starts_on,
				"who": event.owner,
				"text": text,
				"ref": event.name,
				**telephony.get(event.name, {}),
			}
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
	for note in ai_notes(name):
		items.append(
			{
				"kind": "ai",
				"at": note.creation,
				"who": None,
				"text": plain_text(note.content),
				"ref": note.name,
			}
		)
	for proposal in proposals:
		items.append(
			{
				"kind": "proposal",
				"at": proposal.decided_on or proposal.last_viewed_on or proposal.sent_on or proposal.modified,
				"who": None,
				"text": f"{proposal.title}: {proposal.status}",
				"ref": proposal.name,
			}
		)
	names = get_full_names({item["who"] for item in items if item["who"]})
	for item in items:
		item["who_name"] = (
			names.get(item["who"]) if item["who"] else (autopilot.AI_AUTHOR if item["kind"] == "ai" else None)
		)
	items.sort(
		key=lambda item: get_datetime(item["at"]) if item["at"] else get_datetime("1900-01-01"), reverse=True
	)
	return items


def ai_notes(name: str) -> list:
	"""What the AI did on the lead (autopilot.log_on_timeline), newest first."""
	return frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Lead",
			"reference_name": name,
			"comment_type": "Info",
			"comment_by": autopilot.AI_AUTHOR,
		},
		fields=["name", "content", "creation"],
		order_by="creation desc",
	)


# ---------- AI runs leads (docs/smart-leads.md §11) ----------


def require_ai_leads() -> None:
	if not ai_leads_enabled():
		frappe.throw(_("AI runs leads is switched off in TBO Smart Settings."))


@frappe.whitelist()
def get_lead_ai(name: str, acting_user: str | None = None) -> dict:
	"""The lead's "AI suggests": summary, sentiment, intent, next best action, a stage to
	suggest and a drafted message (the stored insight), its health and likely duplicates."""
	with acting_as(acting_user):
		return autopilot.lead_ai(name)


@frappe.whitelist(methods=["POST"])
def refresh_lead_ai(name: str, acting_user: str | None = None) -> dict:
	"""Ask the AI to read the lead again now (write on the lead, once a minute)."""
	with acting_as(acting_user):
		require_ai_leads()
		autopilot.refresh_now(name)
		return autopilot.lead_ai(name)


@frappe.whitelist(methods=["POST"])
def apply_ai_stage(name: str, acting_user: str | None = None) -> dict:
	"""Move the lead to the stage the AI suggested, as the acting user (never Won or Lost).
	Returns the lead as get_lead does."""
	with acting_as(acting_user):
		require_ai_leads()
		autopilot.apply_stage(name)
		return lead_payload(name)


@frappe.whitelist(methods=["POST"])
def dismiss_ai_stage(name: str, acting_user: str | None = None) -> dict:
	"""Drop the AI's stage suggestion."""
	with acting_as(acting_user):
		autopilot.clear_stage(name)
		return autopilot.lead_ai(name)


@frappe.whitelist(methods=["POST"])
def draft_lead_message(name: str, channel: str, language: str = "en", acting_user: str | None = None) -> dict:
	"""An email or WhatsApp text for the acting user to edit and send from their own mail or
	WhatsApp: {channel, subject, text, to?, url?}. Nothing is sent."""
	with acting_as(acting_user):
		if not (ai_leads_enabled() or notes_enabled()):
			frappe.throw(_("AI drafts are switched off in TBO Smart Settings."))
		return autopilot.draft_message(name, channel, language)


@frappe.whitelist(methods=["POST"])
def log_message_sent(name: str, channel: str, text: str, acting_user: str | None = None) -> dict:
	"""The acting user sent a message from their own mail or WhatsApp: log it on the lead as
	theirs. Returns the timeline entry."""
	with acting_as(acting_user) as user:
		if channel not in autopilot.MESSAGE_CHANNELS:
			frappe.throw(_("channel must be whatsapp or email."))
		if not (text or "").strip():
			frappe.throw(_("Write the message first."))
		frappe.has_permission("Lead", "read", name, throw=True)
		event = add_log("Lead", name, channel, text)
		return {
			"kind": "activity",
			"at": event.starts_on,
			"who": user,
			"who_name": frappe.utils.get_fullname(user),
			"text": plain_text(event.subject),
			"ref": event.name,
		}


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
	if "source" in values:
		values[lead_source.field()] = values.pop("source")
	if data.get("lead_name") and not (values.get("first_name") or values.get("last_name")):
		first, _space, last = str(data["lead_name"]).strip().partition(" ")
		values.update({"first_name": first, "last_name": last})
	if values.get("tbo_segment") and values["tbo_segment"] not in SEGMENTS:
		frappe.throw(_("{0} is not a segment.").format(values["tbo_segment"]))
	if values.get("tbo_lead_type") and values["tbo_lead_type"] not in departments.LEAD_TYPES:
		frappe.throw(_("{0} is not a lead type.").format(values["tbo_lead_type"]))
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
def create_lead(
	data: dict | str, allow_duplicate: int | str | bool = 0, acting_user: str | None = None
) -> dict:
	"""A new lead (owner: the acting user unless `lead_owner` names someone they may assign to,
	or, with the owner left empty in a Round robin department, its next member), with `note` as
	its first note. A lead whose email or phone another lead has is refused with
	DuplicateEntryError "Possible duplicate of <lead> (<owner>)" (the matches are in the response)
	unless `allow_duplicate` is 1. Returns it as get_lead does."""
	with acting_as(acting_user) as user:
		data = parse_data(data)
		values = lead_values(data)
		note = data.get("note")
		if not (values.get("first_name") or values.get("company_name")):
			frappe.throw(_("Give the lead a name or a company."))
		check_owner(values.get("lead_owner"))
		if not cint(allow_duplicate):
			duplicates.refuse_duplicate(
				values.get("email_id"),
				values.get("mobile_no") or values.get("phone"),
				values.get("company_name"),
			)
		lead = frappe.new_doc("Lead")
		lead.update({"lead_owner": user, **values})
		# with the owner left empty, a Round robin department gives the lead to its next member
		lead.flags.round_robin = not values.get("lead_owner") and is_round_robin(values.get("tbo_department"))
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


@frappe.whitelist()
def get_lead_photos(lead: str, acting_user: str | None = None) -> list[dict]:
	"""The lead's photos, newest first: [{name, file_name, file_url, file_size, caption,
	uploaded_by, uploaded_by_name, uploaded_on}]. `file_url` is private: show a photo through
	get_lead_photo."""
	with acting_as(acting_user):
		return lead_place.get_photos(lead)


@frappe.whitelist(methods=["POST"])
def upload_lead_photo(
	lead: str, filename: str, content: str, caption: str | None = None, acting_user: str | None = None
) -> dict:
	"""A photo on the lead (`content` base64; PNG, JPEG or WebP, at most 10 MB; at most 20 per
	lead). Needs write on the lead. Returns the photo as get_lead_photos lists it."""
	with acting_as(acting_user):
		return lead_place.add_photo(lead, filename, decode(content), caption)


@frappe.whitelist(methods=["POST"])
def delete_lead_photo(lead: str, file: str, acting_user: str | None = None) -> dict:
	"""Delete a photo: who added it, the lead's owner or a manager (with write on the lead)."""
	with acting_as(acting_user):
		lead_place.remove_photo(lead, file)
		return {"deleted": file}


@frappe.whitelist()
def get_lead_photo(lead: str, file: str, thumb: int | str = 0, acting_user: str | None = None) -> None:
	"""The photo's image bytes (`thumb` 1: a JPEG of at most 480 px) to anyone who may read the
	lead; the response is the file, not JSON."""
	with acting_as(acting_user):
		content, mime, name = lead_place.photo_bytes(lead, file, thumb)
	frappe.local.response.filename = name
	frappe.local.response.filecontent = content
	frappe.local.response.content_type = mime
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.type = "download"


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


# ---------- duplicates and stale leads (docs/lead-departments.md §6, §7) ----------


@frappe.whitelist()
def find_duplicate_leads(
	email: str | None = None,
	phone: str | None = None,
	company: str | None = None,
	exclude: str | None = None,
	acting_user: str | None = None,
) -> list[dict]:
	"""Up to 5 leads with this email, phone (last 10 digits) or company (case-insensitive,
	exact): [{name, lead_name, company_name, email_id, mobile_no, lead_owner, owner_name,
	tbo_stage, matched_on}], matched_on being some of "email", "phone", "company". `exclude` is
	a lead to leave out (the one being edited). It searches every lead, not only those the
	acting user may see, and returns only those identifying fields: it exists so nobody works
	an enquiry a colleague already has."""
	with acting_as(acting_user):
		return duplicates.find_duplicates(email, phone, company, exclude)


@frappe.whitelist()
def get_stale_leads(days: int | str = 3, acting_user: str | None = None) -> list[dict]:
	"""The open leads (not Closed – Won, Closed – Lost or On Hold) the acting user may see that
	have had no activity for `days` days, longest quiet first: [{name, lead_name, company_name,
	lead_owner, tbo_department, tbo_stage, last_activity_on, idle_days}]. A department's own
	`stale_after_days`, when set, replaces `days` for its leads."""
	with acting_as(acting_user):
		return stale.stale_leads(max(cint(days), 1))


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


# ---------- lead departments (docs/lead-departments.md) ----------


@frappe.whitelist()
def get_lead_departments(acting_user: str | None = None) -> list[dict]:
	"""Every lead department, inactive ones too, to any lead user: [{name, department_name,
	lead_type, is_active, heads, members}] (heads and members: [{user, full_name}])."""
	with acting_as(acting_user):
		return departments.get_departments()


@frappe.whitelist(methods=["POST"])
def save_lead_department(values: dict | str, name: str | None = None, acting_user: str | None = None) -> dict:
	"""Create (no `name`) or update a lead department. `values`: department_name (new only),
	lead_type, is_active, heads and members (lists of user emails; a list given replaces the
	old one). Sales Manager or System Manager only (PermissionError otherwise). Returns the
	department as get_lead_departments lists it."""
	with acting_as(acting_user):
		return departments.save_department(parse_data(values), name or None)


@frappe.whitelist(methods=["POST"])
def set_lead_department_active(name: str, active: int | str | bool, acting_user: str | None = None) -> dict:
	"""Switch a department on or off (off: no new leads; its heads see nothing more of it).
	Same people as save_lead_department. Returns the department."""
	with acting_as(acting_user):
		return departments.set_active(name, active)


# ---------- today's calls (docs/call-batches.md) ----------


@frappe.whitelist()
def get_my_calls(acting_user: str | None = None) -> dict:
	"""The acting user's call queue: {enabled, reason?, date, batch: {name, slot, department,
	starts_at, ends_at, seconds_left, batches} | null, counts: {size, called, connected, pending,
	scheduled, overdue}, today: {size, called, connected}, pending: [Call], scheduled: [Call],
	overdue: [Call], can_call, next_batch: {allowed, reason}, next_slot: {label, starts_at} | null}.
	Call = {item, batch, slot, lead, priority, reason, is_repeat, retry_at, attempts,
	batch_ends_at, summary: {name, lead_name, company_name, industry, district, phone, stage,
	temperature, last_note, next_step, wrong_number}}. Calls made another way since (MyOperator,
	Log call) are counted first."""
	with acting_as(acting_user):
		return call_reads.get_my_calls()


@frappe.whitelist(methods=["POST"])
def get_next_batch(
	department: str | None = None,
	industry: str | None = None,
	state: str | None = None,
	district: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""Hand the acting user their next batch: {created, reason, batch?, slot?, size?}. Nothing
	is created while calls are pending, overdue calls pass the department's limit, the daily cap
	is reached or no slot is open; `reason` says which. `industry`, `state` and `district` narrow
	the new leads of this batch (blank: the department's campaign filter)."""
	with acting_as(acting_user):
		return call_reads.get_next_batch(department or None, industry, state, district)


@frappe.whitelist(methods=["POST"])
def record_call_outcome(
	batch_item: str,
	outcome: str,
	note: str | None = None,
	callback_at: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""Record the call for one queue item (its `item`): `outcome` is Connected, Not answered, Busy,
	Call back (`callback_at` required), Not interested or Wrong number. The call is logged as
	log_call does; the item leaves the queue. Not answered and Busy come back once, two hours
	later, if the batch is still running; `callback_at` on Connected sets the lead's next
	follow-up. Returns {item, outcome, retry_at}."""
	with acting_as(acting_user):
		return call_outcomes.record(batch_item, outcome, note, callback_at)


@frappe.whitelist()
def get_call_batches_report(date: str | None = None, acting_user: str | None = None) -> dict:
	"""Managers and department heads: the day's batches per person: {date, people: [{user,
	user_name, department, size, called, connected, overdue, batches: [{name, slot, status,
	starts_at, ends_at, size, called, connected, overdue}]}], totals}."""
	with acting_as(acting_user):
		return call_reads.get_call_batches_report(date)
