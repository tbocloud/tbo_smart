import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from tbo_smart.lead_pipeline import commission


class TBOProjectCommission(Document):
	def validate(self):
		self.check_earners()
		if self.use_override:
			commission.validate_scheme(
				self.override_slabs, self.override_above_target_rate, self.override_target
			)

	def check_earners(self):
		users = [row.user for row in self.earners]
		if len(set(users)) != len(users):
			frappe.throw(_("A user is listed twice."))
		if users and abs(sum(flt(row.share_percent) for row in self.earners) - 100) > 0.005:
			frappe.throw(_("The earners' shares must add up to 100%."))
		if any(flt(row.share_percent) <= 0 for row in self.earners):
			frappe.throw(_("Every share must be above 0%."))
