"""Bank-line verification queue in the CRM (docs/crm-finance-workflow.md §5.3, Phase 4).

Matching stays in ERPNext's Bank Reconciliation Tool (Q10). The CRM lists what is matched but
not yet verified, and lets a second person verify it.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, today

from tbo_smart.finance import reconciliation_enabled
from tbo_smart.finance.access import ALLOCATION_ROLES, is_finance_user
from tbo_smart.finance.reconciliation import VERIFIER_ROLES, verify_line

QUEUE_LIMIT = 500
LINE_FIELDS = [
	"name",
	"date",
	"status",
	"bank_account",
	"company",
	"currency",
	"deposit",
	"withdrawal",
	"reference_number",
	"description",
	"unallocated_amount",
	"tbo_verification",
	"tbo_matched_by",
	"tbo_verified_by",
	"tbo_possible_duplicate_of",
]


def require_reconciliation() -> None:
	if not reconciliation_enabled():
		frappe.throw(_("Reconciliation is not switched on."), frappe.ValidationError)
	if not (set(frappe.get_roles()) & ALLOCATION_ROLES or is_finance_user()):
		frappe.throw(_("Only accounts, finance and management can see bank lines."), frappe.PermissionError)


@frappe.whitelist()
def get_bank_queue() -> dict:
	"""Matched lines waiting for verification, and lines still unmatched with their age."""
	require_reconciliation()
	waiting = frappe.get_all(
		"Bank Transaction",
		filters={"docstatus": 1, "status": "Reconciled", "tbo_verification": ("!=", "Verified")},
		fields=LINE_FIELDS,
		order_by="date asc",
		limit_page_length=QUEUE_LIMIT,
	)
	unmatched = frappe.get_all(
		"Bank Transaction",
		filters={"docstatus": 1, "status": ("in", ["Pending", "Unreconciled"])},
		fields=LINE_FIELDS,
		order_by="date asc",
		limit_page_length=QUEUE_LIMIT,
	)
	user = frappe.session.user
	can_verify = bool(set(frappe.get_roles()) & VERIFIER_ROLES)
	for row in waiting:
		row.amount = flt(row.deposit) or -flt(row.withdrawal)
		row.can_verify = can_verify and row.tbo_matched_by != user
	for row in unmatched:
		row.amount = flt(row.deposit) or -flt(row.withdrawal)
		row.age_days = date_diff(today(), row.date)
	return {"waiting": waiting, "unmatched": unmatched, "can_verify": can_verify}


@frappe.whitelist(methods=["POST"])
def verify_bank_line(name: str) -> dict:
	"""Verify a matched bank line (a different person from whoever matched it)."""
	require_reconciliation()
	return verify_line(name)
