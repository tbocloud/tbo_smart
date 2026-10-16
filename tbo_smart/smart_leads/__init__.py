"""Smart leads on ERPNext's Lead (docs/smart-leads.md).

Four features, each behind its own switch in TBO Smart Settings, all off by default:

- lead scoring and the daily Call today list (`enable_lead_scoring`, AI part
  `lead_scoring_use_ai`);
- the follow-up engine with its escalation ladder and 09:30 digest (`enable_lead_follow_ups`);
- AI call notes, the lead brief and WhatsApp drafts (`enable_lead_notes`);
- the one-time AI triage of stale leads (`enable_lead_triage`).

Everything hangs off ERPNext's Lead, so on a site without ERPNext every switch reads as off
and nothing is set up.
"""

import frappe
from frappe.utils import cint

# The statuses a salesperson still works on: every pipeline status but Closed – Won and Closed –
# Lost (docs/lead-pipeline.md §2). Closed leads are never scored for calling, flagged or triaged.
from tbo_smart.lead_pipeline.statuses import OPEN_STATUSES
from tbo_smart.sales import erpnext_installed

SETTINGS = "TBO Smart Settings"
FOLLOW_UP_TYPE = "Sales follow-up"
# designation roles from the quotation approval feature (tbo_smart.install.QUOTATION_LEVELS)
BDM_ROLE = "Business Development Manager"
SALES_HEAD_ROLE = "Sales & Marketing Head"


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def switch(fieldname: str) -> bool:
	return erpnext_installed() and bool(cint(setting(fieldname)))


def scoring_enabled() -> bool:
	return switch("enable_lead_scoring")


def ai_scoring_enabled() -> bool:
	return scoring_enabled() and bool(cint(setting("lead_scoring_use_ai")))


def follow_ups_enabled() -> bool:
	return switch("enable_lead_follow_ups")


def notes_enabled() -> bool:
	return switch("enable_lead_notes")


def triage_enabled() -> bool:
	return switch("enable_lead_triage")


def any_enabled() -> bool:
	return scoring_enabled() or follow_ups_enabled() or notes_enabled() or triage_enabled()


def call_today_size() -> int:
	return min(max(cint(setting("call_today_size")) or 12, 5), 30)


def is_sales_head(user: str | None = None) -> bool:
	"""Sees the team's lists and the Needs owner list: System Manager, Sales Manager, the
	Sales & Marketing Head role, or the Phase 2 "Head - Sales & Marketing" user."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	return (
		user == "Administrator"
		or bool(roles & {"System Manager", "Sales Manager", SALES_HEAD_ROLE})
		or user == setting("escalation_sales_head")
	)
