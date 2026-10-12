# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOQuotationAdjustmentStep(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action: DF.Literal[
			"Requested", "Applied", "Approved", "Rejected", "Forwarded", "Overridden", "Voided"
		]
		at: DF.Datetime | None
		by_user: DF.Link | None
		level: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		percent: DF.Percent
		remarks: DF.SmallText | None
	# end: auto-generated types

	pass
