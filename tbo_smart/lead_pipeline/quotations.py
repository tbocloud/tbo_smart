"""Quotations on the lead (docs/lead-pipeline.md §9).

A lead's quotation is an ordinary ERPNext Quotation made out to the Lead (*Quotation To* = Lead,
*Party* = the lead), so the discount/commission approval (tbo_smart.quotation_control) and every
ERPNext rule apply as on the desk. When the lead becomes Closed – Won and gets its deal, the
lead's quotations are linked to the deal (ERPNext's own `opportunity`, `relink_to_deal`), and the
deal's Finance and the Sales Order carry on from there; ERPNext makes the Customer from the lead
when the order is made.

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
from tbo_smart.sales import LEAD_LINK
from tbo_smart.sales.activities import add_follow_up_task
from tbo_smart.sales.opportunity import customer_of
from tbo_smart.sales.stages import set_lead_stage

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


def lead_of(doc) -> str | None:
	"""The lead a quotation belongs to: the one it was made for (`tbo_lead`), or the lead its deal
	was made from (a won lead's quotation may be made out to the client's Customer)."""
	if doc.get("quotation_to") == "Lead":
		return doc.get("party_name")
	if doc.get(LEAD_LINK):
		return doc.get(LEAD_LINK)
	if doc.get("opportunity"):
		return frappe.db.get_value("Opportunity", doc.opportunity, LEAD_LINK)
	return None


def lead_quotations(lead: str, fields: list, filters: dict | None = None) -> list:
	"""The lead's quotations: made for it, or linked to its deal."""
	from tbo_smart.sales.opportunity import lead_opportunity

	deal = lead_opportunity(lead)
	or_filters = {LEAD_LINK: lead}
	if deal:
		or_filters["opportunity"] = deal
	return frappe.get_all(
		"Quotation",
		filters={"docstatus": ("!=", 2), **(filters or {})},
		or_filters=or_filters,
		fields=list(dict.fromkeys(["quotation_to", "opportunity", *fields])),
		order_by="creation desc",
		limit_page_length=0,
	)


def is_lead_quotation(doc) -> bool:
	return bool(lead_of(doc))


def validity_days() -> int:
	return cint(setting("quotation_validity_days")) or DEFAULT_VALIDITY


# ---------- the draft states, from the approval record ----------


def draft_status(doc) -> str:
	"""Pending Approval while a request is open; Approved once a granted, not voided approval
	covers it and nothing is above the limit; else Draft."""
	return draft_states([doc.name], {doc.name: doc})[doc.name]


def draft_states(names: list[str], docs: dict | None = None) -> dict[str, str]:
	"""{quotation: Draft / Pending Approval / Approved} for drafts, in one query; a quotation is
	loaded only when it has a request or a grant to check against its current figures. An open
	request no longer counts once nothing is above the limit (the discount was lowered)."""
	from tbo_smart.quotation_control import QuotationControl

	docs = dict(docs or {})
	rows = frappe.get_all(
		"TBO Quotation Adjustment",
		filters={
			"quotation": ("in", names or [""]),
			"voided": 0,
			"status": ("in", (*OPEN_REQUEST, *GRANTED)),
		},
		fields=["quotation", "status", "within_limit"],
		limit_page_length=0,
	)
	open_requests = {row.quotation for row in rows if row.status in OPEN_REQUEST}
	# an adjustment the person applied within their own limit is not an approval
	granted = {row.quotation for row in rows if row.status in GRANTED and not row.within_limit}
	states = {}
	for name in names:
		if name not in open_requests and name not in granted:
			states[name] = DRAFT
			continue
		uncovered = QuotationControl(docs.get(name) or frappe.get_doc("Quotation", name)).uncovered()
		if name in open_requests and uncovered:
			states[name] = PENDING
		elif name in granted and not uncovered:
			states[name] = APPROVED
		else:
			states[name] = DRAFT
	return states


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
		advance_lead(lead_of(doc), target, _("quotation {0} {1}").format(doc.name, status.lower()))


def mark_sent(doc, via: str, follow_up_days: int | None = None) -> str | None:
	"""Record that the quotation went to the client. A draft is submitted first, so the
	discount/commission control refuses it while an approval is missing. Returns the follow-up
	task."""
	if via not in SEND_VIA:
		frappe.throw(_("Sent via Email, WhatsApp or Manual."))
	if not cint(doc.get("tbo_is_latest")):
		frappe.throw(_("Only the latest revision of a quotation can be sent."))
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
	from tbo_smart.smart_leads import FOLLOW_UP_TYPE

	days = (
		cint(days)
		if days is not None
		else (cint(setting("quotation_follow_up_days")) or DEFAULT_FOLLOW_UP_DAYS)
	)
	if days <= 0:
		return None
	owner = frappe.db.get_value("Lead", lead_of(doc), "lead_owner") or frappe.session.user
	task = add_follow_up_task(
		"Lead",
		lead_of(doc),
		add_days(today(), days),
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
			"description_changed": (lines_before[code].description or "") != (row.description or ""),
		}
		for code, row in lines_after.items()
		if code in lines_before
		and (
			flt(lines_before[code].qty) != flt(row.qty)
			or flt(lines_before[code].rate) != flt(row.rate)
			or (lines_before[code].description or "") != (row.description or "")
		)
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
	"""At Closed – Won: the lead's quotations without a deal now belong to it (ERPNext's
	`opportunity`), so the deal's Finance lists them."""
	names = frappe.get_all(
		"Quotation",
		filters={LEAD_LINK: lead, "docstatus": ("!=", 2)},
		fields=["name", "opportunity"],
		limit_page_length=0,
	)
	for row in names:
		if not row.opportunity:
			write_tracked(
				frappe.get_doc("Quotation", row.name),
				{"opportunity": deal},
				_("Linked to deal {0}").format(deal),
			)
	return [row.name for row in names]


def make_sales_order(doc) -> str:
	"""A draft Sales Order from an accepted quotation of a won lead, through ERPNext's own mapper,
	which makes the Customer from the lead when it has none. The finance workflow's
	pending-approval guard applies on its validate."""
	from erpnext.selling.doctype.quotation.quotation import make_sales_order as erpnext_sales_order

	if doc.get("tbo_quote_status") != ACCEPTED or doc.docstatus != 1:
		frappe.throw(_("Only an accepted quotation becomes a Sales Order."))
	deal = doc.get("opportunity")
	if not deal:
		frappe.throw(_("The lead has no deal yet: set it to {0} first.").format(CLOSED_WON))
	deal_doc = frappe.get_doc("Opportunity", deal)
	customer = customer_of(deal_doc)
	if customer and lead_of(doc) and not frappe.db.exists("Customer", {"lead_name": lead_of(doc)}):
		# the client already has a Customer (an earlier deal's): quote to it, so ERPNext doesn't
		# make a second one from the lead
		write_tracked(doc, {"quotation_to": "Customer", "party_name": customer})
	order = erpnext_sales_order(doc.name)
	for row in order.items:
		row.delivery_date = row.delivery_date or getdate(today())
	order.delivery_date = order.delivery_date or getdate(today())
	order.tbo_opportunity = deal
	order.insert()
	if not deal_doc.get("tbo_customer") and order.customer:
		frappe.db.set_value("Opportunity", deal, "tbo_customer", order.customer, update_modified=False)
	return order.name


def close_lead_lost(doc, lost_reason: str) -> None:
	set_lead_stage(lead_of(doc), CLOSED_LOST, lost_reason)
