"""validate hooks on Sales Order, Sales Invoice and Payment Entry (finance workflow Phase 1).

- Deal link (G1): fill `opportunity` from the document chain when it is missing (Sales Order,
  Sales Invoice); on a Payment Entry it always follows the references.
- Pending-approval guard: a Sales Order or Sales Invoice whose source quotation still waits
  for a discount/commission approval, or lost its approval, is refused. The state comes from
  tbo_smart.quotation_control, the discount-control feature.
"""

import frappe
from frappe import _

from tbo_smart.finance import finance_enabled
from tbo_smart.finance.deal_links import (
	resolve_payment_entry,
	resolve_sales_invoice,
	resolve_sales_order,
	source_quotations,
)
from tbo_smart.quotation_control import get_unapproved

RESOLVERS = {
	"Sales Order": resolve_sales_order,
	"Sales Invoice": resolve_sales_invoice,
	"Payment Entry": resolve_payment_entry,
}


class QuotationApprovalPending(frappe.ValidationError):
	"""A source quotation still needs a discount or commission approval."""


class FinanceDocument:
	def __init__(self, doc):
		self.doc = doc

	def validate(self):
		if not finance_enabled():
			return
		self.set_deal()
		if self.doc.doctype in ("Sales Order", "Sales Invoice"):
			self.block_pending_approval()

	def set_deal(self):
		if self.doc.doctype == "Payment Entry":
			# with references the deal always follows them; without any, a typed deal stays
			if has_order_or_invoice_reference(self.doc):
				self.doc.opportunity = RESOLVERS["Payment Entry"](self.doc)
		elif not self.doc.get("opportunity"):
			self.doc.opportunity = RESOLVERS[self.doc.doctype](self.doc)

	def block_pending_approval(self):
		if self.doc.get("is_return"):
			return
		reasons = [
			reason for quotation in source_quotations(self.doc) for reason in get_unapproved(quotation)
		]
		if not reasons:
			return
		frappe.throw(
			"<br>".join(reasons)
			+ "<br>"
			+ _("A {0} cannot be made from it until the approval is complete.").format(_(self.doc.doctype)),
			QuotationApprovalPending,
			title=_("Quotation approval pending"),
		)


def has_order_or_invoice_reference(doc) -> bool:
	return any(
		row.reference_doctype in ("Sales Invoice", "Sales Order") for row in doc.get("references") or []
	)


def validate(doc, method=None):
	FinanceDocument(doc).validate()
