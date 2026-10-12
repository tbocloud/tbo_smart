"""India's share of each KSA receipt: TBO Fund Allocation (docs/crm-finance-workflow.md §3.3,
§4, Phase 3).

One allocation per submitted customer receipt of the invoicing company, from the allocation
start date. It holds no money of its own and posts nothing to the GL (Q1: memo only, until
Finance picks an accounting option): its amounts are derived from the Payment Entry.

Formula (§4.1), per reference row and the unallocated remainder:

    gross     = allocated amount in company currency
    net_ratio = document base_net_total / base_grand_total  (invoice or order)
              = latest submitted order/quotation of the deal  (unallocated, deal known)
              = 1 / (1 + default VAT)                        (unallocated, no document)
    net       = round(gross x net_ratio, 2)
    share     = round(net x percent / 100, 2)

A receipt that brings an invoice's outstanding to zero takes the remainder, so an invoice's
shares always add up to exactly `percent` of its net total. Rounding is half-up.
"""

from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, getdate

from tbo_smart.finance import allocation_enabled, get_setting
from tbo_smart.finance.exceptions import raise_exception

DOCTYPE = "TBO Fund Allocation"
CLOSING_TOLERANCE = 0.005
REFERENCE_DOCTYPES = ("Sales Invoice", "Sales Order")


def money(value) -> float:
	"""Round half-up to 2 decimals (ERPNext's own flt may round half-even)."""
	return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class AllocationSettings:
	def __init__(self):
		self.company = get_setting("invoicing_company")
		self.percent = flt(get_setting("allocation_percent")) or 50
		self.start = get_setting("allocation_start_date")
		self.after_charges = cint(get_setting("allocation_base_after_bank_charges"))
		self.vat = flt(get_setting("default_vat_rate"))

	def covers(self, doc) -> bool:
		"""Whether a payment belongs to the allocation: the invoicing company (Q7), on or
		after the start date (Q16). A missing company only logs a warning (Q2)."""
		if not self.company:
			return False
		if not frappe.db.exists("Company", self.company):
			frappe.log_error(
				title=_("TBO allocation skipped"),
				message=_("Invoicing company {0} does not exist on this site.").format(self.company),
			)
			return False
		if doc.company != self.company:
			return False
		return not self.start or getdate(doc.posting_date) >= getdate(self.start)


