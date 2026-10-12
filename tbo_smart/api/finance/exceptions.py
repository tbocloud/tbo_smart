"""Finance exceptions in the CRM: request, list and decide (docs/crm-finance-workflow.md §3.5).

Phase 2 raises Receivable exceptions from the Receivables page and the deal's Finance tab:
extend a due date, pause reminders, write off a balance, or record a client dispute (which
pauses reminders). A TBO Finance Approver decides them; the requester never can.
"""

import frappe
from frappe import _
from frappe.utils import flt

from tbo_smart.api.finance.receivables import get_invoice_in_scope
from tbo_smart.finance import receivables_enabled
from tbo_smart.tbo_smart.doctype.tbo_finance_exception.tbo_finance_exception import TRANSITIONS, is_approver

DOCTYPE = "TBO Finance Exception"
# what a salesperson or accountant can ask for on an invoice, and the action it implies
RECEIVABLE_REQUESTS = {
	"Extension request": "Extend due date",
	"Pause request": "Pause reminders",
	"Client dispute": "Pause reminders",
	"Write-off request": "Waive balance",
}
LIST_FIELDS = [
	"name",
	"category",
	"exception_type",
	"requested_action",
	"status",
	"source",
	"reference_doctype",
	"reference_name",
	"customer",
	"crm_deal",
	"company",
	"currency",
	"expected_amount",
	"actual_amount",
	"difference",
	"reason",
	"new_due_date",
	"pause_until",
	"resolution_reference_doctype",
	"resolution_reference_name",
	"owner",
	"requested_by",
	"approved_by",
	"approved_on",
	"decision_remarks",
	"creation",
]


@frappe.whitelist(methods=["POST"])
def request_exception(
	invoice: str,
	exception_type: str,
	reason: str,
	new_due_date: str | None = None,
	pause_until: str | None = None,
	payment_schedule: str | None = None,
	resolution_reference_doctype: str | None = None,
	resolution_reference_name: str | None = None,
) -> dict:
	"""Raise a receivable exception on an invoice and send it for approval."""
	row = get_invoice_in_scope(invoice)
	if exception_type not in RECEIVABLE_REQUESTS:
		frappe.throw(_("Choose one of: {0}.").format(", ".join(RECEIVABLE_REQUESTS)))
	if not (reason or "").strip():
		frappe.throw(_("Give a reason."))
	if payment_schedule and not frappe.db.exists(
		"Payment Schedule", {"name": payment_schedule, "parent": invoice, "parenttype": "Sales Invoice"}
	):
		frappe.throw(_("That payment term is not on invoice {0}.").format(invoice))
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"category": "Receivable",
			"exception_type": exception_type,
			"requested_action": RECEIVABLE_REQUESTS[exception_type],
			"source": "User",
			"reference_doctype": "Sales Invoice",
			"reference_name": invoice,
			"payment_schedule": payment_schedule or None,
			"company": row.company,
			"customer": row.customer,
			"crm_deal": row.crm_deal,
			"currency": row.currency,
			"expected_amount": flt(row.outstanding_amount),
			"reason": reason,
			"new_due_date": new_due_date or None,
			"pause_until": pause_until or None,
			"resolution_reference_doctype": resolution_reference_doctype or None,
			"resolution_reference_name": resolution_reference_name or None,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.apply_action("request_approval")
	return serialize(doc)


@frappe.whitelist()
def get_exceptions(
	status: str | None = None, category: str | None = None, crm_deal: str | None = None
) -> dict:
	"""Exceptions the user may read (TBO Finance Exception permission rules), newest first,
	with whether the user may decide each one."""
	if not receivables_enabled():
		frappe.throw(_("Receivables are not switched on."), frappe.ValidationError)
	filters = {}
	for field, value in (("status", status), ("category", category), ("crm_deal", crm_deal)):
		if value:
			filters[field] = value
	rows = frappe.get_list(
		DOCTYPE, filters=filters, fields=LIST_FIELDS, order_by="creation desc", limit_page_length=500
	)
	approver = is_approver()
	for row in rows:
		row["can_decide"] = (
			approver
			and row.status == "Pending Approval"
			and frappe.session.user not in {row.owner, row.requested_by}
		)
	return {"rows": rows, "is_approver": approver}


@frappe.whitelist(methods=["POST"])
def decide_exception(name: str, action: str, remarks: str | None = None) -> dict:
	"""request_approval, approve, reject or reopen. The doctype enforces who may do what."""
	if not receivables_enabled():
		frappe.throw(_("Receivables are not switched on."), frappe.ValidationError)
	if action not in TRANSITIONS:
		frappe.throw(_("Unknown action {0}.").format(action))
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	doc.apply_action(action, remarks)
	return serialize(doc)


def serialize(doc) -> dict:
	return {field: doc.get(field) for field in LIST_FIELDS}
