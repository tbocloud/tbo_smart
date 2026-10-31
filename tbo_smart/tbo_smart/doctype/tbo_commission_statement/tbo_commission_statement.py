import frappe
from frappe import _
from frappe.model.document import Document

FROZEN = ("user", "month", "plans", "collected", "target", "achievement", "commission")


class TBOCommissionStatement(Document):
	def validate(self):
		self.check_one_per_user_and_month()
		self.check_frozen()

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw(_("An approved statement cannot be deleted."))

	def check_one_per_user_and_month(self):
		if frappe.db.exists(
			"TBO Commission Statement",
			{"user": self.user, "month": self.month, "name": ("!=", self.name)},
		):
			frappe.throw(_("{0} already has a statement for {1}.").format(self.user, self.month))

	def check_frozen(self):
		before = self.get_doc_before_save()
		if not before or before.status == "Draft":
			return
		if any(self.get(field) != before.get(field) for field in FROZEN) or len(self.payments) != len(
			before.payments
		):
			frappe.throw(_("An approved statement is never rewritten."))