class AllocationBuilder:
	"""Lines and totals for one Payment Entry. `sign` is -1 for refunds. A `preview` of a past
	receipt uses the plain ratio: an invoice paid in full today says nothing about whether that
	receipt was the one that settled it."""

	def __init__(self, entry, settings: AllocationSettings, sign: int = 1, preview: bool = False):
		self.entry = entry
		self.settings = settings
		self.sign = sign
		self.preview = preview
		self.rate = (
			flt(entry.source_exchange_rate if entry.payment_type == "Receive" else entry.target_exchange_rate)
			or 1
		)
		self.charge_factor = self.get_charge_factor()

	def get_charge_factor(self) -> float:
		"""With "base after bank charges" on, deductions reduce the base pro rata (Q6; default off)."""
		if not self.settings.after_charges:
			return 1.0
		paid = flt(self.entry.base_received_amount) or flt(self.entry.base_paid_amount)
		charges = sum(flt(row.amount) for row in self.entry.get("deductions") or [])
		return (paid - charges) / paid if paid else 1.0

	def lines(self) -> list[dict]:
		rows = []
		for ref in self.entry.get("references") or []:
			if ref.reference_doctype not in REFERENCE_DOCTYPES or not flt(ref.allocated_amount):
				continue
			gross = abs(flt(ref.allocated_amount)) * self.rate * self.charge_factor
			rows.append(self.document_line(ref.reference_doctype, ref.reference_name, gross))
		unallocated = flt(self.entry.get("unallocated_amount"))
		if unallocated > 0:
			gross = unallocated * self.rate * self.charge_factor
			ratio = self.deal_ratio()
			rows.append(self.line("Unallocated", None, self.entry.get("crm_deal"), gross, ratio))
		return rows

	def document_line(self, doctype: str, name: str, gross: float) -> dict:
		doc = frappe.db.get_value(
			doctype,
			name,
			[
				"base_net_total",
				"base_grand_total",
				"crm_deal",
				"outstanding_amount" if doctype == "Sales Invoice" else "name",
			],
			as_dict=True,
		)
		ratio = abs(flt(doc.base_net_total) / flt(doc.base_grand_total)) if flt(doc.base_grand_total) else 1.0
		closing = (
			doctype == "Sales Invoice"
			and self.sign > 0
			and not self.preview
			and abs(flt(doc.outstanding_amount)) <= CLOSING_TOLERANCE
		)
		if closing:
			return self.closing_line(name, doc, gross, ratio)
		return self.line(doctype, name, doc.crm_deal, gross, ratio)

	def line(self, doctype: str, name: str | None, deal: str | None, gross: float, ratio: float) -> dict:
		net = money(gross * ratio)
		share = money(net * self.settings.percent / 100)
		return {
			"reference_doctype": doctype,
			"reference_name": name,
			"crm_deal": deal,
			"gross_amount": money(gross) * self.sign,
			"net_ratio": round(ratio, 9),
			"tax_amount": money(money(gross) - net) * self.sign,
			"net_amount": net * self.sign,
			"share_amount": share * self.sign,
			"is_closing_receipt": 0,
		}

	def closing_line(self, invoice: str, doc, gross: float, ratio: float) -> dict:
		"""The receipt that settles the invoice takes the remainder of its net and share."""
		earlier_net, earlier_share = earlier_on_invoice(invoice, self.entry.name)
		net = money(flt(doc.base_net_total) - earlier_net)
		share = money(money(flt(doc.base_net_total) * self.settings.percent / 100) - earlier_share)
		return {
			"reference_doctype": "Sales Invoice",
			"reference_name": invoice,
			"crm_deal": doc.crm_deal,
			"gross_amount": money(gross),
			"net_ratio": round(ratio, 9),
			"tax_amount": money(money(gross) - net),
			"net_amount": net,
			"share_amount": share,
			"is_closing_receipt": 1,
		}

	def deal_ratio(self) -> float:
		"""Net ratio for money not yet against a document: the deal's latest submitted order or
		quotation, else 1 / (1 + default VAT)."""
		deal = self.entry.get("crm_deal")
		if deal:
			for doctype in ("Sales Order", "Quotation"):
				row = frappe.get_all(
					doctype,
					filters={"crm_deal": deal, "docstatus": 1},
					fields=["base_net_total", "base_grand_total"],
					order_by="creation desc",
					limit=1,
				)
				if row and flt(row[0].base_grand_total):
					return flt(row[0].base_net_total) / flt(row[0].base_grand_total)
		return 1 / (1 + self.settings.vat / 100)


def allocation_totals(company: str) -> list[dict]:
	"""Per currency: share, settled, pending (positive outstanding) and due from the partner
	(negative outstanding, after a reversal), for the Allocations page and the digest."""
	Allocation = frappe.qb.DocType(DOCTYPE)
	outstanding = Allocation.outstanding_amount
	rows = (
		frappe.qb.from_(Allocation)
		.select(
			Allocation.currency,
			Sum(Allocation.share_amount),
			Sum(Allocation.settled_amount),
			Sum(Case().when(outstanding > 0, outstanding).else_(0)),
			Sum(Case().when(outstanding < 0, -outstanding).else_(0)),
		)
		.where(
			(Allocation.docstatus == 1) & (Allocation.company == company) & (Allocation.status != "Reversed")
		)
		.groupby(Allocation.currency)
	).run()
	return [
		{
			"currency": currency,
			"share": flt(share, 2),
			"settled": flt(settled, 2),
			"pending": flt(pending, 2),
			"due_from_partner": flt(due_back, 2),
		}
		for currency, share, settled, pending, due_back in rows
	]


