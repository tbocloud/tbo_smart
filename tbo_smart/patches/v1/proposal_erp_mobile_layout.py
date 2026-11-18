"""The ERPNext + mobile proposal laid out as the owner's model proposal of 2026-11-18 on existing
sites (docs/proposals.md, "The ERPNext + mobile layout"): its library rows, and its template where the editors haven't
changed it."""

from tbo_smart.lead_pipeline.setup import update_erp_mobile_preset


def execute():
	update_erp_mobile_preset()
