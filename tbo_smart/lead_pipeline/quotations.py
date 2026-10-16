"""Quotations on the lead (docs/lead-pipeline.md §9).

A lead's quotation is an ordinary ERPNext Quotation made out to the CRM Lead (*Quotation To* =
CRM Lead) with `tbo_crm_lead` set and `crm_deal` empty, so the discount/commission approval
(tbo_smart.quotation_control) and every ERPNext rule apply as on the desk. When the lead
becomes Closed – Won and gets its deal, the lead's quotations are re-linked to the deal
(`relink_to_deal`), and the deal's Finance tab and the Sales Order carry on from there.

`tbo_quote_status` tracks where the quotation is with the client. The draft states (Draft,
Pending Approval, Approved) follow the approval record; the rest are set by people (mark sent,
viewed, under negotiation, revision required, accepted, rejected) or by the nightly expiry.
Every change is written with a Version row and a timeline comment.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from tbo_smart.finance.stage import write_tracked
from tbo_smart.lead_pipeline import quotations_enabled, setting
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CLOSED_WON, NEGOTIATION, PROPOSAL_SENT
from tbo_smart.lead_pipeline.transitions import advance_lead

DRAFT, PENDING, APPROVED = "Draft", "Pending Approval", "Approved"
SENT, VIEWED, NEGOTIATING = "Sent to Client", "Viewed / Acknowledged", "Under Negotiation"
REVISION, ACCEPTED, REJECTED, EXPIRED = "Revision Required", "Accepted", "Rejected", "Expired"
# with the client: a person moves the quotation between these
WITH_CLIENT = (SENT, VIEWED, NEGOTIATING, REVISION)
CLOSED = (ACCEPTED, REJECTED, EXPIRED)
# a person may set these by hand, from the statuses in the first column
MANUAL = {
	VIEWED: (SENT,),
	NEGOTIATING: (SENT, VIEWED, REVISION),
	REVISION: (SENT, VIEWED, NEGOTIATING),
	ACCEPTED: (SENT, VIEWED, NEGOTIATING, REVISION),
	REJECTED: (SENT, VIEWED, NEGOTIATING, REVISION),
}
# how a quotation moves its lead (forward only, transitions.advance_lead)
LEAD_MOVES = {SENT: PROPOSAL_SENT, NEGOTIATING: NEGOTIATION, ACCEPTED: CLOSED_WON}
OPEN_REQUEST = ("Pending", "Escalated")
GRANTED = ("Approved", "Overridden")
SEND_VIA = ("Email", "WhatsApp", "Manual")
DEFAULT_VALIDITY = 30
DEFAULT_FOLLOW_UP_DAYS = 3


def is_lead_quotation(doc) -> bool:
	return bool(doc.get("tbo_crm_lead"))


def validity_days() -> int:
	return cint(setting("quotation_validity_days")) or DEFAULT_VALIDITY


# ---------- the draft states, from the approval record ----------


def draft_status(doc) -> str:
	"""Pending Approval while a request is open; Approved once a granted, not voided approval
	covers it and nothing is above the limit; else Draft."""
	from tbo_smart.quotation_control import QuotationControl

	open_request = frappe.db.exists(
		"TBO Quotation Adjustment", {"quotation": doc.name, "status": ("in", OPEN_REQUEST), "voided": 0}
	)
	if open_request:
		return PENDING
	granted = frappe.db.exists(
		"TBO Quotation Adjustment", {"quotation": doc.name, "status": ("in", GRANTED), "voided": 0}
	)
	if granted and not QuotationControl(doc).uncovered():
		return APPROVED
	return DRAFT


def sync_status(doc) -> None:
	"""Keep a lead quotation's draft state in step (on save, submit and approval decisions).
	A submitted quotation that hasn't gone out yet is Approved: ready to send."""
	if not is_lead_quotation(doc) or doc.docstatus == 2:
		return
	current = doc.get("tbo_quote_status")
	if doc.docstatus == 0:
		target = draft_status(doc)
	elif current in (None, "", DRAFT, PENDING):
		target = APPROVED
	else:
		return
	if target != current:
		write_tracked(doc, {"tbo_quote_status": target})


def quotation_changed(doc, method=None) -> None:
	"""doc_events: Quotation on_update and on_submit."""
	if quotations_enabled():
		sync_status(doc)


def adjustment_changed(doc, method=None) -> None:
	"""doc_events: TBO Quotation Adjustment on_update: a request or decision changes the
	quotation's draft state."""
	if quotations_enabled() and doc.quotation and frappe.db.exists("Quotation", doc.quotation):
		sync_status(frappe.get_doc("Quotation", doc.quotation))


# ---------- people moving the status ----------


