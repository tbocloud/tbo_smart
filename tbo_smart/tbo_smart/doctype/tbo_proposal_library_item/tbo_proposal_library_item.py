# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TBOProposalLibraryItem(Document):
	"""A reusable piece of proposal text: an assumption, a risk, a role, a phase, an AMC plan…
	(docs/proposals.md §3.3)."""

	def validate(self):
		self.check_payment_terms()
		self.check_timeline()

	def check_payment_terms(self):
		"""A payment terms preset is "milestone | share" lines that add up to 100%."""
		if self.library != "Payment Terms":
			return
		from tbo_smart.lead_pipeline.proposal_builder import milestone_rows

		rows = milestone_rows(self.body)
		if not rows:
			frappe.throw(_('Write each milestone as "Milestone | 30", one a line.'))
		if round(sum(share for _label, share in rows), 2) != 100:
			frappe.throw(
				_("The milestones add up to {0}%, not 100%.").format(f"{sum(s for _l, s in rows):g}")
			)

	def check_timeline(self):
		"""A timeline template is "phase | days" lines that add up to its days."""
		if self.library != "Timeline":
			return
		from tbo_smart.lead_pipeline.proposal_builder import milestone_rows

		rows = milestone_rows(self.body)
		if not rows or not self.weeks:
			frappe.throw(_('Give the timeline its days and its phases, as "Phase | 10", one a line.'))
		total = sum(days for _phase, days in rows)
		if round(total, 2) != round(self.weeks, 2):
			frappe.throw(_("The phases add up to {0} days, not {1}.").format(f"{total:g}", f"{self.weeks:g}"))
