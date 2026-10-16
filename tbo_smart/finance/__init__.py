"""The finance workflow of a deal (an Opportunity) after the sale (docs/crm-finance-workflow.md).

Every hook and API in this package checks its phase's switch in TBO Smart Settings first, so
deploying the code changes nothing until management turns a phase on. Custom fields are the
only thing that runs regardless.
"""

import frappe
from frappe.utils import cint

from tbo_smart.sales import erpnext_installed

SETTINGS = "TBO Smart Settings"
DEFAULT_CRM_ROLES = ("Accounts User", "Accounts Manager")


def finance_enabled() -> bool:
	"""Phase 1 master switch: TBO Smart Settings → Finance workflow (Phase 1). Always off on
	a site without ERPNext (the workflow hangs off its Opportunity)."""
	return erpnext_installed() and bool(cint(frappe.db.get_single_value(SETTINGS, "enable_finance_workflow")))


def receivables_enabled() -> bool:
	"""Phase 2 switch (Receivables and reminders); needs Phase 1 on too."""
	return finance_enabled() and bool(
		cint(frappe.db.get_single_value(SETTINGS, "enable_finance_receivables"))
	)


def allocation_enabled() -> bool:
	"""Phase 3 switch (Allocation and transfers); needs Phase 1 on too."""
	return finance_enabled() and bool(cint(frappe.db.get_single_value(SETTINGS, "enable_fund_allocation")))


def reconciliation_enabled() -> bool:
	"""Phase 4 switch (Reconciliation and approvals); needs Phase 1 on too."""
	return finance_enabled() and bool(cint(frappe.db.get_single_value(SETTINGS, "enable_reconciliation")))


def exceptions_enabled() -> bool:
	"""Whether any phase that raises TBO Finance Exceptions is on; the doctype's permission
	hooks give salespeople nothing while none is."""
	return receivables_enabled() or allocation_enabled() or reconciliation_enabled()


def dashboard_enabled() -> bool:
	"""Phase 5 switch (Finance dashboard); needs Phase 1 on too."""
	return finance_enabled() and bool(cint(frappe.db.get_single_value(SETTINGS, "enable_finance_dashboard")))


def get_setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def get_crm_roles() -> list[str]:
	"""Roles that may read every deal for finance work, from settings (one per line)."""
	value = get_setting("finance_crm_roles")
	roles = [line.strip() for line in (value or "").splitlines() if line.strip()]
	return roles or list(DEFAULT_CRM_ROLES)


def get_default_company() -> str | None:
	"""TBO Smart Settings → Default sales company (the cutover copies Frappe CRM's ERPNext
	company into it), else the site's default company (Q2: one invoicing company until
	management says otherwise)."""
	company = get_setting("sales_company") or frappe.defaults.get_global_default("company")
	if company and frappe.db.exists("Company", company):
		return company
	return None


def publish_deal_update(deals) -> None:
	"""Tell open deal pages that their finance data changed (after the commit)."""
	for deal in {deal for deal in deals if deal}:
		frappe.publish_realtime(
			"tbo_deal_finance_update",
			{"opportunity": deal},
			doctype="Opportunity",
			docname=deal,
			after_commit=True,
		)
