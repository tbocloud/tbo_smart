# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOSegmentRate(Document):
	"""One segment of the rate card in TBO Smart Settings: its price list and the discount a
	salesperson may give without approval (docs/lead-pipeline.md §9.8)."""
