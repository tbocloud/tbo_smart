"""WhatsApp through TBO Chat (docs/whatsapp-outreach.md).

The hub owns the Chatwoot connection; the ERP owns the lead. An approved WhatsApp message is
queued here (`queue_for_chat`, the WhatsApp entry of `sending.DELIVERERS`), the hub fetches the
queue, sends it through Chatwoot and reports back (`report`). A reply from the lead comes in
through `receive` and is filed on the lead as a Communication (medium Chat), which the reply
sorter in `replies` then classifies exactly like an email. Delivery receipts update the message
(`apply_status`). Without a TBO Chat inbox the hub does not ask for the queue and approving a
WhatsApp message stays manual (wa.me, then "Mark sent").
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, get_datetime, now_datetime

from tbo_smart.outreach import APPROVED, CANCELLED, FAILED, MESSAGE, SENT, WHATSAPP
from tbo_smart.outreach.guards import in_window, never_contact, whatsapp_number
from tbo_smart.prospecting.signals import prospect_of
from tbo_smart.smart_leads.utils import INDIA, normalise_phone

QUEUED = "Queued"
SENDING = "Sending"
DELIVERED = "Delivered"
READ = "Read"
# what Chatwoot reports for a message, in the order it normally happens
RANK = {QUEUED: 0, SENDING: 1, SENT: 2, DELIVERED: 3, READ: 4}
RECEIPTS = {"sent": SENT, "delivered": DELIVERED, "read": READ}
WINDOW_HOURS = 24
QUEUE_LIMIT = 20
MATCH_DIGITS = 10
MEDIUM = "Chat"
SUBJECT = "WhatsApp"
CHAT_MESSAGE_PREFIX = "chat:"
# a lone word like this is a request to stop, whatever the AI would say
STOP_WORDS = ("stop", "unsubscribe", "stop messages", "stop messaging", "opt out", "optout")


# ---------- the 24-hour window ----------


def window_ends(last_in) -> str | None:
	"""When free text stops being allowed, or None when the lead never wrote."""
	return str(add_to_date(get_datetime(last_in), hours=WINDOW_HOURS)) if last_in else None


def window_open(last_in) -> bool:
	"""WhatsApp allows free text only within 24 hours of the lead's last message to us."""
	return bool(last_in) and now_datetime() < add_to_date(get_datetime(last_in), hours=WINDOW_HOURS)


def lead_state(lead: str) -> dict:
	"""What the lead page needs to show the conversation and the reply box:
	{to, conversation_id, contact_id, last_in, window_open, window_ends, blocked}. `blocked` is why
	nothing may be sent (unsubscribed, lost, no number), else None."""
	row = frappe.db.get_value(
		"Lead",
		lead,
		[
			"mobile_no",
			"whatsapp_no",
			"phone",
			"tbo_chat_contact_id",
			"tbo_chat_conversation_id",
			"tbo_whatsapp_last_in",
		],
		as_dict=True,
	)
	number = whatsapp_number(row)
	return {
		"to": f"+{number}" if number else None,
		"conversation_id": row.tbo_chat_conversation_id or None,
		"contact_id": row.tbo_chat_contact_id or None,
		"last_in": row.tbo_whatsapp_last_in,
		"window_open": window_open(row.tbo_whatsapp_last_in),
		"window_ends": window_ends(row.tbo_whatsapp_last_in),
		"blocked": never_contact(lead) or (None if number else _("The lead has no usable mobile number.")),
	}


# ---------- sending: the queue the hub works through ----------


def queue_for_chat(doc) -> None:
	"""sending.DELIVERERS[WhatsApp]: the message waits for the hub to send it through TBO Chat."""
	doc.via_chat, doc.chat_status = 1, QUEUED
	doc.save(ignore_permissions=True)


def first_name_of(lead_name: str | None) -> str:
	return (lead_name or "").split()[0] if (lead_name or "").strip() else ""