def earlier_on_invoice(invoice: str, exclude_entry: str) -> tuple[float, float]:
	"""Net and share already allocated on an invoice by other receipts: lines against the
	invoice itself, plus the part of advance lines (against its sales orders) that the invoice
	pulled in through its advances table."""
	Line = frappe.qb.DocType("TBO Fund Allocation Line")
	Allocation = frappe.qb.DocType(DOCTYPE)
	direct = (
		frappe.qb.from_(Line)
		.join(Allocation)
		.on(Line.parent == Allocation.name)
		.select(Line.net_amount, Line.share_amount)
		.where(
			(Allocation.docstatus == 1)
			& (Allocation.payment_entry != exclude_entry)
			& (Line.reference_doctype == "Sales Invoice")
			& (Line.reference_name == invoice)
		)
	).run(as_dict=True)
	net = sum(flt(row.net_amount) for row in direct)
	share = sum(flt(row.share_amount) for row in direct)
	advances = frappe.get_all(
		"Sales Invoice Advance",
		filters={"parent": invoice, "reference_type": "Payment Entry"},
		fields=["reference_name", "allocated_amount"],
	)
	for advance in advances:
		if advance.reference_name == exclude_entry:
			continue
		for row in (
			frappe.qb.from_(Line)
			.join(Allocation)
			.on(Line.parent == Allocation.name)
			.select(Line.gross_amount, Line.net_amount, Line.share_amount)
			.where(
				(Allocation.docstatus == 1)
				& (Allocation.payment_entry == advance.reference_name)
				& Line.reference_doctype.isin(["Sales Order", "Unallocated"])
			)
		).run(as_dict=True):
			if flt(row.gross_amount):
				part = min(flt(advance.allocated_amount) / flt(row.gross_amount), 1)
				net += flt(row.net_amount) * part
				share += flt(row.share_amount) * part
	return money(net), money(share)


def make_allocation(
	entry,
	lines: list[dict],
	settings: AllocationSettings,
	kind: str = "Receipt",
	reversal_of: str | None = None,
):
	deals = {line["crm_deal"] for line in lines}
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"allocation_type": kind,
			"payment_entry": entry.name,
			"reversal_of": reversal_of,
			"posting_date": entry.posting_date,
			"company": entry.company,
			"customer": entry.party,
			"crm_deal": deals.pop() if len(deals) == 1 else None,
			"currency": frappe.get_cached_value("Company", entry.company, "default_currency"),
			"allocation_percent": settings.percent,
			"lines": lines,
		}
	)
	doc.flags.from_engine = True
	# a reversal for a cancelled receipt still points at it
	doc.flags.ignore_links = bool(reversal_of)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def get_receipt_allocation(entry: str):
	name = frappe.db.get_value(
		DOCTYPE, {"payment_entry": entry, "allocation_type": "Receipt", "docstatus": 1}
	)
	return frappe.get_doc(DOCTYPE, name) if name else None


