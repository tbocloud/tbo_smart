# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOProposalImage(Document):
	"""An image in the proposal library, tagged so a proposal picks a fitting one (docs/proposals.md §2.1)."""