def pending(country: str | None = None, limit: int = QUEUE_LIMIT) -> list[dict]:
	"""Approved WhatsApp messages waiting for TBO Chat, oldest approval first, during the send
	window only. Each is claimed (Sending) so a second call never hands it out again. A message
	whose lead unsubscribed or was lost meanwhile is cancelled instead of handed out."""
	if not in_window():
		return []
	country = country or INDIA
	names = frappe.get_all(
		MESSAGE,
		filters={"channel": WHATSAPP, "status": APPROVED, "via_chat": 1, "chat_status": QUEUED},
		order_by="approved_on asc",
		limit=cint(limit) or QUEUE_LIMIT,
		pluck="name",
	)
	rows = []
	for name in names:
		doc = frappe.get_doc(MESSAGE, name)
		if reason := never_contact(doc.lead):
			doc.status, doc.error, doc.chat_status = CANCELLED, reason, ""
			doc.save(ignore_permissions=True)
			continue
		lead = frappe.db.get_value(
			"Lead",
			doc.lead,
			[
				"lead_name",
				"company_name",
				"mobile_no",
				"whatsapp_no",
				"phone",
				"tbo_web_profile",
				"tbo_chat_contact_id",
				"tbo_chat_conversation_id",
				"tbo_whatsapp_last_in",
			],
			as_dict=True,
		)
		number = whatsapp_number(lead, country)
		if not number:
			doc.status, doc.error, doc.chat_status = FAILED, _("The lead has no usable mobile number."), ""
			doc.save(ignore_permissions=True)
			continue
		doc.chat_status = SENDING
		doc.save(ignore_permissions=True)
		rows.append(
			{
				"name": doc.name,
				"lead": doc.lead,
				"to": f"+{number}",
				"body": doc.body,
				"lead_name": lead.lead_name,
				"first_name": first_name_of(lead.lead_name),
				"company_name": lead.company_name,
				"why_call": (prospect_of(lead.tbo_web_profile) or {}).get("why_call"),
				"contact_id": lead.tbo_chat_contact_id or None,
				"conversation_id": lead.tbo_chat_conversation_id or None,
				"window_open": window_open(lead.tbo_whatsapp_last_in),
			}
		)
	return rows


def report(
	name: str,
	state: str,
	text: str | None = None,
	contact_id: str | None = None,
	conversation_id: str | None = None,
	message_id: str | None = None,
	template: str | None = None,
	error: str | None = None,
) -> dict:
	"""The hub's answer for a message it took from the queue. `state`:
	- "sent": TBO Chat accepted it. The message is Sent, `text` (what really went, a template
	  filled in or the free text) is filed on the lead and the ids are kept on the lead.
	- "failed": it could not be sent; the message is Failed with `error` for a person to fix.
	- "manual": TBO Chat is not ready (no inbox, no template); the message stays Approved and the
	  BD sends it by hand, as before."""
	doc = frappe.get_doc(MESSAGE, name)
	if doc.channel != WHATSAPP or doc.status != APPROVED or doc.chat_status not in (SENDING, QUEUED):
		return {"name": name, "status": doc.status, "message": _("It is not waiting for TBO Chat.")}
	if state == "sent":
		doc.status, doc.chat_status, doc.sent_on, doc.error = SENT, SENT, now_datetime(), None
		doc.chat_message_id, doc.template = message_id, template
		doc.communication = file_outgoing(doc.lead, text or doc.body, message_id, doc.approved_by).name
		remember_chat(doc.lead, contact_id, conversation_id)
	elif state == "failed":
		doc.status, doc.chat_status, doc.error = (
			FAILED,
			FAILED,
			(error or _("TBO Chat could not send it."))[:300],
		)
	elif state == "manual":
		doc.via_chat, doc.chat_status = 0, ""
	else:
		frappe.throw(_("state must be sent, failed or manual."))
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status, "message": doc.error or ""}


def apply_status(message_id: str, status: str, error: str | None = None) -> dict:
	"""A delivery receipt from TBO Chat for one of our messages: sent, delivered, read or failed.
	An older receipt never undoes a newer one. Returns {matched, status}."""
	name = frappe.db.get_value(MESSAGE, {"channel": WHATSAPP, "chat_message_id": message_id}, "name")
	if not name:
		return {"matched": False, "status": None}
	doc = frappe.get_doc(MESSAGE, name)
	status = (status or "").strip().lower()
	if status == "failed":
		doc.status, doc.chat_status = FAILED, FAILED
		doc.error = (error or _("WhatsApp could not deliver it."))[:300]
	elif status in RECEIPTS and RANK[RECEIPTS[status]] > RANK.get(doc.chat_status, 0):
		doc.chat_status = RECEIPTS[status]
	else:
		return {"matched": True, "status": doc.chat_status}
	doc.save(ignore_permissions=True)
	return {"matched": True, "status": doc.chat_status}


# ---------- the lead's timeline ----------


