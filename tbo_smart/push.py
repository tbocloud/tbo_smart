"""Expo push for TBO Smart: each new notification for a user reaches their registered phones.

Off unless TBO Smart Settings > Mobile push is ticked. Sending runs in a background job
after the commit, and no failure here may break the insert that triggered it.
"""

import frappe
import requests
from frappe.utils import cint, strip_html

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_TIMEOUT_SECONDS = 10
BODY_LIMIT = 180
SITE = "ess"


def on_notification_log(doc, method=None):
	"""doc_events: Notification Log after_insert (assignments, mentions, shares, alerts)."""
	enqueue_push(
		doc.for_user,
		title=doc.subject,
		body=doc.email_content,
		doctype=doc.document_type,
		name=doc.document_name,
	)


def on_pwa_notification(doc, method=None):
	"""doc_events: PWA Notification after_insert. HRMS writes leave and expense
	approval requests and decisions here, not to Notification Log."""
	enqueue_push(
		doc.to_user,
		title=doc.message,
		body=None,
		doctype=doc.reference_document_type,
		name=doc.reference_document_name,
	)


def enqueue_push(
	user: str | None, title: str | None, body: str | None, doctype: str | None, name: str | None
):
	try:
		if not user or not cint(frappe.db.get_single_value("TBO Smart Settings", "enable_mobile_push")):
			return
		if not frappe.db.exists("TBO Mobile Device", {"user": user}):
			return
		frappe.enqueue(
			"tbo_smart.push.send_push",
			queue="short",
			enqueue_after_commit=True,
			user=user,
			title=clean(title),
			body=clean(body),
			doctype=doctype,
			name=name,
		)
	except Exception:
		frappe.log_error(title="TBO Smart push: enqueue failed")


def send_push(user: str, title: str, body: str, doctype: str | None, name: str | None):
	"""Background job: one Expo request for all of the user's devices; drop dead tokens."""
	try:
		tokens = frappe.get_all("TBO Mobile Device", filters={"user": user}, pluck="expo_push_token")
		if not tokens:
			return
		data = {"site": SITE, "doctype": doctype, "name": name, "route": route_for(user, doctype, name)}
		messages = [
			{"to": token, "title": title, "body": body, "data": data, "sound": "default"} for token in tokens
		]
		response = requests.post(
			EXPO_PUSH_URL,
			json=messages,
			headers={"Accept": "application/json", "Content-Type": "application/json"},
			timeout=EXPO_TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		remove_unregistered(tokens, response.json().get("data") or [])
	except Exception:
		frappe.log_error(title="TBO Smart push: send failed")


def remove_unregistered(tokens: list[str], tickets: list[dict]) -> None:
	"""Expo answers one ticket per message, in order; DeviceNotRegistered means uninstalled."""
	dead = [
		token
		for token, ticket in zip(tokens, tickets, strict=False)
		if ticket.get("status") == "error"
		and (ticket.get("details") or {}).get("error") == "DeviceNotRegistered"
	]
	if dead:
		frappe.db.delete("TBO Mobile Device", {"expo_push_token": ("in", dead)})


def route_for(user: str, doctype: str | None, name: str | None) -> str:
	"""The app screen for a document. HR documents open the approvals list for an approver."""
	if doctype in ("Leave Application", "Expense Claim") and name:
		employee = frappe.db.get_value(doctype, name, "employee")
		if employee and frappe.db.get_value("Employee", employee, "user_id") == user:
			return "/hr/leave" if doctype == "Leave Application" else "/hr/expenses"
		return "/team/approvals?kind=" + ("leave" if doctype == "Leave Application" else "expense")
	routes = {
		"Salary Slip": "/hr/salary",
		"Employee Checkin": "/hr/attendance",
		"Attendance": "/hr/attendance",
		"CRM Lead": f"/crm/lead/{name}",
		"CRM Deal": f"/crm/deal/{name}",
		"CRM Organization": f"/crm/org/{name}",
		"TBO Quotation Adjustment": f"/quotations/{name}",
	}
	return routes.get(doctype, "/alerts") if name else "/alerts"


def clean(text: str | None) -> str:
	text = " ".join(strip_html(text or "").split())
	return text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - 3] + "..."
