"""Smart lead cards on the CRM dashboard (docs/smart-leads.md §4.4), through the CRM fork's
`crm_dashboard_providers` / `crm_dashboard_layout` hooks.

| Card | Switch | Value |
| --- | --- | --- |
| Call today | Lead scoring | Leads on the person's Call today list now |
| Overdue lead follow-ups | Lead follow-ups | Open follow-ups on open leads, assigned to the person, due before today |

The person is the dashboard's user filter (a salesperson always gets themselves), else the
session user. The date range doesn't apply: both are about today.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import getdate

from tbo_smart.smart_leads import CLOSED_TASK_STATUSES, OPEN_STATUSES, follow_ups_enabled, scoring_enabled
from tbo_smart.smart_leads.call_today import build_call_today


def get_tbo_call_today(from_date=None, to_date=None, user=None) -> dict | None:
	if not scoring_enabled():
		return None
	try:
		result = build_call_today(user or frappe.session.user)
	except frappe.PermissionError:
		return None
	return {
		"title": _("Call today"),
		"tooltip": _("Leads to call today: overdue and due follow-ups first, then the best scores"),
		"value": len(result["data"]) + result["more"],
	}


def get_tbo_lead_follow_ups_overdue(from_date=None, to_date=None, user=None) -> dict | None:
	if not follow_ups_enabled():
		return None
	Task = frappe.qb.DocType("CRM Task")
	Lead = frappe.qb.DocType("CRM Lead")
	count = (
		frappe.qb.from_(Task)
		.join(Lead)
		.on((Task.reference_doctype == "CRM Lead") & (Task.reference_docname == Lead.name))
		.select(Count(Task.name))
		.where(
			(Task.assigned_to == (user or frappe.session.user))
			& Task.status.notin(CLOSED_TASK_STATUSES)
			& (Task.due_date < getdate())
			& Lead.status.isin(OPEN_STATUSES)
			& (Lead.converted == 0)
		)
	).run()[0][0]
	return {
		"title": _("Overdue lead follow-ups"),
		"tooltip": _("Your follow-ups on open leads that are past their day"),
		"value": count,
		"negativeIsBetter": True,
	}


def get_cards(from_date, to_date, user=None) -> list[dict]:
	"""crm_dashboard_layout hook: the smart lead cards that are switched on."""
	cards = []
	for name in ("tbo_call_today", "tbo_lead_follow_ups_overdue"):
		data = globals()[f"get_{name}"](from_date, to_date, user)
		if data is not None:
			cards.append({"name": name, "type": "number_chart", "w": 4, "h": 3, "data": data})
	return cards
