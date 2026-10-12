# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

"""One KSA → India transfer and the allocations it settles (docs/crm-finance-workflow.md §3.4).

The money moves through two ordinary Payment Entries, one per company: the sending one (Pay,
from the invoicing company) and, once India confirms, the receiving one (Receive, at the
partner company). This record only links them and says which allocations the transfer
settles; its amounts are read from the Payment Entries. Nothing here posts to the GL (Q1).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from tbo_smart.finance import get_setting
from tbo_smart.finance.exceptions import raise_exception

ALLOCATION = "TBO Fund Allocation"


class TBOFundTransfer(Document):
	def validate(self):
		self.set_companies()
		self.fetch_sending_leg()
		self.fetch_receiving_leg()
		self.validate_rows()
		self.set_totals()
		self.set_status()

	def before_submit(self):
		self.validate_legs()
		self.lock_and_recheck_rows()

	def on_submit(self):
		self.update_allocations()
		self.raise_difference()
		self.set_status(save=True)

	def before_update_after_submit(self):
		self.fetch_receiving_leg()
		self.validate_receiving_leg()
		self.set_status()

	def on_cancel(self):
		self.update_allocations()
		self.db_set("status", "Cancelled")

	# ---------- legs ----------

	def set_companies(self):
		self.from_company = self.from_company or get_setting("invoicing_company")
		self.to_company = self.to_company or get_setting("partner_company")
		if not self.from_company or not self.to_company:
			frappe.throw(_("Set the invoicing and partner companies in TBO Smart Settings first."))
		self.currency = frappe.get_cached_value("Company", self.from_company, "default_currency")

	def fetch_sending_leg(self):
		entry = frappe.get_doc("Payment Entry", self.ksa_payment_entry)
		self.amount_sent = flt(entry.base_paid_amount)
		self.sender_charges = sum(flt(row.amount) for row in entry.get("deductions") or [])
		if not self.reference_no:
			self.reference_no = entry.reference_no

	def fetch_receiving_leg(self):
		if not self.india_payment_entry:
			self.amount_received = self.receiver_charges = self.effective_rate = 0
			return
		entry = frappe.get_doc("Payment Entry", self.india_payment_entry)
		self.received_currency = frappe.get_cached_value("Company", entry.company, "default_currency")
		self.amount_received = flt(entry.base_received_amount)
		self.receiver_charges = sum(flt(row.amount) for row in entry.get("deductions") or [])
		# Q3: the rate the bank achieved, received ÷ sent
		self.effective_rate = flt(self.amount_received / self.amount_sent, 6) if flt(self.amount_sent) else 0

	def validate_legs(self):
		entry = frappe.db.get_value(
			"Payment Entry", self.ksa_payment_entry, ["docstatus", "payment_type", "company"], as_dict=True
		)
		if entry.docstatus != 1 or entry.payment_type != "Pay" or entry.company != self.from_company:
			frappe.throw(
				_("The sending Payment Entry must be a submitted payment (Pay) of {0}.").format(
					self.from_company
				)
			)
		self.validate_receiving_leg()

	def validate_receiving_leg(self):
		if not self.india_payment_entry:
			return
		entry = frappe.db.get_value(
			"Payment Entry", self.india_payment_entry, ["docstatus", "payment_type", "company"], as_dict=True
		)
		if entry.docstatus != 1 or entry.payment_type != "Receive" or entry.company != self.to_company:
			frappe.throw(
				_("The receiving Payment Entry must be a submitted receipt (Receive) of {0}.").format(
					self.to_company
				)
			)
		other = frappe.db.get_value(
			"TBO Fund Transfer",
			{"india_payment_entry": self.india_payment_entry, "docstatus": 1, "name": ("!=", self.name)},
		)
		if other:
			frappe.throw(_("That receipt already confirms transfer {0}.").format(other))

	# ---------- allocations ----------

	def validate_rows(self):
		seen = set()
		for row in self.allocations:
			if row.fund_allocation in seen:
				frappe.throw(
					_("Row {0}: allocation {1} is listed twice.").format(row.idx, row.fund_allocation)
				)
			seen.add(row.fund_allocation)
			allocation = frappe.db.get_value(
				ALLOCATION,
				row.fund_allocation,
				["docstatus", "company", "outstanding_amount", "payment_entry", "customer", "currency"],
				as_dict=True,
			)
			if not allocation or allocation.docstatus != 1 or allocation.company != self.from_company:
				frappe.throw(
					_("Row {0}: {1} is not a submitted allocation of {2}.").format(
						row.idx, row.fund_allocation, self.from_company
					)
				)
			row.update(
				{
					"payment_entry": allocation.payment_entry,
					"customer": allocation.customer,
					"currency": allocation.currency,
					"allocation_outstanding": allocation.outstanding_amount,
				}
			)
			self.check_row_amount(row, flt(allocation.outstanding_amount))

	@staticmethod
	def check_row_amount(row, outstanding: float):
		"""A row settles at most what is open. Negative rows net a reversal (due from India, Q4)."""
		amount = flt(row.allocated_amount, 2)
		if not amount:
			frappe.throw(_("Row {0}: enter the amount this transfer settles.").format(row.idx))
		if (outstanding >= 0 and not 0 < amount <= flt(outstanding, 2)) or (
			outstanding < 0 and not flt(outstanding, 2) <= amount < 0
		):
			frappe.throw(
				_("Row {0}: {1} can settle at most {2}.").format(
					row.idx, row.fund_allocation, flt(outstanding, 2)
				)
			)

	def set_totals(self):
		self.allocated_total = flt(sum(flt(row.allocated_amount) for row in self.allocations), 2)
		# Q8: withholding tax, when KSA deducts any, counts as paid to India
		# a short or excess transfer is allowed but raises an exception on submit (§3.4)
		self.difference = flt(flt(self.amount_sent) + flt(self.withholding_tax) - self.allocated_total, 2)

	def lock_and_recheck_rows(self):
		"""Read each allocation FOR UPDATE, so two transfers cannot over-settle one allocation."""
		for row in self.allocations:
			outstanding = frappe.db.get_value(
				ALLOCATION, row.fund_allocation, "outstanding_amount", for_update=True
			)
			self.check_row_amount(row, flt(outstanding))

	def update_allocations(self):
		for row in self.allocations:
			frappe.get_doc(ALLOCATION, row.fund_allocation).update_settlement()

	# ---------- difference and status ----------

	def raise_difference(self):
		"""A short or excess transfer beyond the tolerance needs a decision (§3.4)."""
		if abs(flt(self.difference)) <= tolerance():
			return
		raise_exception(
			category="Transfer",
			exception_type="Short transfer" if self.difference < 0 else "Excess transfer",
			reference_doctype=self.doctype,
			reference_name=self.name,
			company=self.from_company,
			currency=self.currency,
			expected_amount=self.allocated_total,
			actual_amount=flt(self.amount_sent) + flt(self.withholding_tax),
			reason=_("Transfer {0} sent {1} against allocations of {2}.").format(
				self.name, flt(self.amount_sent), self.allocated_total
			),
			dedupe_key=f"transfer-difference:{self.name}",
		)

	def has_open_exception(self) -> bool:
		return bool(
			frappe.db.exists(
				"TBO Finance Exception",
				{
					"reference_doctype": self.doctype,
					"reference_name": self.name,
					"status": ("in", ["Open", "Pending Approval"]),
				},
			)
		)

	def set_status(self, save: bool = False):
		"""Draft → Sent (sending leg submitted) → Received (India's receipt linked); Difference
		while an exception on it is open. Matched and Closed arrive with Phase 4 (verified bank
		lines)."""
		if self.docstatus == 0:
			status = "Draft"
		elif self.docstatus == 2:
			status = "Cancelled"
		elif not self.is_new() and self.has_open_exception():
			status = "Difference"
		elif self.india_payment_entry:
			status = "Received"
		else:
			status = "Sent"
		if save:
			self.db_set("status", status)
		else:
			self.status = status


def tolerance() -> float:
	return flt(get_setting("amount_tolerance")) or 1.0
