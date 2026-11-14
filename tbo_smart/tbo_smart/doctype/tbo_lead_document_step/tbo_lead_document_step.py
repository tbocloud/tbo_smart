# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOLeadDocumentStep(Document):
	"""A step of a lead's document chain that no client document carries
	(lead_pipeline/document_steps.py): skipped or not used, or the project made straight from the
	lead with its UAT sign-off. Written only through that module."""

	pass
