"""Approving and delivering outreach messages (docs/prospecting.md §10).

Nothing leaves without a person's approval. Approving an email checks the rules in `guards`
(unsubscribed, lost, the daily cap, the send window) and sends it, or leaves it Approved for the
10-minute sender to deliver when the window and the cap allow. WhatsApp has no API yet: approving
marks the message ready, the hub opens wa.me for the BD and the BD confirms "Sent" by hand.
`DELIVERERS` is where a future WhatsApp Cloud API plugs in: add `WHATSAPP: send_whatsapp` and
approving a WhatsApp message will deliver it like an email.
"""

from email.utils import formataddr

import frappe
from frappe import _
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.email_body import get_message_id
from frappe.utils import escape_html, get_fullname, get_string_between, now_datetime

from tbo_smart.outreach import (
	APPROVED,
	CANCELLED,
	DRAFT,
	EMAIL,
	FAILED,
	MESSAGE,
	OPEN,
	OPT_OUT,
	SENT,
	WHATSAPP,
	enabled,
)
from tbo_smart.outreach.guards import blocked_reason, cap_left, in_window, never_contact, next_window
from tbo_smart.sales.activities import add_log

SEND_BATCH = 100


def outgoing_account():
	"""The ERP's default outgoing Email Account, or None."""
	return EmailAccount.find_outgoing()


def result(doc, message: str = "") -> dict:
	return {"name": doc.name, "status": doc.status, "message": message}


def load(name: str):
	"""The message, when the session user may write to its lead."""
	doc = frappe.get_doc(MESSAGE, name)
	frappe.has_permission("Lead", "write", doc.lead, throw=True)
	return doc


def refuse(name: str, message: str) -> dict:
	return {"name": name, "status": None, "message": message}


# ---------- editing and withdrawing ----------


def update(name: str, subject: str | None, body: str) -> dict:
	"""A person's edit of a draft (or of a failed message, which becomes a draft again)."""
	doc = load(name)
	if doc.status not in (DRAFT, FAILED):
		frappe.throw(_("Only a draft can be edited."))
	body = (body or "").strip()
	if not body:
		frappe.throw(_("The message is empty."))
	if doc.channel == EMAIL and not (subject or "").strip():
		frappe.throw(_("An email needs a subject."))
	doc.body = body
	doc.subject = (subject or "").strip() if doc.channel == EMAIL else None
	if doc.status == FAILED:
		doc.status, doc.error = DRAFT, None
	doc.save(ignore_permissions=True)
	return result(doc)


def cancel(names: list[str]) -> list[dict]:
	"""Withdraw drafts, approved and failed messages. A sent message cannot be withdrawn."""
	results = []
	for name in names:
		try:
			doc = load(name)
		except (frappe.PermissionError, frappe.DoesNotExistError):
			results.append(refuse(name, _("You cannot change this message.")))
			continue
		if doc.status not in OPEN:
			results.append(result(doc, _("It is {0}, so it cannot be cancelled.").format(_(doc.status))))
			continue
		doc.status = CANCELLED
		doc.save(ignore_permissions=True)
		results.append(result(doc))
	return results


def cancel_pending(lead: str, reason: str) -> int:
	"""Cancel the lead's drafts and approved messages (it unsubscribed), saying why."""
	names = frappe.get_all(MESSAGE, filters={"lead": lead, "status": ("in", (DRAFT, APPROVED))}, pluck="name")
	for name in names:
		frappe.db.set_value(MESSAGE, name, {"status": CANCELLED, "error": reason})
	return len(names)


# ---------- approving ----------


def approve(names: list[str], user: str) -> list[dict]:
	"""Approve drafts as `user`, who must be able to write to each lead. Emails are then sent at
	once when the window and the daily cap allow, else left for the sender. Fails as a whole when
	an email is among them and there is no outgoing Email Account."""
	docs, results = [], []
	for name in names:
		try:
			docs.append(load(name))
		except (frappe.PermissionError, frappe.DoesNotExistError):
			results.append(refuse(name, _("You cannot change this message.")))
	if any(doc.channel == EMAIL for doc in docs) and not outgoing_account():
		frappe.throw(_("Set up an outgoing email account first."))
	for doc in docs:
		results.append(approve_one(doc, user))
	return results


def approve_one(doc, user: str) -> dict:
	if doc.status != DRAFT:
		return result(doc, _("It is {0}, so it cannot be approved.").format(_(doc.status)))
	if not (doc.body or "").strip() or (doc.channel == EMAIL and not (doc.subject or "").strip()):
		return result(doc, _("Write the message first."))
	if reason := never_contact(doc.lead):
		doc.status, doc.error = CANCELLED, reason
		doc.save(ignore_permissions=True)
		return result(doc, reason)
	if reason := blocked_reason(doc.lead, doc.channel):
		return result(doc, reason)  # a missing address is for the person to fix
	doc.status, doc.approved_by, doc.approved_on = APPROVED, user, now_datetime()
	doc.save(ignore_permissions=True)
	if doc.channel not in DELIVERERS:
		return result(doc, _("Ready: open WhatsApp, send it, then mark it sent."))
	return deliver(doc)


