"""The leads overview and "AI runs leads" for TBO Smart (docs/tbo-smart-api-ess-crm.md §6.5).

These are the hub's own methods (tbo_smart.api.hub_leads, docs/hub-leads-api.md §2.4 and §2.6)
behind the `crm` capability, like every other CRM method of the app. The app calls as the
person, so the hub's methods run as the session user: their sales scope, their reads and their
writes. A message the person sent from their own WhatsApp or mail is logged with
crm.log_activity (kind whatsapp or email), the same record the hub's log_message_sent makes.
"""

import frappe

from tbo_smart.api import hub_leads
from tbo_smart.api.mobile.crm import get_lead, require_crm


@frappe.whitelist()
def get_overview(period: str | None = None, owner: str | None = None) -> dict:
	"""The leads overview for my sales scope (`owner`: one person in it), with the AI's plan for
	today and its counts. `period`: week, month (default) or quarter."""
	require_crm()
	return hub_leads.get_overview(period=period, owner=owner)


@frappe.whitelist()
def get_lead_ai(name: str) -> dict:
	"""What the AI suggests for the lead, its health and likely duplicates."""
	require_crm()
	return hub_leads.get_lead_ai(name)


@frappe.whitelist(methods=["POST"])
def refresh_lead_ai(name: str) -> dict:
	"""Ask the AI to read the lead again now (write on the lead, once a minute)."""
	require_crm()
	return hub_leads.refresh_lead_ai(name)


@frappe.whitelist(methods=["POST"])
def apply_ai_stage(name: str) -> dict:
	"""Move the lead to the stage the AI suggested, as me. Returns the lead as crm.get_lead."""
	require_crm()
	hub_leads.apply_ai_stage(name)
	return get_lead(name)


@frappe.whitelist(methods=["POST"])
def dismiss_ai_stage(name: str) -> dict:
	"""Drop the AI's stage suggestion; returns what the AI suggests now."""
	require_crm()
	return hub_leads.dismiss_ai_stage(name)


@frappe.whitelist(methods=["POST"])
def draft_lead_message(name: str, channel: str, language: str = "en") -> dict:
	"""A WhatsApp or email text for me to edit and send from my own app. Nothing is sent."""
	require_crm()
	return hub_leads.draft_lead_message(name, channel, language)
