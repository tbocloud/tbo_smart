# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from tbo_smart.lead_pipeline.proposals import Proposal


class TBOProposal(Proposal, Document):
	"""A proposal to a lead: ordered blocks, its pricing quotation and the client's web link
	(docs/proposals.md). The logic lives in tbo_smart.lead_pipeline.proposals.Proposal."""
