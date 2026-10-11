# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TBOMobileDevice(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		app_version: DF.Data | None
		expo_push_token: DF.Data
		last_seen: DF.Datetime | None
		platform: DF.Literal["android", "ios"]
		user: DF.Link
	# end: auto-generated types

	pass
