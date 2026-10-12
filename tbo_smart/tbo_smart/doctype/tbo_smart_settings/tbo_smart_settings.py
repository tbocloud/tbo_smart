# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from tbo_smart.api.mobile.auth import GUID, parse_tenant_ids
from tbo_smart.finance.access import grant_crm_read


class TBOSmartSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enable_finance_workflow: DF.Check
		enable_microsoft_sso: DF.Check
		enable_mobile_push: DF.Check
		finance_bde_can_see_amounts: DF.Check
		finance_crm_access: DF.Check
		finance_crm_roles: DF.SmallText | None
		microsoft_mobile_client_id: DF.Data | None
		microsoft_tenant_id: DF.SmallText | None
		oauth_client: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.validate_microsoft_tenant_ids()
		self.validate_finance_roles()

	def on_update(self):
		self.grant_finance_crm_read()

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

	def validate_finance_roles(self):
		"""One existing Role per line; blank lines dropped."""
		roles = [line.strip() for line in (self.finance_crm_roles or "").splitlines() if line.strip()]
		missing = [role for role in roles if not frappe.db.exists("Role", role)]
		if missing:
			frappe.throw(_("These roles do not exist: {0}").format(", ".join(missing)))
		self.finance_crm_roles = "\n".join(roles) or None

	def grant_finance_crm_read(self):
		grant_crm_read()
