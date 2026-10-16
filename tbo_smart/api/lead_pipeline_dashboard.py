"""The CRM's Pipeline page (docs/lead-pipeline.md §11). Needs TBO Smart Settings → Pipeline
dashboard; each person sees their own leads, a team lead their team, managers everyone."""

import frappe

from tbo_smart.lead_pipeline.dashboard import build_dashboard, dashboard_enabled


@frappe.whitelist()
def get_access() -> dict:
	return {"enabled": dashboard_enabled() and bool(frappe.has_permission("CRM Lead", "read"))}


@frappe.whitelist()
def get_pipeline_dashboard(
	from_date: str | None = None,
	to_date: str | None = None,
	bde: str | None = None,
	source: str | None = None,
	status: str | None = None,
) -> dict:
	"""Leads by status, meetings, follow-ups, won and lost, conversion, per BDE and quotations."""
	return build_dashboard(from_date, to_date, bde, source, status)
