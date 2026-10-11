"""TBO Smart CRM façade over Frappe CRM: leads, deals, organizations, follow-ups, activity, insights.

Every endpoint needs the `crm` capability. Lists use frappe.get_list, so Frappe CRM's own
rules (roles, the sales hierarchy and shares) decide what each user sees; `scope` narrows
that further within the caller's crm_scopes. Writes are normal inserts and saves under the
session user. Contract: docs/tbo-smart-api-ess-crm.md.
"""

import frappe
from crm.api.activities import get_linked_notes, get_linked_tasks
from crm.api.comment import add_comment
from crm.api.dashboard import TimestampDiff
from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Avg, Coalesce, Count, Date, IfNull, Max, Min, Sum
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime, strip_html
from pypika.terms import EmptyCriterion, LiteralValue

from tbo_smart.api.mobile.capabilities import (
	list_scope_users,
	require,
	resolve_crm_scope,
	scope_users,
)
from tbo_smart.api.mobile.utils import (
	cached_insights,
	get_full_names,
	in_window,
	page_envelope,
	page_window,
	percent,
	period_windows,
	trend_index,
)

REF_DOCTYPES = {"lead": "CRM Lead", "deal": "CRM Deal"}
CLOSED_TASK_STATUSES = ("Done", "Canceled")
ACTIVITY_KINDS = ("call", "note", "meeting", "email", "whatsapp")
COMMENT_LABELS = {"meeting": "Meeting", "email": "Email", "whatsapp": "WhatsApp"}
CLOSED_STAGE_TYPES = ["Won", "Lost"]
STALLED_DAYS = 14
INSIGHT_LIST_LIMIT = 10

LEAD_FIELDS = [
	"name",
	"lead_name",
	"organization",
	"job_title",
	"status",
	"source",
	"lead_owner",
	"mobile_no",
	"phone",
	"email",
	"territory",
	"converted",
	"creation",
]
DEAL_FIELDS = [
	"name",
	"organization",
	"lead_name",
	"first_name",
	"last_name",
	"status",
	"deal_value",
	"expected_deal_value",
	"currency",
	"probability",
	"expected_closure_date",
	"closed_date",
	"deal_owner",
	"lost_reason",
	"lost_notes",
	"next_step",
	"mobile_no",
	"email",
	"creation",
]


# ---------- Options ----------


@frappe.whitelist()
def get_options() -> dict:
	"""The site's lead sources, lead statuses, deal stages and lost reasons, in CRM order."""
	require_crm()
	return {
		"lead_sources": frappe.get_list("CRM Lead Source", pluck="name", order_by="name asc"),
		"lead_statuses": frappe.get_list("CRM Lead Status", fields=["name", "type"], order_by="position asc"),
		"deal_stages": frappe.get_list(
			"CRM Deal Status", fields=["name", "type", "probability"], order_by="position asc"
		),
		"lost_reasons": frappe.get_list("CRM Lost Reason", pluck="name", order_by="name asc"),
	}


# ---------- Leads ----------


