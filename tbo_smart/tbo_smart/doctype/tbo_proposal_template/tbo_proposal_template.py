# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from tbo_smart.lead_pipeline.proposals import ProposalTemplate


class TBOProposalTemplate(ProposalTemplate, Document):
	"""A proposal's starting blocks and packages for one kind of solution (docs/proposals.md §3).
	The logic lives in tbo_smart.lead_pipeline.proposals.ProposalTemplate."""
