# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from tbo_smart.sales.call_batches.settings import validate_slots
from tbo_smart.sales.products import validate_products


class TBOLeadDepartment(Document):
	def validate(self):
		self.check_people_are_listed_once()
		validate_slots(self)
		validate_products(self)
		self.check_campaign_product()

	def check_people_are_listed_once(self):
		for table, label in (("heads", _("Heads")), ("viewers", _("Viewers")), ("members", _("Members"))):
			users = [row.user for row in self.get(table)]
			repeated = sorted({user for user in users if users.count(user) > 1})
			if repeated:
				frappe.throw(_("{0} lists {1} more than once.").format(label, ", ".join(repeated)))

	def check_campaign_product(self):
		"""The product the batches call for must be one of the department's active products."""
		wanted = (self.campaign_product or "").strip()
		if wanted and not any(
			row.is_active and row.product_name.lower() == wanted.lower() for row in self.products
		):
			frappe.throw(_("{0} is not an active product of {1}.").format(wanted, self.name))
