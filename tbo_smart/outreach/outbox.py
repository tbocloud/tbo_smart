"""The outbox the hub shows: a user's own drafts, or a department's for its head (docs/prospecting.md §10)."""

import frappe
from frappe.utils import cint, today

from tbo_smart.outreach import EMAIL, MESSAGE, OPT_OUT, STATUSES, WHATSAPP, daily_cap, enabled
from tbo_smart.outreach.drafting import current_progress
from tbo_smart.outreach.guards import in_window, next_window, queued_count, sent_today, window_times
from tbo_smart.outreach.sending import outgoing_account
from tbo_smart.prospecting import headed_departments
from tbo_smart.query import aggregate
from tbo_smart.sales.departments import MANAGER_ROLES
from tbo_smart.smart_leads.utils import whatsapp_link

LIMIT = 200
FIELDS = [
	"name",
	"lead",
	"lead_name",
	"department",
	"channel",
	"status",
	"subject",
	"body",
	"recipient",
	"drafted_by",
	"owner",
	"approved_by",
	"approved_on",
	"sent_on",
	"error",
	"communication",
	"prospect_run",
	"batch",
	"creation",
	"modified",
]


def visible_scope(user: str) -> tuple[dict, list]:
	"""(filters, or_filters) for the messages `user` may list: all for managers, their own plus
	their departments' for a head, only their own otherwise."""
	if set(frappe.get_roles(user)) & MANAGER_ROLES:
		return {}, []
	heads = headed_departments(user)
	if heads:
		return {}, [["owner", "=", user], ["department", "in", heads]]
	return {"owner": user}, []


def status_filter(status) -> list[str]:
	statuses = frappe.parse_json(status) if isinstance(status, str) and status.startswith("[") else status
	statuses = [statuses] if isinstance(statuses, str) else list(statuses or [])
	unknown = [value for value in statuses if value not in STATUSES]
	if unknown:
		frappe.throw(frappe._("status must be one of {0}.").format(", ".join(STATUSES)))
	return statuses


def get_outbox(user: str, status=None, batch: str | None = None, run: str | None = None) -> dict:
	"""{enabled, email_ready, rows, counts, cap, window, drafting, opt_out}: the user's messages,
	newest first, their counts by status and what is left of today's email cap."""
	filters, or_filters = visible_scope(user)
	counts = {
		row.status: cint(row.total)
		for row in frappe.get_all(
			MESSAGE,
			filters=filters,
			or_filters=or_filters,
			fields=["status", aggregate("count", "name", "total")],
			group_by="status",
		)
	}
	listed = dict(filters)
	if statuses := status_filter(status):
		listed["status"] = ("in", statuses)
	if batch:
		listed["batch"] = batch
	if run:
		listed["prospect_run"] = run
	rows = frappe.get_all(
		MESSAGE,
		filters=listed,
		or_filters=or_filters,
		fields=FIELDS,
		order_by="creation desc",
		limit_page_length=LIMIT,
	)
	leads = {
		row.name: row
		for row in frappe.get_all(
			"Lead",
			filters={"name": ("in", [row.lead for row in rows] or [""])},
			fields=["name", "company_name", "tbo_stage", "unsubscribed", "tbo_last_reply_class"],
			ignore_permissions=True,
		)
	}
	names = frappe.get_all(
		"User",
		filters={"name": ("in", list({row.owner for row in rows}) or [""])},
		fields=["name", "full_name"],
	)
	full_names = {row.name: row.full_name for row in names}
	start, end = window_times()
	limit = daily_cap()
	used = sent_today(user)
	return {
		"enabled": enabled(),
		"email_ready": bool(outgoing_account()),
		"rows": [serialize(row, leads.get(row.lead), full_names) for row in rows],
		"counts": {status_name: counts.get(status_name, 0) for status_name in STATUSES},
		"cap": {"limit": limit, "used": used, "left": max(limit - used, 0), "queued": queued_count(user)},
		"window": {
			"start": str(start)[:5],
			"end": str(end)[:5],
			"open": in_window(),
			"next": str(next_window()),
		},
		"drafting": current_progress(user),
		"opt_out": OPT_OUT,
		"today": today(),
	}


def serialize(row, lead, full_names: dict) -> dict:
	return {
		"name": row.name,
		"lead": row.lead,
		"lead_name": row.lead_name,
		"company_name": lead.company_name if lead else None,
		"lead_stage": lead.tbo_stage if lead else None,
		"unsubscribed": cint(lead.unsubscribed) if lead else 0,
		"last_reply_class": lead.tbo_last_reply_class if lead else None,
		"department": row.department,
		"channel": row.channel,
		"status": row.status,
		"subject": row.subject,
		"body": row.body,
		"recipient": row.recipient,
		"drafted_by": row.drafted_by,
		"owner": row.owner,
		"owner_name": full_names.get(row.owner),
		"approved_by": row.approved_by,
		"approved_on": row.approved_on,
		"sent_on": row.sent_on,
		"error": row.error,
		"communication": row.communication,
		"prospect_run": row.prospect_run,
		"batch": row.batch,
		"url": whatsapp_link(row.recipient, row.body) if row.channel == WHATSAPP else None,
		"queued": row.channel == EMAIL and row.status == "Approved",
		"creation": row.creation,
		"modified": row.modified,
	}
