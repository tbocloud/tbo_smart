# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from tbo_smart.api.mobile.auth import GUID, parse_tenant_ids


class TBOSmartSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enable_microsoft_sso: DF.Check
		enable_mobile_push: DF.Check
		microsoft_mobile_client_id: DF.Data | None
		microsoft_tenant_id: DF.SmallText | None
		oauth_client: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.validate_microsoft_tenant_ids()

	def validate_microsoft_tenant_ids(self):
		"""Only real tenant GUIDs: "common" or "organizations" would let any Microsoft account in."""
		tenants = parse_tenant_ids(self.microsoft_tenant_id)
		invalid = [tenant for tenant in tenants if not GUID.match(tenant)]
		if invalid:
			frappe.throw(
				_("Microsoft tenant ID must be the directory (tenant) GUID, not {0}.").format(
					", ".join(invalid)
				)
			)
		self.microsoft_tenant_id = ", ".join(tenants) or None
