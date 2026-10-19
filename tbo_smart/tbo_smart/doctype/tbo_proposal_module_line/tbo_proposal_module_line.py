# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOProposalModuleLine(Document):
	"""A module included in a proposal, with its own price and effort (docs/proposals.md §3.2)."""