def remember_chat(lead: str, contact_id, conversation_id) -> None:
	"""Keep TBO Chat's ids on the lead so the next message and the link find the conversation."""
	values = {}
	if contact_id:
		values["tbo_chat_contact_id"] = str(contact_id)
	if conversation_id:
		values["tbo_chat_conversation_id"] = str(conversation_id)
	if values:
		frappe.db.set_value("Lead", lead, values, update_modified=False)


def file_message(
	lead: str, text: str, received: bool, message_id=None, at=None, who: str | None = None, name=None
):
	"""A WhatsApp message as a Communication on the lead (medium Chat). Filed once per chat
	message id."""
	message_key = f"{CHAT_MESSAGE_PREFIX}{message_id}" if message_id else None
	if message_key and (
		existing := frappe.db.get_value(
			"Communication", {"reference_doctype": "Lead", "message_id": message_key}
		)
	):
		return frappe.get_doc("Communication", existing)
	row = frappe.db.get_value("Lead", lead, ["lead_name", "mobile_no"], as_dict=True)
	return frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": MEDIUM,
			"sent_or_received": "Received" if received else "Sent",
			"subject": SUBJECT,
			"content": escape_html(text or "").replace("\n", "<br>"),
			"sender": row.lead_name if received else (who or frappe.session.user),
			"sender_full_name": name or (row.lead_name if received else frappe.utils.get_fullname(who)),
			"phone_no": row.mobile_no if received else None,
			"reference_doctype": "Lead",
			"reference_name": lead,
			"message_id": message_key,
			"communication_date": get_datetime(at) if at else now_datetime(),
		}
	).insert(ignore_permissions=True)


def file_outgoing(lead: str, text: str, message_id=None, who: str | None = None):
	return file_message(lead, text, False, message_id, who=who)


def find_lead(phone: str | None, conversation_id=None) -> str | None:
	"""The lead a chat belongs to: the one that holds the conversation, else the most recently
	changed lead whose mobile, WhatsApp or phone number ends in the same ten digits."""
	if conversation_id and (
		lead := frappe.db.get_value("Lead", {"tbo_chat_conversation_id": str(conversation_id)}, "name")
	):
		return lead
	digits = normalise_phone(phone)
	if not digits:
		return None
	tail = digits[-MATCH_DIGITS:]
	rows = frappe.db.sql(
		"""select name from `tabLead`
		where right(regexp_replace(ifnull(mobile_no, ''), '[^0-9]', ''), %(n)s) = %(tail)s
			or right(regexp_replace(ifnull(whatsapp_no, ''), '[^0-9]', ''), %(n)s) = %(tail)s
			or right(regexp_replace(ifnull(phone, ''), '[^0-9]', ''), %(n)s) = %(tail)s
		order by modified desc limit 1""",
		{"n": MATCH_DIGITS, "tail": tail},
	)
	return rows[0][0] if rows else None


def receive(
	phone: str,
	text: str,
	conversation_id=None,
	contact_id=None,
	message_id=None,
	at=None,
	sender_name: str | None = None,
) -> dict:
	"""A WhatsApp message from someone TBO Chat says wrote to us. When the number or the
	conversation belongs to a lead it is filed on the lead, opens the 24-hour window and is queued
	for the reply sorter. Returns {matched, lead, communication}; `matched` false means the chat
	is not a lead's (the hub treats it as support)."""
	lead = find_lead(phone, conversation_id)
	if not lead or not (text or "").strip():
		return {"matched": bool(lead), "lead": lead, "communication": None}
	remember_chat(lead, contact_id, conversation_id)
	moment = get_datetime(at) if at else now_datetime()
	last_in = frappe.db.get_value("Lead", lead, "tbo_whatsapp_last_in")
	if not last_in or moment > get_datetime(last_in):
		frappe.db.set_value("Lead", lead, "tbo_whatsapp_last_in", moment, update_modified=False)
	doc = file_message(lead, text, True, message_id, moment, name=sender_name)
	return {"matched": True, "lead": lead, "communication": doc.name}


def log_sent(
	lead: str,
	text: str,
	message_id=None,
	contact_id=None,
	conversation_id=None,
	who: str | None = None,
):
	"""A message the BD sent from the lead page through TBO Chat, filed on the lead as theirs."""
	remember_chat(lead, contact_id, conversation_id)
	return file_outgoing(lead, text, message_id, who)


def is_stop_request(text: str) -> bool:
	"""A WhatsApp reply that is only a request to stop. No quoted opt-out line exists on WhatsApp,
	so a plain match is safe here (it is not for email)."""
	return " ".join((text or "").lower().replace(".", " ").replace("!", " ").split()) in STOP_WORDS
