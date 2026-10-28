# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TBOLeadDepartment(Document):
	def validate(self):
		self.check_people_are_listed_once()

	def check_people_are_listed_once(self):
		for table, label in (("heads", _("Heads")), ("members", _("Members"))):
			users = [row.user for row in self.get(table)]
			repeated = sorted({user for user in users if users.count(user) > 1})
			if repeated:
				frappe.throw(_("{0} lists {1} more than once.").format(label, ", ".join(repeated)))