def set_status(doc, status: str, note: str | None = None) -> None:
	"""A person's change, checked against MANUAL; the lead follows (LEAD_MOVES)."""
	if status not in MANUAL:
		frappe.throw(_("{0} can't be set by hand.").format(status))
	if doc.docstatus != 1 or doc.get("tbo_quote_status") not in MANUAL[status]:
		frappe.throw(
			_("A quotation that is {0} can't become {1}.").format(
				_(doc.get("tbo_quote_status") or DRAFT), _(status)
			)
		)
	comment = _("Quote status: {0}").format(status) + (f": {note.strip()}" if (note or "").strip() else "")
	write_tracked(doc, {"tbo_quote_status": status}, comment)
	move_lead(doc, status)


def move_lead(doc, status: str) -> None:
	target = LEAD_MOVES.get(status)
	if target:
		advance_lead(doc.tbo_crm_lead, target, _("quotation {0} {1}").format(doc.name, status.lower()))


def mark_sent(doc, via: str, follow_up_days: int | None = None) -> str | None:
	"""Record that the quotation went to the client. A draft is submitted first, so the
	discount/commission control refuses it while an approval is missing. Returns the follow-up
	task."""
	if via not in SEND_VIA:
		frappe.throw(_("Sent via Email, WhatsApp or Manual."))
	if doc.docstatus == 0:
		if draft_status(doc) == PENDING:
			frappe.throw(_("This quotation is waiting for approval. It can be sent once it is approved."))
		doc.submit()
	elif doc.docstatus == 2 or doc.get("tbo_quote_status") in CLOSED:
		frappe.throw(
			_("A quotation that is {0} can't be sent.").format(_(doc.get("tbo_quote_status") or "Cancelled"))
		)
	write_tracked(
		doc,
		{"tbo_quote_status": SENT, "tbo_sent_via": via, "tbo_sent_on": now_datetime()},
		_("Quote status: {0} ({1})").format(SENT, via),
	)
	move_lead(doc, SENT)
	return schedule_follow_up(doc, follow_up_days)


def schedule_follow_up(doc, days: int | None) -> str | None:
	from tbo_smart.api.mobile.crm import add_follow_up_task
	from tbo_smart.smart_leads import FOLLOW_UP_TYPE

	days = (
		cint(days)
		if days is not None
		else (cint(setting("quotation_follow_up_days")) or DEFAULT_FOLLOW_UP_DAYS)
	)
	if days <= 0:
		return None
	owner = frappe.db.get_value("CRM Lead", doc.tbo_crm_lead, "lead_owner") or frappe.session.user
	task = add_follow_up_task(
		"CRM Lead",
		doc.tbo_crm_lead,
		f"{add_days(today(), days)} 10:00:00",
		title=_("Follow up on quotation {0}").format(doc.name),
		assigned_to=owner,
		task_type=FOLLOW_UP_TYPE,
	)
	return task.name


def expire_quotations() -> int:
	"""Daily: lead quotations still out with the client after their valid till date expire."""
	if not quotations_enabled():
		return 0
	names = frappe.get_all(
		"Quotation",
		filters={
			"tbo_crm_lead": ("is", "set"),
			"docstatus": 1,
			"tbo_quote_status": ("in", (APPROVED, *WITH_CLIENT)),
			"valid_till": ("<", today()),
		},
		pluck="name",
		limit_page_length=0,
	)
	for name in names:
		write_tracked(
			frappe.get_doc("Quotation", name),
			{"tbo_quote_status": EXPIRED},
			_("Quote status: {0}").format(EXPIRED),
		)
	return len(names)


# ---------- revisions ----------


def family_of(doc) -> str:
	return doc.get("tbo_quote_family") or doc.name


def create_revision(doc):
	"""A new draft copied from this quotation (lines, terms, taxes, discount) with the next
	revision number in its family; it becomes the latest, and a quotation that was with the
	client is marked Revision Required."""
	family = family_of(doc)
	members = frappe.get_all(
		"Quotation",
		filters={"tbo_quote_family": family},
		fields=["name", "tbo_revision_no"],
		limit_page_length=0,
	)
	revision = frappe.copy_doc(doc)
	revision.update(
		{
			"tbo_quote_family": family,
			"tbo_revision_no": max(
				[cint(row.tbo_revision_no) for row in members] + [cint(doc.get("tbo_revision_no"))]
			)
			+ 1,
			"tbo_quote_status": DRAFT,
			"tbo_is_latest": 1,
			# copy_doc keeps no_copy fields too; a revision hasn't been sent
			"tbo_sent_via": None,
			"tbo_sent_on": None,
			"transaction_date": today(),
			"valid_till": add_days(today(), validity_days()),
			"amended_from": None,
			# copy_doc keeps a submitted source's docstatus; a revision starts as a draft
			"docstatus": 0,
		}
	)
	revision.insert()
	for row in members:
		if row.name != revision.name:
			frappe.db.set_value("Quotation", row.name, "tbo_is_latest", 0, update_modified=False)
	if doc.get("tbo_quote_status") in WITH_CLIENT and doc.tbo_quote_status != REVISION:
		write_tracked(doc, {"tbo_quote_status": REVISION}, _("Revised as {0}").format(revision.name))
	return revision


