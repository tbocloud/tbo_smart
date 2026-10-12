"""The ERPNext documents of one CRM Deal, read once and shared by the post-sale stage and the
CRM Finance tab, so the stage and the numbers on the tab always agree.

Reads ignore ERPNext document permissions on purpose: the caller has already checked that the
user may see the deal (docs/crm-finance-workflow.md §9, "R own (Finance tab)").
"""

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate, today

AMOUNT_TOLERANCE = 0.005


class DealFinance:
	def __init__(self, deal: str):
		self.deal = deal
		self.quotations = self.load_quotations()
		self.orders = self.load_orders()
		self.invoices = self.load_invoices()
		self.terms = self.load_terms()
		self.payments = self.load_payments()

	# ---------- loading ----------

	def load_quotations(self) -> list:
		Quotation = frappe.qb.DocType("Quotation")
		return (
			frappe.qb.from_(Quotation)
			.select(
				Quotation.name,
				Quotation.status,
				Quotation.docstatus,
				Quotation.transaction_date,
				Quotation.valid_till,
				Quotation.grand_total,
				Quotation.net_total,
				Quotation.currency,
				Quotation.company,
			)
			.where(
				(
					(Quotation.crm_deal == self.deal)
					| ((Quotation.quotation_to == "CRM Deal") & (Quotation.party_name == self.deal))
				)
				& (Quotation.docstatus < 2)
			)
			.orderby(Quotation.creation, order=frappe.qb.desc)
		).run(as_dict=True)

	def load_orders(self) -> list:
		Order = frappe.qb.DocType("Sales Order")
		return (
			frappe.qb.from_(Order)
			.select(
				Order.name,
				Order.status,
				Order.transaction_date,
				Order.delivery_date,
				Order.grand_total,
				Order.advance_paid,
				Order.per_billed,
				Order.currency,
				Order.company,
			)
			.where((Order.crm_deal == self.deal) & (Order.docstatus == 1))
			.orderby(Order.transaction_date, order=frappe.qb.desc)
		).run(as_dict=True)

	def load_invoices(self) -> list:
		Invoice = frappe.qb.DocType("Sales Invoice")
		return (
			frappe.qb.from_(Invoice)
			.select(
				Invoice.name,
				Invoice.status,
				Invoice.posting_date,
				Invoice.due_date,
				Invoice.grand_total,
				Invoice.rounded_total,
				Invoice.outstanding_amount,
				Invoice.currency,
				Invoice.party_account_currency,
				Invoice.company,
				Invoice.is_return,
			)
			.where((Invoice.crm_deal == self.deal) & (Invoice.docstatus == 1))
			.orderby(Invoice.posting_date, order=frappe.qb.desc)
		).run(as_dict=True)

	def load_terms(self) -> dict[str, list]:
		"""Payment schedule rows per invoice, in due-date order."""
		names = [row.name for row in self.invoices]
		if not names:
			return {}
		Term = frappe.qb.DocType("Payment Schedule")
		rows = (
			frappe.qb.from_(Term)
			.select(
				Term.parent,
				Term.payment_term,
				Term.due_date,
				Term.invoice_portion,
				Term.payment_amount,
				Term.paid_amount,
				Term.outstanding,
			)
			.where((Term.parenttype == "Sales Invoice") & Term.parent.isin(names))
			.orderby(Term.due_date)
		).run(as_dict=True)
		terms = {}
		for row in rows:
			terms.setdefault(row.pop("parent"), []).append(row)
		return terms

	def load_payments(self) -> list:
		"""Submitted customer receipts (and refunds, negative) against this deal's orders and
		invoices, plus any unallocated amount of a payment that carries the deal. One row per
		Payment Entry; amounts are in the party account currency."""
		Entry = frappe.qb.DocType("Payment Entry")
		Reference = frappe.qb.DocType("Payment Entry Reference")
		names = [row.name for row in self.orders] + [row.name for row in self.invoices]
		allocated = {}
		if names:
			for row in (
				frappe.qb.from_(Reference)
				.join(Entry)
				.on(Reference.parent == Entry.name)
				.select(Entry.name, Sum(Reference.allocated_amount).as_("amount"))
				.where(
					(Entry.docstatus == 1)
					& (Entry.party_type == "Customer")
					& Reference.reference_doctype.isin(["Sales Order", "Sales Invoice"])
					& Reference.reference_name.isin(names)
				)
				.groupby(Entry.name)
			).run(as_dict=True):
				allocated[row.name] = flt(row.amount)
		unallocated = dict(
			(
				frappe.qb.from_(Entry)
				.select(Entry.name, Entry.unallocated_amount)
				.where(
					(Entry.docstatus == 1)
					& (Entry.party_type == "Customer")
					& (Entry.crm_deal == self.deal)
					& (Entry.unallocated_amount > 0)
				)
			).run()
		)
		entries = set(allocated) | set(unallocated)
		if not entries:
			return []
		rows = (
			frappe.qb.from_(Entry)
			.select(
				Entry.name,
				Entry.payment_type,
				Entry.posting_date,
				Entry.mode_of_payment,
				Entry.reference_no,
				Entry.paid_from_account_currency,
				Entry.paid_to_account_currency,
				Entry.company,
			)
			.where(Entry.name.isin(list(entries)))
			.orderby(Entry.posting_date, order=frappe.qb.desc)
		).run(as_dict=True)
		for row in rows:
			sign = -1 if row.payment_type == "Pay" else 1
			row.amount = sign * (allocated.get(row.name, 0) + flt(unallocated.get(row.name)))
			row.currency = row.paid_to_account_currency if sign < 0 else row.paid_from_account_currency
		return rows

	# ---------- facts ----------

	def sales_invoices(self) -> list:
		"""Submitted invoices that are not credit notes."""
		return [row for row in self.invoices if not row.is_return]

	def overdue_by_invoice(self) -> dict[str, float]:
		"""Outstanding on terms (or, without terms, invoices) due before today."""
		now = getdate(today())
		overdue = {}
		for invoice in self.invoices:
			if flt(invoice.outstanding_amount) <= AMOUNT_TOLERANCE:
				continue
			terms = self.terms.get(invoice.name)
			if terms:
				amount = sum(
					flt(term.outstanding) for term in terms if term.due_date and getdate(term.due_date) < now
				)
				amount = min(amount, flt(invoice.outstanding_amount))
			else:
				due = invoice.due_date or invoice.posting_date
				amount = flt(invoice.outstanding_amount) if due and getdate(due) < now else 0
			if amount > AMOUNT_TOLERANCE:
				overdue[invoice.name] = flt(amount, 2)
		return overdue

	def totals(self) -> list[dict]:
		"""Invoiced, received, outstanding and overdue per currency."""
		overdue = self.overdue_by_invoice()
		by_currency = {}

		def bucket(currency):
			return by_currency.setdefault(
				currency or "",
				{"currency": currency, "invoiced": 0.0, "received": 0.0, "outstanding": 0.0, "overdue": 0.0},
			)

		for invoice in self.invoices:
			row = bucket(invoice.party_account_currency or invoice.currency)
			row["invoiced"] += flt(invoice.rounded_total or invoice.grand_total)
			row["outstanding"] += flt(invoice.outstanding_amount)
			row["overdue"] += overdue.get(invoice.name, 0)
		for payment in self.payments:
			bucket(payment.currency)["received"] += flt(payment.amount)
		return [
			{key: flt(value, 2) if key != "currency" else value for key, value in row.items()}
			for row in by_currency.values()
		]

	def has_quotation_or_order(self) -> bool:
		return bool(self.quotations or self.orders)

	def outstanding(self) -> float:
		return sum(flt(row.outstanding_amount) for row in self.invoices)

	def received(self) -> float:
		return sum(flt(row.amount) for row in self.payments)

	# ---------- Phases 3 and 4 ----------

	def open_exceptions(self) -> bool:
		return bool(
			frappe.db.exists(
				"TBO Finance Exception",
				{"crm_deal": self.deal, "status": ("in", ["Open", "Pending Approval"])},
			)
		)

	def allocation_pending(self) -> bool:
		"""Some allocation line of this deal still has India's share open."""
		Line = frappe.qb.DocType("TBO Fund Allocation Line")
		Allocation = frappe.qb.DocType("TBO Fund Allocation")
		return bool(
			(
				frappe.qb.from_(Line)
				.join(Allocation)
				.on(Line.parent == Allocation.name)
				.select(Allocation.name)
				.where(
					(Allocation.docstatus == 1)
					& (Line.crm_deal == self.deal)
					& (Allocation.status != "Reversed")
					& (Allocation.outstanding_amount != 0)
				)
				.limit(1)
			).run()
		)

	def receipts_verified(self) -> bool:
		"""Every receipt of the deal is on a bank line that a second person verified."""
		receipts = [row.name for row in self.payments if row.payment_type == "Receive"]
		if not receipts:
			return False
		Row = frappe.qb.DocType("Bank Transaction Payments")
		Line = frappe.qb.DocType("Bank Transaction")
		verified = (
			frappe.qb.from_(Row)
			.join(Line)
			.on(Row.parent == Line.name)
			.select(Row.payment_entry)
			.distinct()
			.where(
				(Row.payment_document == "Payment Entry")
				& Row.payment_entry.isin(receipts)
				& (Line.docstatus == 1)
				& (Line.tbo_verification == "Verified")
			)
		).run(pluck=True)
		return set(receipts) <= set(verified)
