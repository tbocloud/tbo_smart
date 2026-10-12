"""CRM finance workflow (docs/crm-finance-workflow.md).

Every hook and API in this package checks its phase's switch in TBO Smart Settings first, so
deploying the code changes nothing until management turns a phase on. Custom fields and the
one-off backfill are the only things that run regardless.
"""

import frappe
from frappe.utils import cint

SETTINGS = "TBO Smart Settings"
DEFAULT_CRM_ROLES = ("Accounts User", "Accounts Manager")


def finance_enabled() -> bool:
	"""Phase 1 master switch: TBO Smart Settings → Finance workflow (Phase 1)."""
	return bool(cint(frappe.db.get_single_value(SETTINGS, "enable_finance_workflow")))


def receivables_enabled() -> bool:
	"""Phase 2 switch (Receivables and reminders); needs Phase 1 on too."""
	return finance_enabled() and bool(
		cint(frappe.db.get_single_value(SETTINGS, "enable_finance_receivables"))
	)


def exceptions_enabled() -> bool:
	"""Whether any phase that raises TBO Finance Exceptions is on; the doctype's permission
	hooks give salespeople nothing while none is."""
	return receivables_enabled()


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
