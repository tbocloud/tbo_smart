"""WhatsApp through TBO Chat for the TBO Support hub (docs/hub-leads-api.md §2.19,
docs/whatsapp-outreach.md).

The hub owns the Chatwoot connection. It asks for the queue of approved WhatsApp messages, sends
them and reports back; it forwards what leads write and Chatwoot's delivery receipts. Those calls
are the integration user's own (no acting_user). The lead page's two calls run as the acting user.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.api.hub_leads import acting_as, integration_role
from tbo_smart.outreach import whatsapp


def integration_only() -> None:
	if integration_role() not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only the hub's integration user may call this."), frappe.PermissionError)


@frappe.whitelist()
def get_whatsapp_queue(
	country_code: str | None = None, limit: int | str = whatsapp.QUEUE_LIMIT
) -> list[dict]:
	"""Approved WhatsApp messages waiting for TBO Chat, during the send window, each claimed for
	the caller: [{name, lead, to (+E.164), body, lead_name, first_name, company_name, why_call,
	contact_id, conversation_id, window_open}]. `country_code` (digits, default 91) is added to
	10-digit numbers. Report every message back with `report_whatsapp`."""
	integration_only()
	return whatsapp.pending(str(country_code or "").strip() or None, cint(limit))


@frappe.whitelist(methods=["POST"])
def report_whatsapp(
	name: str,
	state: str,
	text: str | None = None,
	contact_id: str | None = None,
	conversation_id: str | None = None,
	message_id: str | None = None,
	template: str | None = None,
	error: str | None = None,
) -> dict:
	"""The hub's result for a queued message: `state` "sent" (with `text`, the ids and the
	`template` used), "failed" (with `error`) or "manual" (TBO Chat is not ready: the BD sends it
	by hand). Returns {name, status, message}."""
	integration_only()
	return whatsapp.report(name, state, text, contact_id, conversation_id, message_id, template, error)


@frappe.whitelist(methods=["POST"])
def receive_whatsapp(
	phone: str,
	text: str,
	conversation_id: str | None = None,
	contact_id: str | None = None,
	message_id: str | None = None,
	at: str | None = None,
	sender_name: str | None = None,
) -> dict:
	"""A WhatsApp message a contact sent in TBO Chat. When its number or conversation is a lead's
	it is filed on the lead, opens the 24-hour window and (with Outreach on) is sorted like an
	email reply. Returns {matched, lead, communication}; not matched: not a lead's chat."""
	integration_only()
	return whatsapp.receive(phone, text, conversation_id, contact_id, message_id, at, sender_name)


@frappe.whitelist(methods=["POST"])
def whatsapp_status(message_id: str, status: str, error: str | None = None) -> dict:
	"""A delivery receipt (sent, delivered, read, failed) for a message the hub sent.
	Returns {matched, status}."""
	integration_only()
	return whatsapp.apply_status(message_id, status, error)


@frappe.whitelist()
def get_lead_whatsapp(lead: str, acting_user: str | None = None) -> dict:
	"""The lead's WhatsApp state for its reply box: {to, conversation_id, contact_id, last_in,
	window_open, window_ends, blocked}. `blocked` says why nothing may be sent."""
	with acting_as(acting_user):
		frappe.has_permission("Lead", "read", lead, throw=True)
		return whatsapp.lead_state(lead)


@frappe.whitelist(methods=["POST"])
def log_lead_whatsapp(
	lead: str,
	text: str,
	message_id: str | None = None,
	contact_id: str | None = None,
	conversation_id: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""File a message the acting user sent from the lead page through TBO Chat on the lead's
	timeline as theirs and keep the chat ids. Returns {communication}."""
	with acting_as(acting_user) as user:
		frappe.has_permission("Lead", "write", lead, throw=True)
		if reason := whatsapp.lead_state(lead)["blocked"]:
			frappe.throw(reason)
		doc = whatsapp.log_sent(lead, text, message_id, contact_id, conversation_id, user)
		return {"communication": doc.name}