@frappe.whitelist()
def get_leads(
	status: str | None = None,
	search: str | None = None,
	include_converted: bool = False,
	page: int = 1,
	page_length: int = 20,
	scope: str | None = None,
) -> dict:
	"""Leads the session user may read in `scope` (mine / team / all; default: widest), newest first."""
	capabilities = require_crm()
	filters = scope_filter(capabilities, scope, "lead_owner")
	if status:
		filters["status"] = status
	if not cint(include_converted):
		filters["converted"] = 0
	or_filters = search_filters(search, ["lead_name", "organization", "email", "mobile_no"])
	start, length = page_window(page, page_length)
	leads = frappe.get_list(
		"CRM Lead",
		fields=LEAD_FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="creation desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_leads(leads), page, page_length)


@frappe.whitelist()
def get_lead(name: str) -> dict:
	"""One lead with its notes, tasks and activity."""
	require_crm()
	frappe.has_permission("CRM Lead", "read", name, throw=True)
	lead = frappe.db.get_value("CRM Lead", name, LEAD_FIELDS, as_dict=True)
	return {**serialize_leads([lead])[0], **get_timeline("CRM Lead", name)}


@frappe.whitelist(methods=["POST"])
def create_lead(
	lead_name: str,
	organization: str | None = None,
	email: str | None = None,
	mobile_no: str | None = None,
	source: str | None = None,
	note: str | None = None,
) -> dict:
	"""Create a lead owned by the session user, with an optional first note."""
	require_crm()
	first_name, _space, last_name = (lead_name or "").strip().partition(" ")
	if not first_name:
		frappe.throw(_("Give the lead a name."))
	lead = frappe.new_doc("CRM Lead")
	lead.update(
		{
			"first_name": first_name,
			"last_name": last_name,
			"organization": organization,
			"email": email,
			"mobile_no": mobile_no,
			"source": source,
			"lead_owner": frappe.session.user,
		}
	)
	lead.insert()
	if note:
		add_note("CRM Lead", lead.name, note)
	return get_lead(lead.name)


@frappe.whitelist(methods=["POST"])
def set_lead_status(name: str, status: str) -> dict:
	require_crm()
	lead = frappe.get_doc("CRM Lead", name)
	lead.check_permission("write")
	lead.status = status
	lead.save()
	return {"name": lead.name, "status": lead.status}


@frappe.whitelist(methods=["POST"])
def convert_lead(name: str) -> dict:
	"""Convert a lead to a deal with Frappe CRM's own conversion; a converted lead returns its deal.

	The existing-deal check is done here rather than with convert_to_deal(if_converted=...),
	which older Frappe CRM releases do not have.
	"""
	require_crm()
	frappe.has_permission("CRM Lead", "write", name, throw=True)
	deal = frappe.db.get_value("CRM Deal", {"lead": name}, "name") or convert_to_deal(lead=name)
	return get_deal(deal)


def serialize_leads(leads: list) -> list[dict]:
	names = [lead.name for lead in leads]
	next_follow_up = get_next_follow_ups("CRM Lead", names)
	last_contact = get_last_calls("CRM Lead", names)
	owners = get_full_names({lead.lead_owner for lead in leads})
	return [
		{
			"name": lead.name,
			"lead_name": lead.lead_name,
			"organization": lead.organization,
			"designation": lead.job_title,
			"status": lead.status,
			"source": lead.source,
			"owner": owners.get(lead.lead_owner, lead.lead_owner),
			"owner_id": lead.lead_owner,
			"phone": lead.mobile_no or lead.phone,
			"email": lead.email,
			"territory": lead.territory,
			"converted": bool(lead.converted),
			"last_contact": last_contact.get(lead.name),
			"next_follow_up": next_follow_up.get(lead.name),
			"created": lead.creation,
		}
		for lead in leads
	]


# ---------- Deals ----------


@frappe.whitelist()
def get_deals(
	stage: str | None = None,
	mine: bool = False,
	search: str | None = None,
	page: int = 1,
	page_length: int = 20,
	scope: str | None = None,
) -> dict:
	"""Deals the session user may read in `scope`, optionally one stage. `mine=1` is scope=mine."""
	capabilities = require_crm()
	filters = scope_filter(capabilities, "mine" if cint(mine) else scope, "deal_owner")
	if stage:
		filters["status"] = stage
	or_filters = search_filters(search, ["organization", "lead_name", "email", "mobile_no"])
	start, length = page_window(page, page_length)
	deals = frappe.get_list(
		"CRM Deal",
		fields=DEAL_FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="modified desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_deals(deals), page, page_length)


@frappe.whitelist()
def get_deal(name: str) -> dict:
	"""One deal with its stage history, tasks, notes, activity and ERPNext customer."""
	require_crm()
	frappe.has_permission("CRM Deal", "read", name, throw=True)
	deal = frappe.get_doc("CRM Deal", name)
	row = frappe._dict({field: deal.get(field) for field in DEAL_FIELDS})
	return {
		**serialize_deals([row])[0],
		"erpnext_customer": deal.get("erpnext_customer"),
		"stage_history": [
			{"stage": log.get("from"), "at": log.from_date}
			for log in deal.status_change_log
			if log.get("from")
		],
		**get_timeline("CRM Deal", name),
	}


@frappe.whitelist(methods=["POST"])
def move_deal_stage(name: str, stage: str, lost_reason: str | None = None) -> dict:
	"""Move a deal to another CRM Deal Status. Lost needs a reason.

	A reason that matches a CRM Lost Reason is used as is; free text is saved as
	lost reason "Other" with the text in lost notes, which is what CRM asks for.
	"""
	require_crm()
	deal = frappe.get_doc("CRM Deal", name)
	deal.check_permission("write")
	stage_type = frappe.db.get_value("CRM Deal Status", stage, "type")
	if not stage_type:
		frappe.throw(_("{0} is not a deal stage.").format(stage))
	deal.status = stage
	if stage_type == "Lost":
		reason = (lost_reason or "").strip()
		if not reason:
			frappe.throw(_("Give a reason for losing the deal."))
		if frappe.db.exists("CRM Lost Reason", reason):
			deal.lost_reason, deal.lost_notes = reason, None
		else:
			deal.lost_reason, deal.lost_notes = "Other", reason
	deal.save()
	return get_deal(deal.name)


def serialize_deals(deals: list) -> list[dict]:
	names = [deal.name for deal in deals]
	stage_types = dict(frappe.get_all("CRM Deal Status", fields=["name", "type"], as_list=True))
	owners = get_full_names({deal.deal_owner for deal in deals})
	next_follow_up = get_next_follow_ups("CRM Deal", names)
	result = []
	for deal in deals:
		contact = deal.lead_name or " ".join(part for part in (deal.first_name, deal.last_name) if part)
		result.append(
			{
				"name": deal.name,
				"title": deal.organization or contact or deal.name,
				"organization": deal.organization,
				"contact": contact,
				"stage": deal.status,
				"stage_type": stage_types.get(deal.status),
				"amount": flt(deal.deal_value or deal.expected_deal_value),
				"currency": deal.currency,
				"probability": flt(deal.probability),
				"expected_close": deal.expected_closure_date,
				"closed_on": deal.closed_date,
				"owner": owners.get(deal.deal_owner, deal.deal_owner),
				"owner_id": deal.deal_owner,
				"lost_reason": deal.lost_notes or deal.lost_reason,
				"next_step": deal.next_step,
				"phone": deal.mobile_no,
				"email": deal.email,
				"next_follow_up": next_follow_up.get(deal.name),
				"created": deal.creation,
			}
		)
	return result


# ---------- Organizations ----------


@frappe.whitelist()
def get_organizations(search: str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""CRM Organizations with open deal counts and the ERPNext Customers CRM linked to their deals."""
	require_crm()
	or_filters = search_filters(search, ["organization_name", "website"])
	start, length = page_window(page, page_length)
	organizations = frappe.get_list(
		"CRM Organization",
		fields=["name", "organization_name", "territory", "industry", "website", "no_of_employees"],
		or_filters=or_filters,
		order_by="organization_name asc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_organizations(organizations), page, page_length)


@frappe.whitelist()
def get_organization(name: str) -> dict:
	"""One organization with its contacts and deals."""
	require_crm()
	frappe.has_permission("CRM Organization", "read", name, throw=True)
	organization = frappe.db.get_value(
		"CRM Organization",
		name,
		["name", "organization_name", "territory", "industry", "website", "no_of_employees"],
		as_dict=True,
	)
	contacts = frappe.get_list(
		"Contact",
		filters={"company_name": name},
		fields=["name", "full_name", "designation", "mobile_no", "phone", "email_id"],
		order_by="full_name asc",
	)
	deals = frappe.get_list(
		"CRM Deal", filters={"organization": name}, fields=DEAL_FIELDS, order_by="modified desc"
	)
	return {
		**serialize_organizations([organization])[0],
		"contacts": [
			{
				"name": contact.full_name or contact.name,
				"contact": contact.name,
				"designation": contact.designation,
				"phone": contact.mobile_no or contact.phone,
				"email": contact.email_id,
			}
			for contact in contacts
		],
		"deals": serialize_deals(deals),
	}


def serialize_organizations(organizations: list) -> list[dict]:
	names = [organization.name for organization in organizations]
	open_deals, customers = get_organization_deal_links(names)
	return [
		{
			"name": organization.name,
			"organization_name": organization.organization_name,
			"territory": organization.territory,
			"industry": organization.industry,
			"website": organization.website,
			"no_of_employees": organization.no_of_employees,
			"open_deals": open_deals.get(organization.name, 0),
			"erpnext_customers": sorted(customers.get(organization.name, [])),
		}
		for organization in organizations
	]


def get_organization_deal_links(names: list[str]) -> tuple[dict, dict]:
	"""Open deal count and ERPNext customers per organization, from the deals the user can read.

	Frappe CRM links a Customer to a Deal (CRM Deal.erpnext_customer, set when the deal
	is won with the ERPNext integration on); there is no direct Organization -> Customer link.
	"""
	if not names:
		return {}, {}
	fields = ["name", "organization", "status"]
	has_customer = frappe.get_meta("CRM Deal").has_field("erpnext_customer")
	if has_customer:
		fields.append("erpnext_customer")
	deals = frappe.get_list("CRM Deal", filters={"organization": ("in", names)}, fields=fields)
	stage_types = dict(frappe.get_all("CRM Deal Status", fields=["name", "type"], as_list=True))
	open_deals, customers = {}, {}
	for deal in deals:
		if stage_types.get(deal.status) not in ("Won", "Lost"):
			open_deals[deal.organization] = open_deals.get(deal.organization, 0) + 1
		if has_customer and deal.erpnext_customer:
			customers.setdefault(deal.organization, set()).add(deal.erpnext_customer)
	return open_deals, customers


# ---------- Sales insights ----------


@frappe.whitelist()
def get_sales_insights(
	period: str = "month", scope: str | None = None, stalled_days: int = STALLED_DAYS
) -> dict:
	"""Read-only pipeline, win/loss, funnel, activity and trend aggregates, cached for a few minutes.

	`scope` (mine / team / all, default the widest in crm_scopes) filters on the deal and
	lead owner, as Frappe CRM's dashboard does. Values are deal_value x exchange_rate in
	the CRM base currency, the dashboard's formula.
	"""
	capabilities = require("sales_insights", _("Only Sales Managers can see sales insights."))
	scope = resolve_crm_scope(capabilities, scope)
	stalled_days = min(max(cint(stalled_days) or STALLED_DAYS, 1), 365)
	windows = period_windows(period)
	return cached_insights(
		"sales", (period, scope, stalled_days), lambda: build_sales_insights(windows, scope, stalled_days)
	)


def build_sales_insights(windows: frappe._dict, scope: str, stalled_days: int) -> dict:
	owners = scope_users(scope)
	closed = get_closed_deals(windows.trend[0].from_date, owners)
	won = summarize_closed(closed, "Won", windows)
	lost = summarize_closed(closed, "Lost", windows)
	win_rate = percent(won["count"], won["count"] + lost["count"])
	previous_win_rate = percent(won["previous_count"], won["previous_count"] + lost["previous_count"])
	return {
		"period": windows.period,
		"from_date": windows.from_date,
		"to_date": windows.to_date,
		"previous": {"from_date": windows.previous_from, "to_date": windows.previous_to},
		"scope": scope,
		"currency": get_crm_currency(),
		"pipeline": get_pipeline(owners),
		"won": won,
		"lost": lost,
		"win_rate": win_rate,
		"win_rate_delta": round(win_rate - previous_win_rate, 1),
		"stalled": get_stalled_deals(owners, stalled_days),
		"leads_funnel": get_leads_funnel(windows, owners),
		"activity": get_activity_by_owner(windows, owners),
		"trend": get_sales_trend(windows, owners, closed),
		"closing": get_closing_deals(windows, owners),
		"generated_at": now_datetime(),
	}


def deal_value(Deal):
	"""Frappe CRM's dashboard value: deal_value in the base currency."""
	return Coalesce(Deal.deal_value, 0) * IfNull(Deal.exchange_rate, 1)


def owned_by(field, owners: list[str] | None):
	"""Owner criterion for a scope; no filter for scope=all."""
	return field.isin(owners) if owners else EmptyCriterion()


def get_crm_currency() -> str | None:
	return frappe.db.get_single_value("FCRM Settings", "currency") or frappe.db.get_default("currency")


def current_stage_log(Log, Deal):
	"""Join to the open CRM Status Change Log row of the deal's current stage (when it entered it)."""
	return (
		(Log.parent == Deal.name)
		& (Log.parenttype == "CRM Deal")
		& (Log["from"] == Deal.status)
		& (IfNull(Log["to"], "") == "")
	)


def get_pipeline(owners: list[str] | None) -> dict:
	"""Open stages in CRM order: count, value, weighted value, average age and days in stage."""
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	Log = frappe.qb.DocType("CRM Status Change Log")
	now = now_datetime()
	value = deal_value(Deal)
	stages = (
		frappe.qb.from_(Status)
		.left_join(Deal)
		.on((Deal.status == Status.name) & owned_by(Deal.deal_owner, owners))
		.select(
			Status.name.as_("stage"),
			Status.type.as_("type"),
			Count(Deal.name).as_("count"),
			Sum(value).as_("value"),
			Sum(value * IfNull(Deal.probability, 0) / 100).as_("weighted"),
			Avg(TimestampDiff(LiteralValue("DAY"), Deal.creation, now)).as_("avg_age_days"),
		)
		.where(Status.type.notin(CLOSED_STAGE_TYPES))
		.groupby(Status.name, Status.type, Status.position)
		.orderby(Status.position)
	).run(as_dict=True)
	in_stage = dict(
		(
			frappe.qb.from_(Deal)
			.join(Log)
			.on(current_stage_log(Log, Deal))
			.select(Deal.status, Avg(TimestampDiff(LiteralValue("DAY"), Log.from_date, now)))
			.where(
				Deal.status.isin([row.stage for row in stages] or [""]) & owned_by(Deal.deal_owner, owners)
			)
			.groupby(Deal.status)
		).run()
	)
	by_stage = [
		{
			"stage": row.stage,
			"type": row.type,
			"count": row["count"],
			"value": flt(row.value),
			"weighted": flt(row.weighted),
			"avg_age_days": round(flt(row.avg_age_days), 1) if row["count"] else None,
			"avg_days_in_stage": round(flt(in_stage[row.stage]), 1) if row.stage in in_stage else None,
		}
		for row in stages
	]
	return {
		"count": sum(row["count"] for row in by_stage),
		"value": sum(row["value"] for row in by_stage),
		"weighted": sum(row["weighted"] for row in by_stage),
		"by_stage": by_stage,
	}


def get_closed_deals(from_date, owners: list[str] | None) -> list:
	"""Won and lost deals per closing day since from_date, in one grouped query.

	Won uses closed_date, as CRM's dashboard does. CRM stores no date for a loss, so a lost
	deal closes on the day it entered its Lost stage (status change log), else its last edit.
	"""
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	Log = frappe.qb.DocType("CRM Status Change Log")
	closed_on = Coalesce(
		Case().when(Status.type == "Won", Deal.closed_date), Date(Coalesce(Log.from_date, Deal.modified))
	)
	return (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.left_join(Log)
		.on(current_stage_log(Log, Deal))
		.select(
			Status.type.as_("type"),
			closed_on.as_("closed_on"),
			Count(Deal.name).distinct().as_("count"),
			Sum(deal_value(Deal)).as_("value"),
		)
		.where(
			Status.type.isin(CLOSED_STAGE_TYPES)
			& owned_by(Deal.deal_owner, owners)
			& (closed_on >= from_date)
		)
		.groupby(Status.type, closed_on)
	).run(as_dict=True)


def summarize_closed(closed: list, stage_type: str, windows: frappe._dict) -> dict:
	totals = {"count": 0, "value": 0.0, "previous_count": 0, "previous_value": 0.0}
	for row in closed:
		if row.type != stage_type:
			continue
		if in_window(row.closed_on, windows.from_date, windows.to_date):
			totals["count"] += row["count"]
			totals["value"] += flt(row.value)
		elif in_window(row.closed_on, windows.previous_from, windows.previous_to):
			totals["previous_count"] += row["count"]
			totals["previous_value"] += flt(row.value)
	return {
		"count": totals["count"],
		"value": totals["value"],
		"count_delta": totals["count"] - totals["previous_count"],
		"value_delta": totals["value"] - totals["previous_value"],
		"previous_count": totals["previous_count"],
		"previous_value": totals["previous_value"],
	}


def get_stalled_deals(owners: list[str] | None, stalled_days: int) -> dict:
	"""Open deals with no edit, call, note, task change or comment in the last N days."""
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	cutoff = add_days(now_datetime(), -stalled_days)
	stalled = (
		Status.type.notin(CLOSED_STAGE_TYPES) & owned_by(Deal.deal_owner, owners) & (Deal.modified < cutoff)
	)
	for activity in recent_deal_activity(cutoff):
		stalled &= Deal.name.notin(activity)
	value = deal_value(Deal)
	base = frappe.qb.from_(Deal).join(Status).on(Deal.status == Status.name).where(stalled)
	summary = base.select(Count(Deal.name).as_("count"), Sum(value).as_("value")).run(as_dict=True)[0]
	deals = (
		base.select(
			Deal.name,
			Deal.organization,
			Deal.lead_name,
			Deal.status.as_("stage"),
			value.as_("value"),
			Deal.deal_owner,
			Deal.modified.as_("idle_since"),
		)
		.orderby(value, order=frappe.qb.desc)
		.limit(INSIGHT_LIST_LIMIT)
	).run(as_dict=True)
	names = get_full_names({deal.deal_owner for deal in deals})
	return {
		"days": stalled_days,
		"count": summary["count"] or 0,
		"value": flt(summary.value),
		"deals": [
			{
				"name": deal.name,
				"title": deal.organization or deal.lead_name or deal.name,
				"stage": deal.stage,
				"value": flt(deal.value),
				"owner": names.get(deal.deal_owner, deal.deal_owner),
				"owner_id": deal.deal_owner,
				"idle_since": deal.idle_since,
			}
			for deal in deals
		],
	}


def recent_deal_activity(cutoff) -> list:
	"""Subqueries of deals with a call, note, task change or comment since cutoff."""
	queries = []
	for doctype, ref_field, time_field in (
		("CRM Call Log", "reference_docname", "creation"),
		("FCRM Note", "reference_docname", "creation"),
		("CRM Task", "reference_docname", "modified"),
	):
		table = frappe.qb.DocType(doctype)
		queries.append(
			frappe.qb.from_(table)
			.select(table[ref_field])
			.where(
				(table.reference_doctype == "CRM Deal")
				& table[ref_field].isnotnull()
				& (table[time_field] >= cutoff)
			)
		)
	Comment = frappe.qb.DocType("Comment")
	queries.append(
		frappe.qb.from_(Comment)
		.select(Comment.reference_name)
		.where(
			(Comment.reference_doctype == "CRM Deal")
			& Comment.reference_name.isnotnull()
			& (Comment.comment_type == "Comment")
			& (Comment.creation >= cutoff)
		)
	)
	return queries


def get_leads_funnel(windows: frappe._dict, owners: list[str] | None) -> dict:
	"""Leads created in the period, how many are qualified (a Won-type lead status, or
	converted) and how many became deals, with the change against the previous span."""
	Lead = frappe.qb.DocType("CRM Lead")
	LeadStatus = frappe.qb.DocType("CRM Lead Status")
	qualified = (Lead.converted == 1) | (LeadStatus.type == "Won")

	def created_in(from_date, to_date):
		return (Lead.creation >= from_date) & (Lead.creation < add_days(to_date, 1))

	def count_if(criterion):
		return Sum(Case().when(criterion, 1).else_(0))

	current = created_in(windows.from_date, windows.to_date)
	previous = created_in(windows.previous_from, windows.previous_to)
	row = (
		frappe.qb.from_(Lead)
		.left_join(LeadStatus)
		.on(Lead.status == LeadStatus.name)
		.select(
			count_if(current).as_("new"),
			count_if(current & qualified).as_("qualified"),
			count_if(current & (Lead.converted == 1)).as_("converted"),
			count_if(previous).as_("previous_new"),
			count_if(previous & (Lead.converted == 1)).as_("previous_converted"),
		)
		.where(
			owned_by(Lead.lead_owner, owners)
			& (Lead.creation >= windows.previous_from)
			& (Lead.creation < add_days(windows.to_date, 1))
		)
	).run(as_dict=True)[0]
	new, converted = cint(row.new), cint(row.converted)
	return {
		"new": new,
		"qualified": cint(row.qualified),
		"converted": converted,
		"qualified_pct": percent(row.qualified, new),
		"converted_pct": percent(converted, new),
		"new_delta": new - cint(row.previous_new),
		"converted_delta": converted - cint(row.previous_converted),
	}


def get_activity_by_owner(windows: frappe._dict, owners: list[str] | None) -> list[dict]:
	"""Calls logged, notes written and CRM tasks done in the period, per person (three grouped queries)."""
	start, end = windows.from_date, add_days(windows.to_date, 1)
	CallLog = frappe.qb.DocType("CRM Call Log")
	Note = frappe.qb.DocType("FCRM Note")
	Task = frappe.qb.DocType("CRM Task")
	call_user = Coalesce(
		Case().when(CallLog.type == "Incoming", CallLog.receiver).else_(CallLog.caller), CallLog.owner
	)
	counts = {}
	for key, table, user, criterion in (
		("calls", CallLog, call_user, (CallLog.creation >= start) & (CallLog.creation < end)),
		("notes", Note, Note.owner, (Note.creation >= start) & (Note.creation < end)),
		(
			"tasks_done",
			Task,
			Task.assigned_to,
			(Task.status == "Done") & (Task.modified >= start) & (Task.modified < end),
		),
	):
		rows = (
			frappe.qb.from_(table)
			.select(user.as_("user"), Count(table.name))
			.where(criterion & user.isnotnull() & owned_by(user, owners))
			.groupby(user)
		).run()
		for person, count in rows:
			counts.setdefault(person, {"calls": 0, "notes": 0, "tasks_done": 0})[key] = count
	names = get_full_names(set(counts))
	activity = [
		{"user": person, "name": names.get(person, person), **row, "total": sum(row.values())}
		for person, row in counts.items()
	]
	return sorted(activity, key=lambda row: (-row["total"], row["name"]))


def get_sales_trend(windows: frappe._dict, owners: list[str] | None, closed: list) -> list[dict]:
	"""Won value and count, and new leads, for each of the 8 trend periods."""
	trend = [
		{
			"label": window.label,
			"from_date": window.from_date,
			"to_date": window.to_date,
			"won_value": 0.0,
			"won_count": 0,
			"new_leads": 0,
		}
		for window in windows.trend
	]
	for row in closed:
		index = trend_index(windows, row.closed_on) if row.type == "Won" else None
		if index is not None:
			trend[index]["won_value"] += flt(row.value)
			trend[index]["won_count"] += row["count"]
	Lead = frappe.qb.DocType("CRM Lead")
	created = Date(Lead.creation)
	leads = (
		frappe.qb.from_(Lead)
		.select(created, Count(Lead.name))
		.where(owned_by(Lead.lead_owner, owners) & (Lead.creation >= windows.trend[0].from_date))
		.groupby(created)
	).run()
	for day, count in leads:
		index = trend_index(windows, day)
		if index is not None:
			trend[index]["new_leads"] += count
	return trend


def get_closing_deals(windows: frappe._dict, owners: list[str] | None) -> list[dict]:
	"""The biggest open deals expected to close in the current period."""
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	value = deal_value(Deal)
	deals = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Deal.name,
			Deal.organization,
			Deal.lead_name,
			Deal.status.as_("stage"),
			value.as_("value"),
			Deal.probability,
			Deal.expected_closure_date,
			Deal.deal_owner,
		)
		.where(
			Status.type.notin(CLOSED_STAGE_TYPES)
			& owned_by(Deal.deal_owner, owners)
			& Deal.expected_closure_date.between(windows.from_date, windows.end_date)
		)
		.orderby(value, order=frappe.qb.desc)
		.limit(INSIGHT_LIST_LIMIT)
	).run(as_dict=True)
	names = get_full_names({deal.deal_owner for deal in deals})
	return [
		{
			"name": deal.name,
			"title": deal.organization or deal.lead_name or deal.name,
			"stage": deal.stage,
			"value": flt(deal.value),
			"probability": flt(deal.probability),
			"expected_close": deal.expected_closure_date,
			"owner": names.get(deal.deal_owner, deal.deal_owner),
			"owner_id": deal.deal_owner,
		}
		for deal in deals
	]


# ---------- Follow-ups and activity ----------


@frappe.whitelist()
def get_my_follow_ups(scope: str | None = None) -> list[dict]:
	"""Open CRM Tasks due today or overdue, oldest first: assigned to me (scope=mine, the
	default), to my sales team (team) or to anyone (all)."""
	capabilities = require_crm()
	users = scope_users(resolve_crm_scope(capabilities, scope or "mine"))
	end_of_today = get_datetime(f"{frappe.utils.nowdate()} 23:59:59")
	filters = {"status": ("not in", CLOSED_TASK_STATUSES), "due_date": ("<=", end_of_today)}
	if users:
		filters["assigned_to"] = ("in", users)
	tasks = frappe.get_list(
		"CRM Task",
		filters=filters,
		fields=[
			"name",
			"title",
			"status",
			"priority",
			"due_date",
			"assigned_to",
			"reference_doctype",
			"reference_docname",
		],
		order_by="due_date asc",
		limit=100,
	)
	refs = get_ref_labels(tasks)
	assignees = get_full_names({task.assigned_to for task in tasks})
	return [
		{
			"id": task.name,
			"kind": "task",
			"subject": task.title,
			"status": task.status,
			"priority": task.priority,
			"due": task.due_date,
			"overdue": get_datetime(task.due_date) < now_datetime(),
			"with": refs.get(task.reference_docname, {}).get("with"),
			"organization": refs.get(task.reference_docname, {}).get("organization"),
			"ref": ref_of(task.reference_doctype, task.reference_docname),
			"assigned_to": task.assigned_to,
			"assigned_to_name": assignees.get(task.assigned_to, task.assigned_to),
		}
		for task in tasks
	]


@frappe.whitelist(methods=["POST"])
def log_activity(
	doctype: str,
	name: str,
	kind: str,
	text: str,
	next_follow_up: str | None = None,
	phone: str | None = None,
) -> dict:
	"""Log a call, note, meeting, email or WhatsApp on a lead or deal.

	call -> CRM Call Log (Manual, Outgoing, Completed) to `phone` (default: the record's
	mobile) from the caller's own mobile, with the text as its note;
	note -> FCRM Note; meeting / email / whatsapp -> a CRM comment, as Frappe CRM has no
	doctype for them. A call or meeting closes my open follow-ups on the record that are
	due by now. next_follow_up (date or datetime) adds a CRM Task assigned to me.
	"""
	require_crm()
	reference_doctype = REF_DOCTYPES.get(doctype)
	if not reference_doctype:
		frappe.throw(_("doctype must be lead or deal."))
	if kind not in ACTIVITY_KINDS:
		frappe.throw(_("kind must be one of {0}.").format(", ".join(ACTIVITY_KINDS)))
	if not (text or "").strip():
		frappe.throw(_("Write what happened."))
	frappe.has_permission(reference_doctype, "read", name, throw=True)

	if kind == "note":
		add_note(reference_doctype, name, text)
	elif kind == "call":
		add_call(reference_doctype, name, text, phone)
	else:
		add_comment(reference_doctype, name, f"{COMMENT_LABELS[kind]}: {frappe.utils.escape_html(text)}")

	if kind in ("call", "meeting"):
		close_due_follow_ups(reference_doctype, name)
	if next_follow_up:
		add_follow_up_task(reference_doctype, name, next_follow_up)
	return get_timeline(reference_doctype, name)


@frappe.whitelist(methods=["POST"])
def add_follow_up(doctype: str, name: str, due: str, title: str | None = None) -> dict:
	"""Schedule a follow-up (a CRM Task assigned to me) on a lead or deal."""
	require_crm()
	reference_doctype = REF_DOCTYPES.get(doctype)
	if not reference_doctype:
		frappe.throw(_("doctype must be lead or deal."))
	frappe.has_permission(reference_doctype, "read", name, throw=True)
	task = add_follow_up_task(reference_doctype, name, due, title)
	return {"id": task.name, "subject": task.title, "due": task.due_date, "status": task.status}


@frappe.whitelist(methods=["POST"])
def set_task_done(name: str, done: bool = True) -> dict:
	"""Tick or untick a CRM Task."""
	require_crm()
	task = frappe.get_doc("CRM Task", name)
	task.check_permission("write")
	task.status = "Done" if cint(done) else "Todo"
	task.save()
	return {"id": task.name, "status": task.status, "done": task.status == "Done"}


def add_note(reference_doctype: str, name: str, text: str):
	note = frappe.new_doc("FCRM Note")
	note.update(
		{
			"title": text.strip().splitlines()[0][:140],
			"content": frappe.utils.escape_html(text),
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	note.insert()
	return note


def add_call(reference_doctype: str, name: str, text: str, phone: str | None = None):
	"""CRM Call Log needs both numbers: the one called and the caller's own."""
	now = now_datetime()
	called = phone or frappe.db.get_value(reference_doctype, name, "mobile_no")
	caller = frappe.db.get_value("User", frappe.session.user, "mobile_no") or frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, "cell_number"
	)
	if not called:
		frappe.throw(_("{0} has no mobile number. Send the number you called.").format(name))
	if not caller:
		frappe.throw(_("Add your mobile number to your user profile to log calls."))
	call = frappe.new_doc("CRM Call Log")
	call.update(
		{
			"type": "Outgoing",
			"status": "Completed",
			"telephony_medium": "Manual",
			"caller": frappe.session.user,
			"from": caller,
			"to": called,
			"start_time": now,
			"end_time": now,
			"note": add_note(reference_doctype, name, text).name,
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	call.insert()
	return call


def add_follow_up_task(reference_doctype: str, name: str, due: str, title: str | None = None):
	label = frappe.db.get_value(
		reference_doctype, name, "lead_name" if reference_doctype == "CRM Lead" else "organization"
	)
	task = frappe.new_doc("CRM Task")
	task.update(
		{
			"title": title or _("Follow up with {0}").format(label or name),
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": frappe.session.user,
			"due_date": get_datetime(due),
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	task.insert()
	return task


def close_due_follow_ups(reference_doctype: str, name: str) -> None:
	due = frappe.get_list(
		"CRM Task",
		filters={
			"reference_doctype": reference_doctype,
			"reference_docname": name,
			"assigned_to": frappe.session.user,
			"status": ("not in", CLOSED_TASK_STATUSES),
			"due_date": ("<=", now_datetime()),
		},
		pluck="name",
	)
	for task_name in due:
		task = frappe.get_doc("CRM Task", task_name)
		task.status = "Done"
		task.save()


def get_timeline(reference_doctype: str, name: str) -> dict:
	"""Notes, tasks and a merged newest-first activity list (calls, notes, comments)."""
	notes = get_linked_notes(name)
	tasks = get_linked_tasks(name)
	CallLog = frappe.qb.DocType("CRM Call Log")
	calls = (
		frappe.qb.from_(CallLog)
		.select(
			CallLog.name,
			CallLog.type,
			CallLog.status,
			CallLog.duration,
			CallLog.caller,
			CallLog.receiver,
			CallLog.note,
			Coalesce(CallLog.start_time, CallLog.creation).as_("at"),
		)
		.where((CallLog.reference_doctype == reference_doctype) & (CallLog.reference_docname == name))
	).run(as_dict=True)
	Comment = frappe.qb.DocType("Comment")
	comments = (
		frappe.qb.from_(Comment)
		.select(Comment.name, Comment.content, Comment.comment_email, Comment.creation)
		.where(
			(Comment.reference_doctype == reference_doctype)
			& (Comment.reference_name == name)
			& (Comment.comment_type == "Comment")
		)
	).run(as_dict=True)

	people = get_full_names(
		{note.owner for note in notes}
		| {call.caller or call.receiver for call in calls}
		| {comment.comment_email for comment in comments}
	)
	note_text = {note.name: strip_html(note.content or note.title or "").strip() for note in notes}
	call_notes = {call.note for call in calls if call.note}

	activities = [
		{
			"id": call.name,
			"at": call.at,
			"who": people.get(call.caller or call.receiver),
			"kind": "call",
			"text": note_text.get(call.note) or _("{0} call, {1}").format(call.type, call.status),
		}
		for call in calls
	]
	activities += [
		{
			"id": note.name,
			"at": note.creation,
			"who": people.get(note.owner),
			"kind": "note",
			"text": note_text[note.name],
		}
		for note in notes
		if note.name not in call_notes
	]
	activities += [
		{
			"id": comment.name,
			"at": comment.creation,
			"who": people.get(comment.comment_email, comment.comment_email),
			"kind": "comment",
			"text": strip_html(comment.content or "").strip(),
		}
		for comment in comments
	]
	activities.sort(key=lambda row: get_datetime(row["at"]), reverse=True)
	return {
		"notes": [
			{
				"id": note.name,
				"at": note.creation,
				"who": people.get(note.owner),
				"text": note_text[note.name],
			}
			for note in sorted(notes, key=lambda note: note.creation, reverse=True)
		],
		"tasks": [
			{
				"id": task.name,
				"title": task.title,
				"due": task.due_date,
				"status": task.status,
				"done": task.status == "Done",
				"assigned_to": task.assigned_to,
			}
			for task in sorted(tasks, key=lambda task: str(task.due_date or ""))
		],
		"activities": activities,
	}


def get_next_follow_ups(reference_doctype: str, names: list[str]) -> dict:
	if not names:
		return {}
	Task = frappe.qb.DocType("CRM Task")
	return dict(
		(
			frappe.qb.from_(Task)
			.select(Task.reference_docname, Min(Task.due_date))
			.where(
				(Task.reference_doctype == reference_doctype)
				& (Task.reference_docname.isin(names))
				& (Task.status.notin(CLOSED_TASK_STATUSES))
				& (Task.due_date.isnotnull())
			)
			.groupby(Task.reference_docname)
		).run()
	)


def get_last_calls(reference_doctype: str, names: list[str]) -> dict:
	if not names:
		return {}
	CallLog = frappe.qb.DocType("CRM Call Log")
	return dict(
		(
			frappe.qb.from_(CallLog)
			.select(CallLog.reference_docname, Max(Coalesce(CallLog.start_time, CallLog.creation)))
			.where((CallLog.reference_doctype == reference_doctype) & (CallLog.reference_docname.isin(names)))
			.groupby(CallLog.reference_docname)
		).run()
	)


def get_ref_labels(tasks: list) -> dict:
	"""Contact name and organization for each lead or deal the tasks point at (two queries)."""
	labels = {}
	for doctype, fields in (
		("CRM Lead", ["name", "lead_name", "organization"]),
		("CRM Deal", ["name", "lead_name", "organization"]),
	):
		names = [task.reference_docname for task in tasks if task.reference_doctype == doctype]
		if names:
			for row in frappe.get_list(doctype, filters={"name": ("in", names)}, fields=fields):
				labels[row.name] = {"with": row.lead_name, "organization": row.organization}
	return labels


def ref_of(reference_doctype: str | None, name: str | None) -> dict | None:
	kind = {doctype: key for key, doctype in REF_DOCTYPES.items()}.get(reference_doctype)
	return {"doctype": kind, "name": name} if kind else None


def require_crm() -> frappe._dict:
	return require("crm", _("Your role has no access to CRM."))


def scope_filter(capabilities: frappe._dict, scope: str | None, owner_field: str) -> dict:
	"""get_list filters for a lead or deal list scope (checked against crm_scopes)."""
	users = list_scope_users(capabilities, resolve_crm_scope(capabilities, scope))
	return {owner_field: ("in", users)} if users else {}


def search_filters(search: str | None, fields: list[str]) -> dict | None:
	search = (search or "").strip()
	if not search:
		return None
	return {field: ("like", f"%{search}%") for field in fields}
