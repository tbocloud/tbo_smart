import frappe
from frappe import _
from frappe.model.document import Document

from tbo_smart.lead_pipeline import commission


class TBOCommissionPlan(Document):
	def validate(self):
		commission.validate_scheme(self.slabs, self.above_target_rate, self.monthly_target)
		self.check_conditions_are_unique()

	def check_conditions_are_unique(self):
		if not self.is_active:
			return
		mine = (self.department, self.lead_type, self.project_type, self.user)
		for other in frappe.get_all(
			"TBO Commission Plan",
			filters={"is_active": 1, "name": ("!=", self.name)},
			fields=["name", "department", "lead_type", "project_type", "user"],
		):
			if (other.department, other.lead_type, other.project_type, other.user) == mine:
				frappe.throw(_("{0} already applies to exactly the same cases.").format(other.name))
