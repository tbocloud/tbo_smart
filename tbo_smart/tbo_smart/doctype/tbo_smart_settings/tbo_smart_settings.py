# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from tbo_smart.ai import reset_provider_state
from tbo_smart.api.mobile.auth import GUID, parse_tenant_ids
from tbo_smart.finance.access import sync_crm_read
from tbo_smart.smart_leads.notify import teams_url_problem

LADDER_FIELDS = ("lead_ladder_l1_days", "lead_ladder_l2_days", "lead_ladder_l3_days")


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
		self.validate_ai_urls()
		self.validate_teams_url()
		self.validate_smart_lead_numbers()
		self.validate_pipeline_numbers()
		self.validate_segment_rates()
		self.validate_telephony()

	def on_update(self):
		self.grant_finance_crm_read()
		self.forget_ai_failures()

	def validate_ai_urls(self):
		"""Keys go to these URLs, so only https; the lead page URL goes into emails and Teams."""
		for fieldname in ("openai_base_url", "anthropic_base_url", "lead_page_url"):
			value = (self.get(fieldname) or "").strip().rstrip("/")
			if value and not value.startswith("https://"):
				frappe.throw(_("{0}: use an https:// URL.").format(_(self.meta.get_label(fieldname))))
			self.set(fieldname, value or None)

	def validate_teams_url(self):
		"""A pasted Teams message link or a cut-off workflow URL is refused here, not at 09:30."""
		raw = self.teams_dm_workflow_url or ""
		if not raw or self.is_dummy_password(raw):
			return
		self.teams_dm_workflow_url = raw.strip()
		problem = teams_url_problem(self.teams_dm_workflow_url)
		if problem:
			frappe.throw(_("Teams DM workflow URL: {0}").format(problem))

	def validate_smart_lead_numbers(self):
		"""Blank numbers fall back to their defaults in the code; set ones must make sense."""
		if self.call_today_size and not 5 <= cint(self.call_today_size) <= 30:
			frappe.throw(_("Leads in Call today must be between 5 and 30."))
		ladder = [
			cint(self.get(fieldname)) or default
			for fieldname, default in zip(LADDER_FIELDS, (2, 4, 7), strict=True)
		]
		if ladder != sorted(set(ladder)):
			frappe.throw(_("The escalation days must go up: BDM, then sales head, then managers."))
		for fieldname in (
			"lead_stale_days",
			"triage_daily_capacity",
			"triage_max_leads",
			"ai_daily_lead_limit",
		):
			if cint(self.get(fieldname)) < 0:
				frappe.throw(_("{0} can't be negative.").format(_(self.meta.get_label(fieldname))))
		if cint(self.triage_max_leads) > 1000:
			frappe.throw(_("The triage takes at most 1,000 leads per run."))

	def validate_pipeline_numbers(self):
		if self.meeting_reminder_minutes and not 15 <= cint(self.meeting_reminder_minutes) <= 1440:
			frappe.throw(_("The meeting reminder must be between 15 minutes and a day before."))
		for fieldname in ("quotation_validity_days", "quotation_follow_up_days", "proposal_validity_days"):
			if cint(self.get(fieldname)) < 0:
				frappe.throw(_("{0} can't be negative.").format(_(self.meta.get_label(fieldname))))
		self.quotation_preset_roles = self.clean_roles(self.quotation_preset_roles)

	def validate_segment_rates(self):
		"""One row per segment, an enabled selling price list, a band from 0 to 100%."""
		seen = set()
		for row in self.get("segment_rates") or []:
			if row.segment in seen:
				frappe.throw(_("Segment rate card: {0} is there twice.").format(_(row.segment)))
			seen.add(row.segment)
			if not 0 <= flt(row.max_discount) <= 100:
				frappe.throw(
					_("Segment rate card: the {0} band must be from 0 to 100%.").format(_(row.segment))
				)
			if row.price_list and not frappe.db.exists(
				"Price List", {"name": row.price_list, "selling": 1, "enabled": 1}
			):
				frappe.throw(
					_("Segment rate card: {0} is not an enabled selling price list.").format(row.price_list)
				)

	def validate_telephony(self):
		"""One mapping row per person, each with a MyOperator user ID or a mobile to ring."""
		seen = set()
		for row in self.get("telephony_agents") or []:
			if row.user in seen:
				frappe.throw(_("MyOperator users: {0} is there twice.").format(row.user))
			seen.add(row.user)
			if not ((row.myoperator_user_id or "").strip() or (row.mobile or "").strip()):
				frappe.throw(
					_("MyOperator users: give {0} a MyOperator user ID or a mobile.").format(row.user)
				)
		for fieldname in ("telephony_recording_days", "call_summary_min_seconds"):
			if cint(self.get(fieldname)) < 0:
				frappe.throw(_("{0} can't be negative.").format(_(self.meta.get_label(fieldname))))
		if flt(self.transcription_price_per_minute) < 0:
			frappe.throw(_("The transcription price can't be negative."))

	def forget_ai_failures(self):
		"""A fixed key or model is tried at once, not after the 30-minute pause."""
		reset_provider_state()

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
		"""Add the finance roles' deal read rows, or remove the ones this setting added."""
		granted = sync_crm_read(frappe.parse_json(self.finance_crm_granted or "[]"))
		value = frappe.as_json(granted) if granted else None
		if value != (self.finance_crm_granted or None):
			self.db_set("finance_crm_granted", value)

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
