"""Outreach for the TBO Support hub (docs/hub-leads-api.md §2.17): AI first messages that a
person approves before they go, and what the outbox holds.

Every method runs as the acting user (see hub_leads.acting_as) and needs write permission on
each message's lead. Nothing is sent without `approve_outreach`.
"""

import frappe
from frappe import _

from tbo_smart.api.hub_leads import acting_as
from tbo_smart.outreach import drafting, outbox, require_enabled, sending


def names_of(names) -> list[str]:
	names = frappe.parse_json(names) if isinstance(names, str) and names.strip().startswith("[") else names
	names = [names] if isinstance(names, str) else list(names or [])
	if not names:
		frappe.throw(_("Name the messages."))
	return names


@frappe.whitelist(methods=["POST"])
def draft_outreach(channel: str, leads=None, run: str | None = None, acting_user: str | None = None) -> dict:
	"""Draft first messages for `leads` (a list) or a prospect `run`'s leads, in the background:
	{batch, queued, skipped: [{lead, lead_name, reason}]}. Leads the user may not edit, that
	unsubscribed, are lost, have no address or already have a first message on the channel are
	skipped; at most TBO Smart Settings' drafts per request are queued."""
	with acting_as(acting_user):
		require_enabled()
		return drafting.queue_drafts(leads, run, channel)


@frappe.whitelist()
def get_outbox(
	status=None, batch: str | None = None, run: str | None = None, acting_user: str | None = None
) -> dict:
	"""The acting user's drafts (a head sees their departments', a manager everyone's), newest first:
	{enabled, email_ready, rows, counts, cap, window, drafting, opt_out, today}."""
	with acting_as(acting_user) as user:
		return outbox.get_outbox(user, status, batch, run)


@frappe.whitelist(methods=["POST"])
def update_outreach(name: str, body: str, subject: str | None = None, acting_user: str | None = None) -> dict:
	"""Edit a draft's subject and body (a failed message becomes a draft again)."""
	with acting_as(acting_user):
		return sending.update(name, subject, body)


@frappe.whitelist(methods=["POST"])
def regenerate_outreach(name: str, acting_user: str | None = None) -> dict:
	"""Replace a draft's text with a new AI draft."""
	with acting_as(acting_user):
		require_enabled()
		doc = sending.load(name)
		if doc.status not in ("Draft", "Failed"):
			frappe.throw(_("Only a draft can be regenerated."))
		drafting.regenerate(doc)
		doc.save(ignore_permissions=True)
		return sending.result(doc)


@frappe.whitelist(methods=["POST"])
def approve_outreach(names, acting_user: str | None = None) -> list[dict]:
	"""Approve drafts: [{name, status, message}]. An email goes out at once when the send window is
	open and the user's daily cap allows, else it stays Approved and the 10-minute sender delivers
	it. A WhatsApp message becomes Approved (ready); the hub opens WhatsApp and the BD confirms with
	`mark_outreach_sent`. Fails with "Set up an outgoing email account first." when an email is
	among them and the site has none."""
	with acting_as(acting_user) as user:
		require_enabled()
		return sending.approve(names_of(names), user)


@frappe.whitelist(methods=["POST"])
def cancel_outreach(names, acting_user: str | None = None) -> list[dict]:
	"""Withdraw drafts, approved and failed messages: [{name, status, message}]."""
	with acting_as(acting_user):
		return sending.cancel(names_of(names))


@frappe.whitelist(methods=["POST"])
def mark_outreach_sent(names, acting_user: str | None = None) -> list[dict]:
	"""The BD sent these approved WhatsApp messages by hand: they become Sent and are logged on the
	lead as the BD's."""
	with acting_as(acting_user):
		return sending.mark_sent(names_of(names))