class PaymentAllocation:
	"""doc_events entry point for Payment Entry submit and cancel."""

	def __init__(self, entry):
		self.entry = entry
		self.settings = AllocationSettings()

	def on_submit(self):
		if (
			not allocation_enabled()
			or self.entry.party_type != "Customer"
			or not self.settings.covers(self.entry)
		):
			return
		if self.entry.payment_type == "Receive":
			lines = AllocationBuilder(self.entry, self.settings).lines()
			if lines:
				make_allocation(self.entry, lines, self.settings)
		elif self.entry.payment_type == "Pay":
			self.reverse_refund()

	def reverse_refund(self):
		"""A refund to the client lowers the payable: a negative Reversal for its net (Q4)."""
		lines = AllocationBuilder(self.entry, self.settings, sign=-1).lines()
		if lines:
			make_allocation(self.entry, lines, self.settings, kind="Reversal")

	def on_cancel(self):
		# the receipt's allocations (and a reversal made below) must not block its cancellation
		self.entry.ignore_linked_doctypes = (*(self.entry.get("ignore_linked_doctypes") or ()), DOCTYPE)
		if not allocation_enabled():
			return
		for allocation in frappe.get_all(
			DOCTYPE, filters={"payment_entry": self.entry.name, "docstatus": 1}, pluck="name"
		):
			self.undo(frappe.get_doc(DOCTYPE, allocation))

	def undo(self, allocation):
		"""Unsettled: cancel it. Already (partly) settled by a transfer: a Reversal for the part
		India received, so India owes back exactly that, plus an exception for the Finance
		Manager (§3.3). The original is marked Reversed: its unsettled rest is no longer payable."""
		if not flt(allocation.settled_amount):
			allocation.flags.from_engine = True
			allocation.cancel()
			return
		reversal = make_allocation(
			self.entry,
			self.reversal_lines(allocation),
			self.settings,
			kind="Reversal",
			reversal_of=allocation.name,
		)
		allocation.db_set("status", "Reversed")
		raise_exception(
			category="Allocation",
			exception_type="Receipt cancelled after transfer",
			reference_doctype=DOCTYPE,
			reference_name=reversal.name,
			company=allocation.company,
			customer=allocation.customer,
			crm_deal=allocation.crm_deal,
			currency=allocation.currency,
			expected_amount=0,
			actual_amount=flt(allocation.settled_amount),
			reason=_("Receipt {0} was cancelled after {1} of its share was transferred.").format(
				self.entry.name, flt(allocation.settled_amount)
			),
			dedupe_key=f"cancelled-after-transfer:{allocation.name}",
		)

	@staticmethod
	def reversal_lines(allocation) -> list[dict]:
		"""The allocation's lines negated and scaled to the settled part of its share; the last
		line takes the rounding so the shares add up to exactly minus the settled amount."""
		settled, share = flt(allocation.settled_amount), flt(allocation.share_amount)
		factor = min(settled / share, 1) if share else 1
		lines = []
		for line in allocation.lines:
			row = {
				key: line.get(key)
				for key in (
					"reference_doctype",
					"reference_name",
					"crm_deal",
					"net_ratio",
					"is_closing_receipt",
				)
			}
			for key in ("gross_amount", "tax_amount", "net_amount", "share_amount"):
				row[key] = -money(flt(line.get(key)) * factor)
			lines.append(row)
		if lines:
			lines[-1]["share_amount"] = money(
				lines[-1]["share_amount"] - sum(row["share_amount"] for row in lines) - settled
			)
		return lines


class JournalReceiptCheck:
	"""A customer receipt booked as a Journal Entry gets no allocation; it raises an exception
	so Accounts book it as a Payment Entry instead (§3.3)."""

	def __init__(self, entry):
		self.entry = entry

	def on_submit(self):
		if not allocation_enabled() or not AllocationSettings().covers(self.entry):
			return
		credited = [
			row
			for row in self.entry.accounts
			if row.party_type == "Customer" and flt(row.credit_in_account_currency) > 0
		]
		banked = [
			row
			for row in self.entry.accounts
			if flt(row.debit) > 0
			and frappe.get_cached_value("Account", row.account, "account_type") in ("Bank", "Cash")
		]
		if not credited or not banked:
			return
		raise_exception(
			category="Allocation",
			exception_type="Receipt via Journal Entry",
			reference_doctype="Journal Entry",
			reference_name=self.entry.name,
			company=self.entry.company,
			customer=credited[0].party,
			currency=frappe.get_cached_value("Company", self.entry.company, "default_currency"),
			actual_amount=sum(flt(row.credit) for row in credited),
			reason=_(
				"A customer receipt was booked as a Journal Entry, so it has no allocation. Book it as a Payment Entry."
			),
			dedupe_key=f"je-receipt:{self.entry.name}",
		)


def payment_submitted(doc, method=None):
	PaymentAllocation(doc).on_submit()


def payment_cancelled(doc, method=None):
	PaymentAllocation(doc).on_cancel()


def journal_submitted(doc, method=None):
	JournalReceiptCheck(doc).on_submit()
