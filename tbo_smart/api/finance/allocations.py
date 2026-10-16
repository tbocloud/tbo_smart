"""Allocations & Transfers page (docs/crm-finance-workflow.md §7, Phase 3).

Accounts, finance and management only (risk R7): salespeople never see India's share.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from tbo_smart.finance import allocation_enabled, get_setting
from tbo_smart.finance.access import can_see_allocations
from tbo_smart.finance.allocation import AllocationBuilder, AllocationSettings, allocation_totals
from tbo_smart.finance.transfers import get_open_allocations

LIST_LIMIT = 500
PREVIEW_LIMIT = 1000
TRANSFER_FIELDS = [
	"name",
	"status",
	"transfer_date",
	"reference_no",
	"currency",
	"amount_sent",
	"withholding_tax",
	"allocated_total",
	"difference",
	"received_currency",
	"amount_received",
	"effective_rate",
	"ksa_payment_entry",
	"india_payment_entry",
	"docstatus",
]


def require_allocations() -> AllocationSettings:
	if not allocation_enabled():
		frappe.throw(_("Allocation and transfers are not switched on."), frappe.ValidationError)
	if not can_see_allocations():
		frappe.throw(_("Only accounts, finance and management can see allocations."), frappe.PermissionError)
	return AllocationSettings()


@frappe.whitelist()
def get_allocation_summary() -> dict:
	"""Payable to the partner (share), settled, pending, due from the partner (negative
	outstanding after a reversal), the allocations and the transfers."""
	settings = require_allocations()
	allocations = frappe.get_all(
		"TBO Fund Allocation",
		filters={"docstatus": 1, "company": settings.company},
		fields=[
			"name",
			"allocation_type",
			"status",
			"payment_entry",
			"posting_date",
			"customer",
			"opportunity",
			"currency",
			"gross_amount",
			"net_amount",
			"share_amount",
			"settled_amount",
			"outstanding_amount",
		],
		order_by="posting_date desc, creation desc",
		limit_page_length=LIST_LIMIT,
	)
	transfers = frappe.get_all(
		"TBO Fund Transfer",
		filters={"from_company": settings.company, "docstatus": ("<", 2)},
		fields=TRANSFER_FIELDS,
		order_by="transfer_date desc, creation desc",
		limit_page_length=LIST_LIMIT,
	)
	return {
		"company": settings.company,
		"partner_company": get_setting("partner_company"),
		"percent": settings.percent,
		"start_date": settings.start,
		"accounting_option": get_setting("allocation_accounting_option") or "Memo only",
		"totals": allocation_totals(settings.company),
		"allocations": allocations,
		"transfers": transfers,
		"can_transfer": bool(frappe.has_permission("TBO Fund Transfer", "submit")),
	}


@frappe.whitelist()
def get_transfer_form() -> dict:
	"""What the New transfer dialog needs: open allocations FIFO and the sending company's
	submitted payments (Pay) not used by a transfer yet."""
	settings = require_allocations()
	used = frappe.get_all("TBO Fund Transfer", filters={"docstatus": ("<", 2)}, pluck="ksa_payment_entry")
	payments = frappe.get_all(
		"Payment Entry",
		filters={
			"docstatus": 1,
			"payment_type": "Pay",
			"company": settings.company,
			"name": ("not in", used or [""]),
		},
		fields=["name", "posting_date", "base_paid_amount", "reference_no", "party"],
		order_by="posting_date desc",
		limit_page_length=50,
	)
	return {"allocations": get_open_allocations(settings.company), "payments": payments}


@frappe.whitelist(methods=["POST"])
def create_transfer(
	ksa_payment_entry: str,
	transfer_date: str,
	rows: list | str,
	reference_no: str | None = None,
	withholding_tax: float | None = None,
) -> dict:
	"""Create and submit a transfer: the sending Payment Entry and the allocations it settles
	([{fund_allocation, allocated_amount}]). TBO Fund Transfer permissions apply."""
	require_allocations()
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	if not isinstance(rows, list) or not rows:
		frappe.throw(_("Choose at least one allocation to settle."))
	doc = frappe.get_doc(
		{
			"doctype": "TBO Fund Transfer",
			"ksa_payment_entry": ksa_payment_entry,
			"transfer_date": transfer_date,
			"reference_no": reference_no,
			"withholding_tax": flt(withholding_tax),
			"allocations": [
				{
					"fund_allocation": row.get("fund_allocation"),
					"allocated_amount": flt(row.get("allocated_amount")),
				}
				for row in rows
				if isinstance(row, dict)
			],
		}
	)
	doc.insert()
	doc.submit()
	return {"name": doc.name, "status": doc.status, "difference": doc.difference}


@frappe.whitelist(methods=["POST"])
def confirm_receipt(transfer: str, india_payment_entry: str) -> dict:
	"""India confirms: link its receipt to the transfer. Needs write on TBO Fund Transfer."""
	require_allocations()
	doc = frappe.get_doc("TBO Fund Transfer", transfer)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Only a sent transfer can be confirmed."))
	doc.india_payment_entry = india_payment_entry
	doc.save()
	return {"name": doc.name, "status": doc.status, "effective_rate": doc.effective_rate}


@frappe.whitelist()
def preview_allocations(from_date: str, to_date: str) -> dict:
	"""Backfill for reporting (Q16): what the allocations of receipts before the start date
	would have been. Records nothing and posts nothing."""
	settings = require_allocations()
	from_date, to_date = getdate(from_date), getdate(to_date)
	if to_date < from_date:
		frappe.throw(_("To date must be on or after from date."))
	if settings.start and to_date >= getdate(settings.start):
		to_date = frappe.utils.add_days(settings.start, -1)
	entries = frappe.get_all(
		"Payment Entry",
		filters={
			"docstatus": 1,
			"payment_type": "Receive",
			"party_type": "Customer",
			"company": settings.company,
			"posting_date": ("between", [from_date, to_date]),
		},
		pluck="name",
		order_by="posting_date asc",
		limit_page_length=PREVIEW_LIMIT,
	)
	rows = []
	for name in entries:
		entry = frappe.get_doc("Payment Entry", name)
		lines = AllocationBuilder(entry, settings, preview=True).lines()
		rows.append(
			{
				"payment_entry": name,
				"posting_date": entry.posting_date,
				"customer": entry.party,
				"gross": flt(sum(line["gross_amount"] for line in lines), 2),
				"net": flt(sum(line["net_amount"] for line in lines), 2),
				"share": flt(sum(line["share_amount"] for line in lines), 2),
			}
		)
	return {
		"from_date": from_date,
		"to_date": to_date,
		"rows": rows,
		"total_share": flt(sum(row["share"] for row in rows), 2),
		"truncated": len(entries) >= PREVIEW_LIMIT,
	}


def get_deal_allocation(deal: str) -> dict | None:
	"""The Finance tab's Allocation section: the deal's share, settled and pending. None for
	users who may not see allocations."""
	if not allocation_enabled() or not can_see_allocations():
		return None
	Line = frappe.qb.DocType("TBO Fund Allocation Line")
	Allocation = frappe.qb.DocType("TBO Fund Allocation")
	rows = (
		frappe.qb.from_(Line)
		.join(Allocation)
		.on(Line.parent == Allocation.name)
		.select(
			Allocation.name,
			Allocation.currency,
			Allocation.share_amount,
			Allocation.outstanding_amount,
			Line.share_amount.as_("line_share"),
		)
		.where(
			(Allocation.docstatus == 1)
			& (Allocation.company == get_setting("invoicing_company"))
			& (Line.opportunity == deal)
			& (Allocation.status != "Reversed")
		)
	).run(as_dict=True)
	share = sum(flt(row.line_share) for row in rows)
	# each allocation's outstanding is shared out over its lines in proportion to their share
	pending = sum(
		flt(row.outstanding_amount) * flt(row.line_share) / flt(row.share_amount)
		for row in rows
		if flt(row.share_amount)
	)
	return {
		"currency": rows[0].currency if rows else None,
		"share": flt(share, 2),
		"settled": flt(share - pending, 2),
		"pending": flt(pending, 2),
		"allocations": len({row.name for row in rows}),
	}
