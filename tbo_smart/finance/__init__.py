"""CRM finance workflow (docs/crm-finance-workflow.md).

Every hook and API in this package checks its phase's switch in TBO Smart Settings first, so
deploying the code changes nothing until management turns a phase on. Custom fields and the
one-off backfill are the only things that run regardless.
"""

import frappe
from frappe.utils import cint

SETTINGS = "TBO Smart Settings"
DEFAULT_CRM_ROLES = ("Accounts User", "Accounts Manager")


def crm_installed() -> bool:
	"""The finance workflow hangs off CRM Deal, so it exists only on sites with the CRM app.
	tbo_smart is also installed on sites without it (e.g. tbo-smart.tbocloud.in)."""
	return "crm" in frappe.get_installed_apps()


def finance_enabled() -> bool:
	"""Phase 1 master switch: TBO Smart Settings → Finance workflow (Phase 1). Always off on
	a site without the CRM app."""
	return crm_installed() and bool(cint(frappe.db.get_single_value(SETTINGS, "enable_finance_workflow")))


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
	"""Roles that may open the CRM read-only for finance work, from settings (one per line)."""
	value = get_setting("finance_crm_roles")
	roles = [line.strip() for line in (value or "").splitlines() if line.strip()]
	return roles or list(DEFAULT_CRM_ROLES)


def get_default_company() -> str | None:
	"""ERPNext CRM Settings → ERPNext company, when it names a Company on this site (Q2: one
	invoicing company until management says otherwise)."""
	company = frappe.db.get_single_value("ERPNext CRM Settings", "erpnext_company")
	if company and frappe.db.exists("Company", company):
		return company
	return None


def publish_deal_update(deals) -> None:
	"""Tell open CRM deal pages that their finance data changed (after the commit)."""
	for deal in {deal for deal in deals if deal}:
		frappe.publish_realtime(
			"tbo_deal_finance_update",
			{"crm_deal": deal},
			doctype="CRM Deal",
			docname=deal,
			after_commit=True,
		)
