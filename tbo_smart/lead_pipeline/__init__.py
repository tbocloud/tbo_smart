"""TBO's lead pipeline on the CRM Lead (docs/lead-pipeline.md).

Everything happens on the lead: its statuses, its meetings with their minutes (MOM) and, in
later phases, its quotations. A CRM Deal is made only when the lead reaches Closed – Won, so the
deal-based finance workflow (docs/crm-finance-workflow.md) carries on from there.

The statuses and their one-off migration (`statuses.py`, `status_migration.py`) are in place on
every CRM site from the deploy on. Everything else is behind TBO Smart Settings → Lead pipeline,
off by default, and reads as off on a site without the CRM app.
"""

import frappe
from frappe.utils import cint

from tbo_smart.finance import crm_installed

SETTINGS = "TBO Smart Settings"


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def pipeline_enabled() -> bool:
	"""TBO Smart Settings → Lead pipeline: meetings, MOM, automatic statuses and the deal at
	Closed – Won."""
	return crm_installed() and bool(cint(setting("enable_lead_pipeline")))


def require_pipeline() -> None:
	if not pipeline_enabled():
		frappe.throw(frappe._("The lead pipeline is not switched on."), frappe.ValidationError)