def start_family(doc) -> None:
	"""A new lead quotation starts its own family, revision 0, the latest."""
	write_tracked(doc, {"tbo_quote_family": doc.name, "tbo_revision_no": 0, "tbo_is_latest": 1})


def get_revisions(doc) -> list[dict]:
	"""Every quotation of the family, oldest first, each with what changed from the previous one."""
	names = frappe.get_all(
		"Quotation",
		filters={"tbo_quote_family": family_of(doc)},
		pluck="name",
		order_by="tbo_revision_no asc, creation asc",
		limit_page_length=0,
	) or [doc.name]
	docs = [frappe.get_doc("Quotation", name) for name in names]
	rows = []
	for index, current in enumerate(docs):
		rows.append(
			{
				"name": current.name,
				"revision": cint(current.get("tbo_revision_no")),
				"status": current.get("tbo_quote_status"),
				"docstatus": current.docstatus,
				"is_latest": cint(current.get("tbo_is_latest")),
				"transaction_date": current.transaction_date,
				"valid_till": current.valid_till,
				"currency": current.currency,
				"grand_total": flt(current.grand_total),
				"discount_percent": discount_percent(current),
				"changes": diff(docs[index - 1], current) if index else None,
			}
		)
	return rows


def discount_percent(doc) -> float:
	from tbo_smart.quotation_control import get_discount_percent

	return flt(get_discount_percent(doc), 2)


def diff(before, after) -> dict:
	"""Price, discount, lines and terms changed between two revisions."""
	lines_before = {row.item_code: row for row in before.items}
	lines_after = {row.item_code: row for row in after.items}
	changed = [
		{
			"item_code": code,
			"item_name": row.item_name,
			"qty": [flt(lines_before[code].qty), flt(row.qty)],
			"rate": [flt(lines_before[code].rate), flt(row.rate)],
		}
		for code, row in lines_after.items()
		if code in lines_before
		and (flt(lines_before[code].qty) != flt(row.qty) or flt(lines_before[code].rate) != flt(row.rate))
	]
	return {
		"grand_total": [flt(before.grand_total), flt(after.grand_total)],
		"discount_percent": [discount_percent(before), discount_percent(after)],
		"added": [lines_after[code].item_name or code for code in lines_after if code not in lines_before],
		"removed": [lines_before[code].item_name or code for code in lines_before if code not in lines_after],
		"changed": changed,
		"terms_changed": (before.get("terms") or "") != (after.get("terms") or ""),
		"valid_till": [before.valid_till, after.valid_till],
	}


# ---------- the deal ----------


def relink_to_deal(lead: str, deal: str) -> list[str]:
	"""At Closed – Won: the lead's quotations without a deal now belong to it (Frappe CRM's
	`crm_deal`, and *Quotation To* CRM Deal for those made out to the lead), so the deal's Finance
	tab lists them and ERPNext finds the deal's customer for the Sales Order."""
	names = frappe.get_all(
		"Quotation",
		filters={"tbo_crm_lead": lead, "docstatus": ("!=", 2)},
		pluck="name",
		limit_page_length=0,
	)
	for name in names:
		doc = frappe.get_doc("Quotation", name)
		values = {}
		if doc.meta.has_field("crm_deal") and not doc.get("crm_deal"):
			values["crm_deal"] = deal
		if doc.quotation_to == "CRM Lead":
			values.update({"quotation_to": "CRM Deal", "party_name": deal})
		if values:
			write_tracked(doc, values, _("Linked to deal {0}").format(deal))
	return names


def make_sales_order(doc) -> str:
	"""A draft Sales Order from an accepted quotation, through ERPNext's own mapper, once the
	deal's customer exists (Frappe CRM creates it). The finance workflow's pending-approval
	guard applies on its validate."""
	from crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings import check_customer_for_deal
	from erpnext.selling.doctype.quotation.quotation import make_sales_order as erpnext_sales_order

	if doc.get("tbo_quote_status") != ACCEPTED or doc.docstatus != 1:
		frappe.throw(_("Only an accepted quotation becomes a Sales Order."))
	deal = doc.get("crm_deal")
	if not deal:
		frappe.throw(_("The lead has no deal yet: set it to {0} first.").format(CLOSED_WON))
	if not check_customer_for_deal(deal):
		frappe.throw(
			_("The deal has no ERPNext customer. Turn on ERPNext CRM Settings so the CRM can create it.")
		)
	order = erpnext_sales_order(doc.name)
	for row in order.items:
		row.delivery_date = row.delivery_date or getdate(today())
	order.delivery_date = order.delivery_date or getdate(today())
	order.insert()
	return order.name


def close_lead_lost(doc, lost_reason: str) -> None:
	from tbo_smart.api.mobile.crm import apply_lead_status

	apply_lead_status(doc.tbo_crm_lead, CLOSED_LOST, lost_reason)
