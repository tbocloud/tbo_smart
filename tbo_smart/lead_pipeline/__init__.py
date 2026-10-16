"""TBO's lead pipeline on ERPNext's Lead (docs/lead-pipeline.md).

Everything happens on the lead: its stages (`tbo_stage`), its meetings with their minutes (MOM)
and its quotations. The deal (an Opportunity) is made only when the lead reaches Closed – Won,
so the deal-based finance workflow (docs/crm-finance-workflow.md) carries on from there.

The stages (`statuses.py`) are in place on every ERPNext site from the deploy on. Everything
else is behind TBO Smart Settings → Lead pipeline, off by default, and reads as off on a site
without ERPNext.
"""

import frappe
from frappe.utils import cint

from tbo_smart.sales import erpnext_installed

SETTINGS = "TBO Smart Settings"


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def pipeline_enabled() -> bool:
	"""TBO Smart Settings → Lead pipeline: meetings, MOM, automatic statuses and the deal at
	Closed – Won."""
	return erpnext_installed() and bool(cint(setting("enable_lead_pipeline")))


def quotations_enabled() -> bool:
	"""TBO Smart Settings → Quotations on the lead (needs the lead pipeline on)."""
	return pipeline_enabled() and bool(cint(setting("enable_lead_quotations")))


def require_pipeline() -> None:
	if not pipeline_enabled():
		frappe.throw(frappe._("The lead pipeline is not switched on."), frappe.ValidationError)


def require_quotations() -> None:
	if not quotations_enabled():
		frappe.throw(frappe._("Quotations on the lead are not switched on."), frappe.ValidationError)
