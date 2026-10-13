# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from tbo_smart.api.mobile.auth import GUID, parse_tenant_ids
from tbo_smart.finance.access import sync_crm_read
from tbo_smart.finance.setup import create_collections_view


class TBOSmartSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from tbo_smart.tbo_smart.doctype.tbo_checkin_location.tbo_checkin_location import TBOCheckinLocation

		accounts_lead: DF.Link | None
		allocation_accounting_option: DF.Literal[
			"Memo only", "Monthly inter-company invoice", "Journal Entry per receipt"
		]
		allocation_base_after_bank_charges: DF.Check
		allocation_percent: DF.Percent
		allocation_start_date: DF.Date | None
		amount_tolerance: DF.Currency
		checkin_anywhere_roles: DF.SmallText | None
		checkin_locations: DF.TableMultiSelect[TBOCheckinLocation]
		checkin_max_accuracy: DF.Int
		default_vat_rate: DF.Percent
		enable_fund_allocation: DF.Check
		enable_reconciliation: DF.Check
		enforce_checkin_radius: DF.Check
		enable_finance_dashboard: DF.Check
		enable_finance_receivables: DF.Check
		enable_finance_workflow: DF.Check
		escalation_finance_manager: DF.Link | None
		escalation_sales_head: DF.Link | None
		enable_microsoft_sso: DF.Check
		enable_mobile_push: DF.Check
		finance_bde_can_see_amounts: DF.Check
		finance_crm_access: DF.Check
		finance_crm_granted: DF.SmallText | None
		finance_crm_roles: DF.SmallText | None
		invoicing_company: DF.Link | None
		management_digest_recipients: DF.SmallText | None
		microsoft_mobile_client_id: DF.Data | None
		microsoft_tenant_id: DF.SmallText | None
		oauth_client: DF.Link | None
		partner_company: DF.Link | None
		reminder_sender: DF.Link | None
		reverse_geocode_email: DF.Data | None
		reverse_geocode_url: DF.Data | None
		second_approval_amount: DF.Currency
		send_client_reminders: DF.Check
		transfer_pending_days: DF.Int
		unmatched_alert_days: DF.Int
		weekly_client_reminders: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_microsoft_tenant_ids()
		self.validate_finance_roles()
		self.validate_allocation()
		self.validate_checkin_roles()
		self.validate_checkin_locations()

	def on_update(self):
		self.grant_finance_crm_read()
		self.create_collections_view()

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
		self.finance_crm_roles = self.clean_roles(self.finance_crm_roles)

	def validate_checkin_roles(self):
		self.checkin_anywhere_roles = self.clean_roles(self.checkin_anywhere_roles)

	@staticmethod
	def clean_roles(text: str | None) -> str | None:
		"""One existing Role per line; blank lines dropped."""
		roles = [line.strip() for line in (text or "").splitlines() if line.strip()]
		missing = [role for role in roles if not frappe.db.exists("Role", role)]
		if missing:
			frappe.throw(_("These roles do not exist: {0}").format(", ".join(missing)))
		return "\n".join(roles) or None

	def validate_checkin_locations(self):
		"""A listed location must have a centre and a radius, or it would silently not count."""
		names = [row.shift_location for row in self.checkin_locations]
		if not names:
			return
		unusable = [
			row.name
			for row in frappe.get_all(
				"Shift Location",
				filters={"name": ("in", names)},
				fields=["name", "latitude", "longitude", "checkin_radius"],
			)
			if not (row.latitude or row.longitude) or (row.checkin_radius or 0) <= 0
		]
		if unusable:
			frappe.throw(
				_("Set the coordinates and a check-in radius on these Shift Locations first: {0}").format(
					", ".join(unusable)
				)
			)

	def grant_finance_crm_read(self):
		"""Add the finance roles' CRM read rows, or remove the ones this setting added."""
		granted = sync_crm_read(frappe.parse_json(self.finance_crm_granted or "[]"))
		value = frappe.as_json(granted) if granted else None
		if value != (self.finance_crm_granted or None):
			self.db_set("finance_crm_granted", value)

	def create_collections_view(self):
		if self.enable_finance_workflow and self.enable_finance_receivables:
			create_collections_view()

	def validate_allocation(self):
		if not self.enable_fund_allocation:
			return
		if not (self.invoicing_company and self.partner_company):
			frappe.throw(
				_("Set the invoicing company and the partner company before switching allocation on.")
			)
		if self.invoicing_company == self.partner_company:
			frappe.throw(_("The invoicing and partner companies must differ."))
		if not 0 < (self.allocation_percent or 0) <= 100:
			frappe.throw(_("Partner share % must be between 0 and 100."))
		if self.allocation_accounting_option and self.allocation_accounting_option != "Memo only":
			frappe.msgprint(
				_(
					"Only Memo only is built so far: allocations and transfers still post nothing to the accounts."
				),
				indicator="orange",
				alert=True,
			)
