"""Telling people: Teams direct messages, email and in-app notifications (docs/smart-leads.md §5.4).

Teams goes through the same Power Automate workflow as the TBO Support hub's HD Chat Settings
("Direct Message Workflow URL"): it receives `recipient` (the person's email) plus an Adaptive
Card and posts it to them as the Flow bot. No Azure app is needed.
"""

from urllib.parse import urlparse

import frappe
import requests
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

from tbo_smart.ai import password

TIMEOUT = 10
# Teams Workflows (Power Automate) URLs, old and new hosts
WORKFLOW_HOSTS = ("logic.azure.com", "powerplatform.com")


class TeamsError(Exception):
	pass


def on_domain(host: str, domains: tuple[str, ...]) -> bool:
	"""`host` is one of `domains` or a subdomain of one (not just a name ending in it)."""
	return any(host == domain or host.endswith("." + domain) for domain in domains)


def teams_url_problem(url: str) -> str | None:
	"""Why a pasted Teams DM workflow URL can't work, or None. Catches a Teams message link or a
	cut-off URL when the settings are saved, not as an HTTP error at 09:30."""
	parsed = urlparse(url)
	host = (parsed.hostname or "").lower()
	if on_domain(host, ("teams.microsoft.com",)):
		return _(
			"this is a link to a Teams message, not the workflow's URL. In Power Automate, open the flow, click its trigger and copy the HTTP URL."
		)
	if parsed.scheme != "https" or not host:
		return _("paste the full https:// URL of the workflow.")
	if not on_domain(host, WORKFLOW_HOSTS):
		return _(
			"this doesn't look like a Teams Workflows (Power Automate) URL. Copy the HTTP URL from the flow's trigger."
		)
	if "sig=" not in parsed.query:
		return _(
			"the URL is cut off: it must end with &sp=…&sv=…&sig=…. Copy it again from the flow's trigger."
		)
	return None


def teams_message(text: str, url: str | None, button: str) -> dict:
	"""A Teams message with one Adaptive Card, the shape Workflows webhooks accept."""
	card = {
		"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
		"type": "AdaptiveCard",
		"version": "1.4",
		# Teams only breaks lines on blank lines
		"body": [{"type": "TextBlock", "text": text.replace("\n", "\n\n"), "wrap": True}],
	}
	if url:
		card["actions"] = [{"type": "Action.OpenUrl", "title": button, "url": url}]
	return {
		"type": "message",
		"attachments": [
			{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
		],
	}


def send_teams(email: str, text: str, url: str | None, button: str) -> bool:
	"""Message one person in Teams; False when no workflow URL is set."""
	workflow = password("teams_dm_workflow_url")
	if not workflow:
		return False
	response = requests.post(
		workflow, json={"recipient": email, **teams_message(text, url, button)}, timeout=TIMEOUT
	)
	if response.status_code >= 300:
		raise TeamsError(f"http_{response.status_code}")
	return True


def deliver(user: str, subject: str, text: str, html: str, url: str, button: str) -> str:
	"""Teams, else email. Returns the channel used ("teams", "email" or "none")."""
	email = frappe.db.get_value("User", user, "email") or user
	try:
		if send_teams(email, text, url, button):
			return "teams"
	except (TeamsError, requests.RequestException):
		frappe.log_error(title="TBO smart leads: Teams message not sent")
	try:
		frappe.sendmail(recipients=[email], subject=subject, message=html, now=frappe.flags.in_test)
		return "email"
	except Exception:
		frappe.log_error(title="TBO smart leads: email not sent")
		return "none"


def notify(users, subject: str, document_type: str, document_name: str) -> None:
	"""In-app notifications (Notification Log), which tbo_smart.push also sends to phones."""
	users = sorted({user for user in users if user})
	if users:
		enqueue_create_notification(
			users,
			{
				"type": "Alert",
				"document_type": document_type,
				"document_name": document_name,
				"subject": subject,
				"from_user": "Administrator",
			},
		)
