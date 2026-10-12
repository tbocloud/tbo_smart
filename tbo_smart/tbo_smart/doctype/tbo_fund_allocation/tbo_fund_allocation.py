# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

"""India's share of one KSA receipt (docs/crm-finance-workflow.md §3.3).

Created and submitted only by tbo_smart.finance.allocation from a Payment Entry; nobody types
or edits one. Settlement comes from submitted TBO Fund Transfers.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import flt

TOLERANCE = 0.005


class TBOFundAllocation(Document):
	def validate(self):
		self.check_origin()
		self.set_totals()

	def on_submit(self):
		self.update_settlement()

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def before_cancel(self):
		self.check_origin()
		self.check_unsettled()

	# ---------- rules ----------

	def check_origin(self):
		"""Only the allocation engine creates or cancels allocations."""
		if not self.flags.from_engine:
			frappe.throw(
				_(
					"Allocations are made from receipts automatically and cannot be created or changed by hand."
				)
			)

	def check_unsettled(self):
		if flt(self.settled_amount):
			frappe.throw(
				_("Allocation {0} is already settled by a transfer; reverse it instead.").format(self.name)
			)

	def set_totals(self):
		self.gross_amount = sum(flt(line.gross_amount) for line in self.lines)
		self.tax_amount = sum(flt(line.tax_amount) for line in self.lines)
		self.net_amount = sum(flt(line.net_amount) for line in self.lines)
		self.share_amount = sum(flt(line.share_amount) for line in self.lines)
		self.settled_amount = 0
		self.outstanding_amount = self.share_amount

	# ---------- settlement ----------

	def update_settlement(self):
		"""Settled = what submitted transfers have allocated to it; status follows."""
		Row = frappe.qb.DocType("TBO Fund Transfer Allocation")
		Transfer = frappe.qb.DocType("TBO Fund Transfer")
		settled = (
			frappe.qb.from_(Row)
			.join(Transfer)
			.on(Row.parent == Transfer.name)
			.select(Sum(Row.allocated_amount))
			.where((Transfer.docstatus == 1) & (Row.fund_allocation == self.name))
		).run()[0][0]
		settled = flt(settled, 2)
		outstanding = flt(flt(self.share_amount) - settled, 2)
		if self.status == "Reversed" and self.docstatus == 1:
			status = "Reversed"
		elif abs(outstanding) <= TOLERANCE:
			status = "Settled"
		elif abs(settled) <= TOLERANCE:
			status = "Open"
		else:
			status = "Partly Settled"
		self.db_set({"settled_amount": settled, "outstanding_amount": outstanding, "status": status})