def deliver(doc) -> dict:
	"""Send an approved message if the window is open and its approver has sends left today."""
	if not in_window():
		return result(
			doc, _("Approved. It will go out from {0}.").format(frappe.utils.format_datetime(next_window()))
		)
	if cap_left(doc.approved_by) <= 0:
		return result(doc, _("Approved. Today's limit is reached, so it goes out on the next working day."))
	DELIVERERS[doc.channel](doc)
	return result(doc, doc.error or "")


# ---------- delivering ----------


def message_html(body: str) -> str:
	"""The body as simple HTML, with the opt-out line under it."""
	paragraphs = [part.strip() for part in body.strip().split("\n\n") if part.strip()]
	html = "".join(f"<p>{escape_html(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)
	return html + f'<p style="color:#6b7280;font-size:12px">{escape_html(_(OPT_OUT))}</p>'


def send_email(doc) -> None:
	"""Send the email through the default outgoing account. A Communication on the Lead records it
	(and is what a reply threads to); the mail itself is queued by frappe.sendmail. A failure is
	kept on the message, not raised, so one bad address never stops the rest."""
	status = frappe.db.get_value(MESSAGE, doc.name, "status", for_update=True)
	if status != APPROVED:
		return
	communication = None
	if reason := never_contact(doc.lead):
		doc.status, doc.error = CANCELLED, reason
		doc.save(ignore_permissions=True)
		return
	try:
		account = outgoing_account()
		if not account:
			frappe.throw(_("Set up an outgoing email account first."))
		if reason := blocked_reason(doc.lead, EMAIL):
			frappe.throw(reason)
		html = message_html(doc.body)
		sender = formataddr((get_fullname(doc.approved_by) or "", account.email_id))
		message_id = get_string_between("<", get_message_id(), ">")
		communication = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"delivery_status": "Sending",
				"subject": doc.subject,
				"content": html,
				"sender": sender,
				"sender_full_name": get_fullname(doc.approved_by),
				"recipients": doc.recipient or frappe.db.get_value("Lead", doc.lead, "email_id"),
				"reference_doctype": "Lead",
				"reference_name": doc.lead,
				"message_id": message_id,
			}
		).insert(ignore_permissions=True)
		frappe.sendmail(
			recipients=[communication.recipients],
			sender=sender,
			subject=doc.subject,
			message=html,
			reference_doctype="Lead",
			reference_name=doc.lead,
			communication=communication.name,
			message_id=message_id,
		)
	except Exception as error:
		doc.status, doc.error = FAILED, str(error)[:300]
		frappe.log_error(title=f"Outreach: email to {doc.lead} failed")
		if communication:
			communication.db_set("delivery_status", "Error")
	else:
		doc.status, doc.sent_on, doc.communication, doc.error = SENT, now_datetime(), communication.name, None
	doc.save(ignore_permissions=True)


DELIVERERS = {EMAIL: send_email}


def send_approved() -> None:
	"""Scheduler, every 10 minutes: deliver approved emails while the window is open, each approver
	within their daily cap, oldest approval first."""
	if not enabled() or not in_window() or not outgoing_account():
		return
	names = frappe.get_all(
		MESSAGE,
		filters={"status": APPROVED, "channel": ("in", list(DELIVERERS))},
		order_by="approved_on asc",
		limit=SEND_BATCH,
		pluck="name",
	)
	for name in names:
		doc = frappe.get_doc(MESSAGE, name)
		if cap_left(doc.approved_by) <= 0:
			continue
		DELIVERERS[doc.channel](doc)
		frappe.db.commit()  # nosemgrep


# ---------- WhatsApp by hand ----------


def mark_sent(names: list[str]) -> list[dict]:
	"""The BD sent these WhatsApp messages from their own phone: log them on the lead as theirs."""
	results = []
	for name in names:
		try:
			doc = load(name)
		except (frappe.PermissionError, frappe.DoesNotExistError):
			results.append(refuse(name, _("You cannot change this message.")))
			continue
		if doc.channel != WHATSAPP or doc.status != APPROVED:
			results.append(result(doc, _("Only an approved WhatsApp message can be marked sent.")))
			continue
		doc.status, doc.sent_on = SENT, now_datetime()
		doc.save(ignore_permissions=True)
		add_log("Lead", doc.lead, "whatsapp", doc.body)
		results.append(result(doc))
	return results
