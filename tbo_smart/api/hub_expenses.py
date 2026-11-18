"""Expense claims for the TBO Support hub (docs/hub-leads-api.md §2.35).

Every method runs as the acting user (see hub_leads.acting_as) through the TBO Smart app's own
expense paths (api/mobile/ess.py, api/mobile/manager.py), so the hub, the app and the desk agree
on who may claim, see and decide: a person's claims are their own Employee's, and their approvals
are what HRMS gives them to approve (the Expense Claim workflow, else the named approver, or an
HR Manager / HR User). Receipts stay private here; the hub streams them through
`get_expense_receipt`.
"""

import os

import erpnext
import frappe
from frappe import _

from tbo_smart.api.hub_leads import acting_as
from tbo_smart.api.mobile import ess, manager
from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.lead_pipeline.lead_files import MAX_FILE_BYTES, TYPES, clean_name

RECEIPT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
CLAIMS_PAGE_LENGTH = 50


@frappe.whitelist()
def get_expenses(page: int = 1, acting_user: str | None = None) -> dict:
	"""What the acting user's expenses page shows: {employee (linked to an active Employee),
	can_approve, currency, types, claims: {data, page, has_more} (their own, newest first) or
	null without an Employee, approvals: [Approval] (others' claims waiting for them)}."""
	with acting_as(acting_user) as user:
		capabilities = get_capabilities()
		employee = frappe.db.get_value(
			"Employee", {"user_id": user, "status": "Active"}, ["name", "company"], as_dict=True
		)
		company = employee.company if employee else erpnext.get_default_company()
		approvals = []
		if capabilities.approve_expense:
			approvals = manager.get_expense_approvals(employee.name if employee else "")
			receipts = ess.claim_receipts([row["ref"] for row in approvals])
			for row in approvals:
				row["receipt"] = receipts[row["ref"]].file_name if row["ref"] in receipts else None
		return {
			"employee": bool(employee),
			"can_approve": bool(capabilities.approve_expense),
			"currency": erpnext.get_company_currency(company) if company else None,
			"types": ess.get_expense_types() if employee else [],
			"claims": ess.get_expense_claims(page, CLAIMS_PAGE_LENGTH) if employee else None,
			"approvals": approvals,
		}


@frappe.whitelist(methods=["POST"])
def create_expense_claim(
	expense_type: str,
	amount: float,
	expense_date: str,
	description: str | None = None,
	receipt_filename: str | None = None,
	receipt_base64: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""A draft claim of one expense for the acting user's Employee, routed to their expense
	approver, with an optional private receipt (PDF or picture, at most 10 MB)."""
	with acting_as(acting_user):
		if receipt_base64:
			receipt_filename = receipt_name(receipt_filename, receipt_base64)
		return ess.create_expense_claim(
			expense_type, amount, expense_date, description, receipt_filename, receipt_base64
		)


@frappe.whitelist(methods=["POST"])
def decide_expense(
	name: str, approve: bool, reason: str | None = None, acting_user: str | None = None
) -> dict:
	"""Approve or reject a claim waiting for the acting user; a rejection needs `reason`."""
	with acting_as(acting_user):
		return manager.decide_approval("expense", name, approve, reason)


@frappe.whitelist()
def get_expense_receipt(name: str, acting_user: str | None = None) -> None:
	"""The claim's receipt to anyone who may read the claim; the response is the file, not JSON."""
	with acting_as(acting_user):
		frappe.has_permission("Expense Claim", "read", name, throw=True)
		receipt = ess.claim_receipts([name]).get(name)
		if not receipt:
			frappe.throw(_("This claim has no receipt."), frappe.DoesNotExistError)
		file = frappe.get_doc("File", receipt.name)
		content = file.get_content()
	frappe.local.response.filename = file.file_name
	frappe.local.response.filecontent = content.encode() if isinstance(content, str) else content
	frappe.local.response.content_type = TYPES.get(
		os.path.splitext(file.file_name or "")[1].lower(), "application/octet-stream"
	)
	frappe.local.response.display_content_as = "attachment"
	frappe.local.response.type = "download"


def receipt_name(filename: str | None, content_base64: str) -> str:
	"""The receipt's cleaned file name; anything but a PDF or picture, or over 10 MB, is refused."""
	extension = os.path.splitext(filename or "")[1].lower()
	if extension not in RECEIPT_EXTENSIONS:
		frappe.throw(_("Attach the receipt as a PDF or a picture (PNG, JPEG or WebP)."))
	# base64 holds three bytes in every four characters
	if len(content_base64) * 3 // 4 > MAX_FILE_BYTES:
		frappe.throw(_("The receipt is larger than 10 MB. Make it smaller and try again."))
	stem, extension = clean_name(filename)
	return f"{stem}{extension}"
