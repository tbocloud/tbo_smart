# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import NestedSet


class TBOSalesHierarchy(NestedSet):
	nsm_parent_field = "reports_to"

	def validate(self):
		self.make_parent_a_group()

	def make_parent_a_group(self):
		"""A node someone reports to becomes a group, so the tree accepts the child."""
		if self.reports_to and not frappe.db.get_value(self.doctype, self.reports_to, "is_group"):
			frappe.db.set_value(self.doctype, self.reports_to, "is_group", 1)


def on_doctype_update():
	frappe.db.add_index("TBO Sales Hierarchy", ["lft", "rgt"])
