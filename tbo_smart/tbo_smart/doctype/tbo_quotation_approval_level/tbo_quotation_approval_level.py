# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOQuotationApprovalLevel(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		approves_directly: DF.Check
		level: DF.Data
		max_commission: DF.Percent
		max_discount: DF.Percent
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		rank: DF.Int
		role: DF.Link
		unlimited: DF.Check
	# end: auto-generated types

	pass
