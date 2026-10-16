"""Transfer helpers and the nightly pending-transfer check (docs/crm-finance-workflow.md §3.4,
§5.2, Phase 3)."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, today

from tbo_smart.finance import allocation_enabled, get_setting
from tbo_smart.finance.exceptions import raise_exception


def get_open_allocations(company: str) -> list[dict]:
	"""Submitted allocations with something left to settle, oldest first (FIFO, §2 stage 12).
	Negative ones (reversals, due from India) come too, so a transfer can net them (Q4)."""
	return frappe.get_all(
		"TBO Fund Allocation",
		filters={
			"docstatus": 1,
			"company": company,
			"outstanding_amount": ("!=", 0),
			"status": ("!=", "Reversed"),
		},
		fields=[
			"name",
			"allocation_type",
			"payment_entry",
			"posting_date",
			"customer",
			"opportunity",
			"currency",
			"share_amount",
			"settled_amount",
			"outstanding_amount",
		],
		order_by="posting_date asc, creation asc",
	)


def flag_pending_transfers() -> None:
	"""Daily: a transfer still Sent (India has not confirmed) after N days raises an exception."""
	if not allocation_enabled():
		return
	days = cint(get_setting("transfer_pending_days")) or 5
	for row in frappe.get_all(
		"TBO Fund Transfer",
		filters={"docstatus": 1, "status": "Sent", "transfer_date": ("<", add_days(today(), -days))},
		fields=["name", "from_company", "currency", "amount_sent", "transfer_date"],
	):
		raise_exception(
			category="Transfer",
			exception_type="Transfer pending",
			reference_doctype="TBO Fund Transfer",
			reference_name=row.name,
			company=row.from_company,
			currency=row.currency,
			expected_amount=row.amount_sent,
			reason=_(
				"Transfer {0} of {1} was sent on {2} and India has not confirmed it after {3} days."
			).format(row.name, row.amount_sent, row.transfer_date, days),
			dedupe_key=f"transfer-pending:{row.name}",
		)
