# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TBOQuotationPackage(Document):
	"""A preset of quotation lines: a service bundle with its default description, quantity,
	rate and project duration (docs/lead-pipeline.md §9)."""

	def validate(self):
		self.validate_lines()

	def validate_lines(self):
		for row in self.items:
			if (row.qty or 0) <= 0:
				frappe.throw(_("Row {0}: quantity must be more than 0.").format(row.idx))
			if (row.rate or 0) < 0:
				frappe.throw(_("Row {0}: rate can't be negative.").format(row.idx))
